from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

from app.agent.llm import InvalidLLMJSONError, LLMClient, LLMConfigurationError, LLMRequestError
from app.agent.orchestrator import (
    CatalogIntegrationUnavailable, InvalidAgentCommand, OrchestrationLimitError, Orchestrator,
)
from app.db import apply_migrations, load_seed
from app.state.models import TripState
from app.state.store import RequestIdConflict, SessionNotFound, StateStore, VersionConflict

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    database_url = os.getenv("DATABASE_URL", "")
    app.state.store = None
    if database_url:
        # Fail fast: migration and seed errors must not be hidden.
        apply_migrations(database_url)
        seeded = load_seed(database_url)
        log.info("database initialized; seed entries processed=%d", seeded)
        app.state.store = StateStore(database_url)
    else:
        log.warning("DATABASE_URL is not configured; health is available, session endpoints are disabled")
    app.state.llm = LLMClient()
    app.state.orchestrator = Orchestrator(app.state.llm)
    app.state.session_locks = {}
    yield


app = FastAPI(title="Trip Agent", version="0.1.0", lifespan=lifespan)


class CreateSessionResponse(BaseModel):
    session_id: UUID
    version: int


class MessageRequest(BaseModel):
    request_id: UUID
    text: str = Field(min_length=1, max_length=20000)
    expected_version: int = Field(ge=0)


class CommandRequest(BaseModel):
    request_id: UUID
    type: str
    payload: dict[str, Any]
    expected_version: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_command(self):
        required = {
            "edit_requirement": {"key", "value"},
            "remove_requirement": {"key"},
            "remove_assumption": {"key"},
            "select_destination": {"destination_id"},
            "accept_alternative": {"alternative_id"},
            "select_bundle": {"bundle_id"},
            "request_checkout": set(),
        }
        if self.type not in required:
            raise ValueError(f"unsupported command type: {self.type}")
        absent = required[self.type] - self.payload.keys()
        if absent:
            raise ValueError(f"missing payload keys: {', '.join(sorted(absent))}")
        if self.type == "edit_requirement" and self.payload.get("kind", "hard") not in {"hard", "soft"}:
            raise ValueError("edited requirement kind must be hard or soft")
        return self


def get_store(request: Request) -> StateStore:
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(503, detail={"code": "database_unavailable", "message": "DATABASE_URL is not configured"})
    return store


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/sessions", response_model=CreateSessionResponse)
async def create_session(request: Request):
    state = get_store(request).create_session()
    return CreateSessionResponse(session_id=state.session_id, version=state.version)


@app.get("/api/sessions/{session_id}")
async def read_session(session_id: UUID, request: Request):
    store = get_store(request)
    try:
        session = store.get_session(session_id)
    except SessionNotFound:
        raise HTTPException(404, detail={"code": "session_not_found", "message": "Session does not exist"})
    return {"state": session["state"].model_dump(mode="json"), "messages": session["messages"]}


@app.get("/api/sessions/{session_id}/trace")
async def read_trace(session_id: UUID, request: Request):
    store = get_store(request)
    try:
        store.get_state(session_id)
    except SessionNotFound:
        raise HTTPException(404, detail={"code": "session_not_found", "message": "Session does not exist"})
    return {"session_id": str(session_id), "steps": store.get_trace(session_id)}


@app.get("/api/sessions/{session_id}/requests/{request_id}")
async def read_request_status(session_id: UUID, request_id: UUID, request: Request):
    """Recovery endpoint; completed SSE is not replayed, current state must be re-read."""
    store = get_store(request)
    try:
        store.get_state(session_id)
    except SessionNotFound:
        raise HTTPException(404, detail={"code": "session_not_found", "message": "Session does not exist"})
    record = store.get_request(session_id, request_id)
    if record is None:
        return {"request_id": str(request_id), "status": "not_found", "current_version": store.get_state(session_id).version}
    return {
        "request_id": str(request_id), "status": record["status"],
        "committed_version": record.get("committed_version"),
        "current_version": store.get_state(session_id).version,
        "error": record.get("error"), "updated_at": record.get("updated_at"),
        "replay_available": False,
    }


