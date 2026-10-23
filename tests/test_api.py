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

    def save_turn(self, state, *, expected_version, user_text, assistant_text, trace_steps):
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
    stale = await client.post(f"/api/sessions/{session_id}/messages", json={"text": "x", "expected_version": 1})
    assert stale.status_code == 409
    response = await asyncio.wait_for(
        client.post(f"/api/sessions/{session_id}/messages", json={"text": "x", "expected_version": 0}), timeout=3,
    )
    assert "event: step" in response.text and "event: state" in response.text
    assert "event: message" in response.text and 'event: done\ndata: {"ok":true,"version":1}' in response.text
    saved = (await client.get(f"/api/sessions/{session_id}")).json()
    assert saved["state"]["version"] == 1 and len(saved["messages"]) == 2
    assert (await client.get(f"/api/sessions/{session_id}/trace")).json()["steps"]
    assert (await client.get(f"/api/sessions/{uuid4()}")).status_code == 404


@pytest.mark.asyncio
async def test_parallel_same_version_requests_only_commit_one(api_client):
    client = api_client
    app.state.orchestrator = FakeOrchestrator(delay=0.05)
    session_id = (await client.post("/api/sessions")).json()["session_id"]
    url, request = f"/api/sessions/{session_id}/messages", {"text": "x", "expected_version": 0}
    a, b = await asyncio.gather(client.post(url, json=request), client.post(url, json=request))
    assert sorted([a.status_code, b.status_code]) == [200, 409]
    assert app.state.store.get_state(session_id).version == 1
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
        json={"text": "Из Москвы в Сочи 10–17 ноября, двое взрослых", "expected_version": 0},
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
    response = await client.post(f"/api/sessions/{session_id}/messages", json={"text": "x", "expected_version": 0})
    assert "event: error" in response.text and 'event: done\ndata: {"ok":false}' in response.text
    assert store.get_state(session_id).version == 0
    assert store.messages[UUID(session_id)] == []


@pytest.mark.asyncio
async def test_missing_llm_key_is_a_clear_agent_error(api_client):
    app.state.orchestrator = Orchestrator(LLMClient(api_key="", model_fast="test-model"))
    client, store = api_client, app.state.store
    session_id = (await client.post("/api/sessions")).json()["session_id"]
    response = await client.post(f"/api/sessions/{session_id}/messages", json={"text": "x", "expected_version": 0})
    assert '"code":"llm_not_configured"' in response.text
    assert 'event: done\ndata: {"ok":false}' in response.text
    assert store.get_state(session_id).version == 0
