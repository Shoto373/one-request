import asyncio
from datetime import datetime, timezone
from threading import RLock
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio

from app.agent.llm import InvalidLLMJSONError, LLMClient
from app.agent.orchestrator import Orchestrator, ParsedRequest
from app.catalog.models import Offer
from app.main import app
from app.state.models import Bundle, Requirement, TripState
from app.state.store import SessionNotFound, VersionConflict


class MemoryStore:
    """Test-only CAS store; production state is PostgreSQL."""
    def __init__(self):
        self.states, self.messages, self.trace = {}, {}, {}
        self.requests = {}
        self.guard = RLock()

    def create_session(self):
        state = TripState(session_id=uuid4())
        with self.guard:
            self.states[state.session_id] = state
            self.messages[state.session_id] = []
            self.trace[state.session_id] = []
        return state

    def get_state(self, session_id):
        key = UUID(str(session_id))
        with self.guard:
            if key not in self.states:
                raise SessionNotFound(str(key))
            return self.states[key].model_copy(deep=True)

    def get_session(self, session_id):
        state = self.get_state(session_id)
        with self.guard:
            return {"state": state, "messages": list(self.messages[state.session_id])}

    def get_trace(self, session_id):
        with self.guard:
            return list(self.trace[UUID(str(session_id))])

    def begin_request(self, request_id, session_id, fingerprint):
        key = UUID(str(request_id))
        with self.guard:
            old = self.requests.get(key)
            if old is None:
                self.requests[key] = {"session_id": UUID(str(session_id)), "fingerprint": fingerprint, "status": "processing"}
                return {"status": "new"}
            if old["session_id"] != UUID(str(session_id)) or old["fingerprint"] != fingerprint:
                from app.state.store import RequestIdConflict
                raise RequestIdConflict("request id reused")
            return dict(old)

    def fail_request(self, request_id, error):
        with self.guard:
            self.requests[UUID(str(request_id))].update(status="failed", error=error)

    def get_request(self, session_id, request_id):
        with self.guard:
            value = self.requests.get(UUID(str(request_id)))
            if value is None or value["session_id"] != UUID(str(session_id)):
                return None
            return dict(value, committed_version=value.get("committed_version"), updated_at="now")

    def save_turn(self, state, *, expected_version, user_text, assistant_text, trace_steps, request_id=None):
        with self.guard:
            actual = self.states[state.session_id].version
            if actual != expected_version:
                raise VersionConflict(expected_version, actual)
            saved = state.model_copy(update={"version": actual + 1})
            self.states[state.session_id] = saved
            self.messages[state.session_id] += [
                {"id": str(uuid4()), "role": "user", "text": user_text, "created_at": datetime.now(timezone.utc).isoformat()},
                {"id": str(uuid4()), "role": "assistant", "text": assistant_text, "created_at": datetime.now(timezone.utc).isoformat()},
            ]
            self.trace[state.session_id] += list(trace_steps)
            if request_id is not None:
                self.requests[UUID(str(request_id))].update(status="completed", committed_version=saved.version)
            return saved


class FakeOrchestrator:
    def __init__(self, delay=0, error=None):
        self.delay, self.error = delay, error

    async def prepare_turn(self, state, text):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return state, "ok", [{"step_type": "llm", "name": "parse_request", "input": {}, "output": {}, "status": "ok"}]

    async def prepare_command(self, state, command_type, payload):
        if self.error:
            raise self.error
        changed = state.model_copy(deep=True)
        changed.requirements.append(Requirement(key=payload["key"], value=payload["value"], kind="hard", source="user_edit"))
        return changed, "command applied", [{"step_type": "state", "name": "edit_requirement", "input": payload, "output": {}, "status": "ok"}]


