from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agent.llm import InvalidLLMJSONError, LLMClient, LLMConfigurationError, LLMRequestError
from app.agent.orchestrator import CatalogIntegrationUnavailable, Orchestrator
from app.db import apply_migrations, load_seed
from app.state.store import SessionNotFound, StateStore, VersionConflict

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
    text: str = Field(min_length=1, max_length=20000)
    expected_version: int = Field(ge=0)


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


@app.post("/api/sessions/{session_id}/messages")
async def send_message(session_id: UUID, body: MessageRequest, request: Request):
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
        if current.version != body.expected_version:
            raise HTTPException(409, detail={
                "code": "version_conflict", "message": "Session changed; reload its state",
                "expected_version": body.expected_version, "current_version": current.version,
            })
    except Exception:
        lock.release()
        raise

    async def event_stream():
        try:
            candidate, reply, trace = await request.app.state.orchestrator.prepare_turn(current, body.text)
            saved = store.save_turn(
                candidate, expected_version=body.expected_version, user_text=body.text,
                assistant_text=reply, trace_steps=trace,
            )
            for item in trace:
                yield _sse("step", item)
            # Emit only the state returned by the committed save operation.
            yield _sse("state", {"state": saved.model_dump(mode="json")})
            yield _sse("message", {"role": "assistant", "text": reply})
            yield _sse("done", {"ok": True, "version": saved.version})
        except VersionConflict as exc:
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
                code, message = "catalog_integration_missing", str(exc)
            else:
                log.exception("message orchestration failed")
                code, message = "agent_error", "The agent could not complete this request"
            yield _sse("error", {"code": code, "message": message})
            yield _sse("done", {"ok": False})
        finally:
            lock.release()

    return StreamingResponse(
        event_stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