async def _session_event_stream(
    session_id: UUID,
    expected_version: int,
    request_id: UUID,
    fingerprint: str,
    request: Request,
    operation: Callable[[TripState], Awaitable[tuple[TripState, str, list[dict[str, Any]]]]],
):
    store = get_store(request)
    locks: dict[UUID, asyncio.Lock] = request.app.state.session_locks
    lock = locks.setdefault(session_id, asyncio.Lock())
    # Hold a per-session process lock from preflight through persistence. This is
    # why the supported deployment is one worker; SQL still enforces CAS at commit.
    await lock.acquire()
    try:
        try:
            current = store.get_state(session_id)
        except SessionNotFound:
            raise HTTPException(404, detail={"code": "session_not_found", "message": "Session does not exist"})
        try:
            prior = store.begin_request(request_id, session_id, fingerprint)
        except RequestIdConflict as exc:
            raise HTTPException(409, detail={"code": "request_id_conflict", "message": str(exc), "current_version": current.version})
        if prior["status"] == "completed":
            payload = {"request_id": str(request_id), "committed_version": prior.get("committed_version"),
                       "current_version": current.version, "reload": True}
            lock.release()
            async def completed_replay():
                yield _sse("replay", payload)
                yield _sse("done", {"ok": True, "version": current.version})
            return StreamingResponse(completed_replay(), media_type="text/event-stream")
        if prior["status"] == "failed":
            err = prior.get("error") or {"code": "request_failed", "message": "Request failed; reload current state and submit a new request."}
            lock.release()
            async def failed_replay():
                yield _sse("error", err)
                yield _sse("done", {"ok": False})
            return StreamingResponse(failed_replay(), media_type="text/event-stream")
        if prior["status"] == "processing":
            lock.release()
            raise HTTPException(409, detail={"code": "request_in_progress", "message": "This request is still running; check its status or reload the session.", "request_id": str(request_id), "current_version": current.version})
        if current.version != expected_version:
            store.fail_request(request_id, {"code": "version_conflict", "message": "Session changed; reload state and submit a new request."})
            raise HTTPException(409, detail={
                "code": "version_conflict", "message": "Session changed; reload its state",
                "expected_version": expected_version, "current_version": current.version,
            })
    except Exception:
        lock.release()
        raise

    async def run_and_save():
        try:
            candidate, reply, trace = await operation(current)
            saved_state = store.save_turn(
                candidate, expected_version=expected_version, user_text=getattr(request.state, "user_text", ""),
                assistant_text=reply, trace_steps=trace, request_id=request_id,
            )
            return saved_state, reply, trace
        except VersionConflict:
            store.fail_request(request_id, {"code": "version_conflict", "message": "Session changed; reload state and submit a new request."})
            raise
        except Exception:
            store.fail_request(request_id, {"code": "request_failed", "message": "The request failed; reload state before continuing."})
            raise
        finally:
            # Work begins as soon as the request is accepted, even if its client
            # disconnects before the ASGI server starts sending the SSE body.
            lock.release()

    task = asyncio.create_task(run_and_save())
    task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)

    async def event_stream():
        try:
            # Shield: after reservation, cancellation of the SSE response does
            # not cancel state computation or its atomic commit.
            saved, reply, trace = await asyncio.shield(task)
            for item in trace:
                yield _sse("step", item)
            # Emit only the state returned by the committed save operation.
            yield _sse("state", {"state": saved.model_dump(mode="json")})
            yield _sse("message", {"role": "assistant", "text": reply})
            yield _sse("done", {"ok": True, "version": saved.version})
        except VersionConflict as exc:
            store.fail_request(request_id, {"code": "version_conflict", "message": "Session changed; reload state and submit a new request."})
            yield _sse("error", {"code": "version_conflict", "message": str(exc), "current_version": exc.actual})
            yield _sse("done", {"ok": False})
        except Exception as exc:
            if isinstance(exc, LLMConfigurationError):
                code, message = "llm_not_configured", str(exc)
            elif isinstance(exc, InvalidLLMJSONError):
                code, message = "invalid_llm_response", str(exc)
            elif isinstance(exc, LLMRequestError):
                code, message = "llm_request_failed", str(exc)
            elif isinstance(exc, CatalogIntegrationUnavailable):
                code, message = "a_tool_unavailable", str(exc)
            elif isinstance(exc, OrchestrationLimitError):
                code, message = "agent_step_limit", str(exc)
            elif isinstance(exc, InvalidAgentCommand):
                code, message = "invalid_command", str(exc)
            else:
                log.exception("message orchestration failed")
                code, message = "agent_error", "The agent could not complete this request"
            store.fail_request(request_id, {"code": code, "message": message})
            yield _sse("error", {"code": code, "message": message})
            yield _sse("done", {"ok": False})

    return StreamingResponse(
        event_stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/sessions/{session_id}/messages")
async def send_message(session_id: UUID, body: MessageRequest, request: Request):
    request.state.user_text = body.text
    fingerprint = json.dumps(body.model_dump(mode="json", exclude={"request_id"}), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return await _session_event_stream(
        session_id, body.expected_version, body.request_id, fingerprint, request,
        lambda state: request.app.state.orchestrator.prepare_turn(state, body.text),
    )


@app.post("/api/sessions/{session_id}/commands")
async def send_command(session_id: UUID, body: CommandRequest, request: Request):
    request.state.user_text = f"[command:{body.type}]"
    fingerprint = json.dumps(body.model_dump(mode="json", exclude={"request_id"}), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return await _session_event_stream(
        session_id, body.expected_version, body.request_id, fingerprint, request,
        lambda state: request.app.state.orchestrator.prepare_command(state, body.type, body.payload),
    )


# ---- трек C: корзина/оплата и раздача React-сборки ----
from pathlib import Path  # noqa: E402

from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from app.cart.router import router as cart_router  # noqa: E402

app.include_router(cart_router)

WEB_DIR = Path(os.getenv("WEB_DIR", Path(__file__).resolve().parents[1] / "web" / "dist"))
if (WEB_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
async def spa(path: str):
    """SPA-фолбэк: любые не-API пути отдают index.html (роутинг на клиенте)."""
    if path.startswith("api/"):
        raise HTTPException(404, detail={"code": "not_found", "message": "Not found"})
    f = (WEB_DIR / path).resolve()
    if path and f.is_file() and WEB_DIR.resolve() in f.parents:
        return FileResponse(f)
    index = WEB_DIR / "index.html"
    if not index.is_file():
        raise HTTPException(404, detail={"code": "web_not_built", "message": "Frontend is not built (web/dist)"})
    return FileResponse(index)