@pytest_asyncio.fixture
async def api_client(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    async with app.router.lifespan_context(app):
        app.state.store = MemoryStore()
        app.state.orchestrator = FakeOrchestrator()
        app.state.session_locks = {}
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.mark.asyncio
async def test_health_starts_without_database_or_llm_key(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
    assert response.status_code == 200 and response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_session_read_stale_conflict_and_sse(api_client):
    client = api_client
    created = await client.post("/api/sessions")
    assert created.status_code == 200
    session_id = created.json()["session_id"]
    read = await client.get(f"/api/sessions/{session_id}")
    assert read.json()["state"]["version"] == 0
    stale = await client.post(f"/api/sessions/{session_id}/messages", json={"request_id": str(uuid4()), "text": "x", "expected_version": 1})
    assert stale.status_code == 409
    response = await asyncio.wait_for(
        client.post(f"/api/sessions/{session_id}/messages", json={"request_id": str(uuid4()), "text": "x", "expected_version": 0}), timeout=3,
    )
    assert "event: step" in response.text and "event: state" in response.text
    assert "event: message" in response.text and 'event: done\ndata: {"ok":true,"version":1}' in response.text
    saved = (await client.get(f"/api/sessions/{session_id}")).json()
    assert saved["state"]["version"] == 1 and len(saved["messages"]) == 2
    assert (await client.get(f"/api/sessions/{session_id}/trace")).json()["steps"]
    assert (await client.get(f"/api/sessions/{uuid4()}")).status_code == 404


@pytest.mark.asyncio
async def test_command_endpoint_emits_saved_state_and_rejects_stale_version(api_client):
    client = api_client
    session_id = (await client.post("/api/sessions")).json()["session_id"]
    url = f"/api/sessions/{session_id}/commands"
    stale = await client.post(url, json={"request_id": str(uuid4()), "type": "edit_requirement", "payload": {"key": "origin", "value": "Москва"}, "expected_version": 4})
    assert stale.status_code == 409
    response = await client.post(url, json={"request_id": str(uuid4()), "type": "edit_requirement", "payload": {"key": "origin", "value": "Москва"}, "expected_version": 0})
    assert response.status_code == 200 and "event: state" in response.text and '"version":1' in response.text
    persisted = (await client.get(f"/api/sessions/{session_id}")).json()["state"]
    assert persisted["version"] == 1
    assert next(r for r in persisted["requirements"] if r["key"] == "origin")["value"] == "Москва"


@pytest.mark.asyncio
async def test_parallel_same_version_requests_only_commit_one(api_client):
    client = api_client
    app.state.orchestrator = FakeOrchestrator(delay=0.05)
    session_id = (await client.post("/api/sessions")).json()["session_id"]
    url, request = f"/api/sessions/{session_id}/messages", {"request_id": str(uuid4()), "text": "x", "expected_version": 0}
    a, b = await asyncio.gather(client.post(url, json=request), client.post(url, json=request))
    assert [a.status_code, b.status_code] == [200, 200]
    assert "event: replay" in a.text or "event: replay" in b.text
    assert app.state.store.get_state(session_id).version == 1
    assert len(app.state.store.messages[UUID(session_id)]) == 2


@pytest.mark.asyncio
async def test_request_id_duplicate_replays_ack_without_duplicate_messages(api_client):
    client = api_client
    session_id = (await client.post("/api/sessions")).json()["session_id"]
    req = {"request_id": str(uuid4()), "text": "x", "expected_version": 0}
    url = f"/api/sessions/{session_id}/messages"
    first = await client.post(url, json=req)
    replay = await client.post(url, json=req)
    assert first.status_code == replay.status_code == 200
    assert "event: replay" in replay.text
    stored = app.state.store.get_session(session_id)
    assert stored["state"].version == 1 and len(stored["messages"]) == 2
    status = await client.get(f"/api/sessions/{session_id}/requests/{req['request_id']}")
    assert status.json()["status"] == "completed"
    assert status.json()["committed_version"] == 1 and status.json()["current_version"] == 1
    assert status.json()["replay_available"] is False


@pytest.mark.asyncio
async def test_request_id_cannot_be_reused_for_different_content(api_client):
    client = api_client
    session_id = (await client.post("/api/sessions")).json()["session_id"]
    req_id = str(uuid4())
    url = f"/api/sessions/{session_id}/messages"
    await client.post(url, json={"request_id": req_id, "text": "first", "expected_version": 0})
    conflict = await client.post(url, json={"request_id": req_id, "text": "different", "expected_version": 0})
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "request_id_conflict"
    assert len(app.state.store.messages[UUID(session_id)]) == 2


@pytest.mark.asyncio
async def test_message_persists_extracted_requirements_bundle_and_version(api_client):
    class Parser:
        model_fast = "fake"

        async def chat_json(self, *_args, **_kwargs):
            return ParsedRequest(requirements=[
                Requirement(key="origin", value="Москва", kind="hard", source="user_text", quote="Из Москвы"),
                Requirement(key="destination", value="Сочи", kind="hard", source="user_text", quote="в Сочи"),
                Requirement(key="dates", value="10–17 ноября", kind="hard", source="user_text", quote="10–17 ноября"),
                Requirement(key="adults", value=2, kind="hard", source="user_text", quote="двое взрослых"),
            ])

    offer = Offer(offer_id="known-offer", kind="hotel", item_id="known-hotel", unit_price_rub=1200,
                  price_unit="per_room_night", units=7, total_rub=8400, synthetic=True)
    bundle = Bundle(id="known-bundle", label="дешевле", items=[offer], total_rub=8400)
    app.state.orchestrator = Orchestrator(
        Parser(), bundle_builder=lambda _requirements, _state: [bundle],
        today=lambda: __import__("datetime").date(2026, 10, 23),
    )
    client = api_client
    session_id = (await client.post("/api/sessions")).json()["session_id"]
    response = await client.post(
        f"/api/sessions/{session_id}/messages",
        json={"request_id": str(uuid4()), "text": "Из Москвы в Сочи 10–17 ноября, двое взрослых", "expected_version": 0},
    )
    assert response.status_code == 200 and '"version":1' in response.text
    persisted = (await client.get(f"/api/sessions/{session_id}")).json()["state"]
    assert persisted["version"] == 1
    assert persisted["bundles"][0]["id"] == "known-bundle"
    assert persisted["bundles"][0]["items"][0]["offer_id"] == "known-offer"
    assert {item["key"] for item in persisted["requirements"]} >= {"origin", "destination", "dates", "adults", "travel_year"}


@pytest.mark.asyncio
async def test_llm_failure_sse_does_not_change_state(api_client):
    client, store = api_client, app.state.store
    app.state.orchestrator = FakeOrchestrator(error=InvalidLLMJSONError("bad"))
    session_id = (await client.post("/api/sessions")).json()["session_id"]
    response = await client.post(f"/api/sessions/{session_id}/messages", json={"request_id": str(uuid4()), "text": "x", "expected_version": 0})
    assert "event: error" in response.text and 'event: done\ndata: {"ok":false}' in response.text
    assert store.get_state(session_id).version == 0
    assert store.messages[UUID(session_id)] == []


@pytest.mark.asyncio
async def test_timeout_sse_does_not_change_state(api_client):
    from app.agent.llm import LLMRequestError
    client, store = api_client, app.state.store
    app.state.orchestrator = FakeOrchestrator(error=LLMRequestError("timed out"))
    session_id = (await client.post("/api/sessions")).json()["session_id"]
    response = await client.post(f"/api/sessions/{session_id}/messages", json={"request_id": str(uuid4()), "text": "x", "expected_version": 0})
    assert '"code":"llm_request_failed"' in response.text and '"ok":false' in response.text
    assert store.get_state(session_id).version == 0 and store.messages[UUID(session_id)] == []


@pytest.mark.asyncio
async def test_missing_llm_key_is_a_clear_agent_error(api_client):
    app.state.orchestrator = Orchestrator(LLMClient(api_key="", model_fast="test-model"))
    client, store = api_client, app.state.store
    session_id = (await client.post("/api/sessions")).json()["session_id"]
    response = await client.post(f"/api/sessions/{session_id}/messages", json={"request_id": str(uuid4()), "text": "x", "expected_version": 0})
    assert '"code":"llm_not_configured"' in response.text
    assert 'event: done\ndata: {"ok":false}' in response.text
    assert store.get_state(session_id).version == 0
