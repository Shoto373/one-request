from datetime import date
from uuid import uuid4

import pytest

from app.agent.llm import InvalidLLMJSONError, LLMClient
from app.agent.orchestrator import Orchestrator, ParsedRequest
from app.catalog.models import Offer
from app.state.models import Bundle, Requirement, TripState


class FakeLLM:
    model_fast = "fake"

    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    async def chat_json(self, *_args, **_kwargs):
        if self.error:
            raise self.error
        return self.result


def full_request():
    return ParsedRequest(requirements=[
        Requirement(key="origin", value="Москва", kind="hard", source="user_text", quote="Из Москвы"),
        Requirement(key="destination", value="Сочи", kind="hard", source="user_text", quote="в Сочи"),
        Requirement(key="dates", value="10–17 ноября", kind="hard", source="user_text", quote="10–17 ноября"),
        Requirement(key="adults", value=2, kind="hard", source="user_text", quote="двое взрослых"),
    ])


def bundle():
    offer = Offer(offer_id="offer-1", kind="hotel", item_id="hotel-1", unit_price_rub=1000,
                  price_unit="per_room_night", units=7, total_rub=7000, synthetic=True)
    return Bundle(id="bundle-1", label="дешевле", items=[offer], total_rub=7000)


@pytest.mark.asyncio
async def test_full_request_calls_builder_and_saves_explicit_year_assumption():
    calls = []

    def builder(requirements, state):
        calls.append((requirements, state))
        return [bundle()]

    orchestrator = Orchestrator(FakeLLM(full_request()), bundle_builder=builder, today=lambda: date(2026, 10, 23))
    initial = TripState(session_id=uuid4())
    state, reply, trace = await orchestrator.prepare_turn(initial, "Из Москвы в Сочи 10–17 ноября, двое взрослых")
    assert len(calls) == 1
    assert state.stage == "select" and state.bundles[0].id == "bundle-1"
    assert next(r for r in state.requirements if r.key == "travel_year").value == 2026
    assert next(r for r in state.requirements if r.key == "travel_year").kind == "assumption"
    assert next(r for r in state.requirements if r.key == "dates").value == {"start": "2026-11-10", "end": "2026-11-17"}
    assert "1 комплект" in reply
    assert [x["name"] for x in trace] == ["parse_request", "build_bundles"]


@pytest.mark.asyncio
async def test_insufficient_request_asks_at_most_two_and_does_not_search():
    called = False

    def builder(*_):
        nonlocal called
        called = True
        return []

    parsed = ParsedRequest(requirements=[Requirement(key="destination", value="Сочи", kind="hard", source="user_text", quote="Сочи")])
    orchestrator = Orchestrator(FakeLLM(parsed), bundle_builder=builder)
    state, reply, trace = await orchestrator.prepare_turn(TripState(session_id=uuid4()), "В Сочи")
    assert len(state.open_questions) == 2
    assert not called
    assert "Откуда" in reply
    assert len(trace) == 1


@pytest.mark.asyncio
async def test_empty_catalog_result_is_honest():
    orchestrator = Orchestrator(FakeLLM(full_request()), bundle_builder=lambda *_: [])
    state, reply, _ = await orchestrator.prepare_turn(
        TripState(session_id=uuid4()), "Из Москвы в Сочи 10–17 ноября, двое взрослых",
    )
    assert not state.bundles
    assert "не найдено" in reply
    assert "вымышленные" in reply


@pytest.mark.asyncio
async def test_llm_error_does_not_return_candidate_state():
    orchestrator = Orchestrator(FakeLLM(error=InvalidLLMJSONError("bad json")))
    initial = TripState(session_id=uuid4())
    with pytest.raises(InvalidLLMJSONError):
        await orchestrator.prepare_turn(initial, "request")
    assert initial.version == 0 and not initial.requirements
