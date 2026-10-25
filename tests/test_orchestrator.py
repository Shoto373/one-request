from datetime import date
from uuid import uuid4

import pytest

from app.agent.llm import InvalidLLMJSONError, LLMClient
from app.agent.orchestrator import InvalidAgentCommand, Orchestrator, ParsedRequest
from app.catalog.models import Offer
from app.state.models import Bundle, OpenQuestion, Requirement, TripState


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
async def test_product_destination_scope_rejects_text_and_ui_commands():
    calls = []
    def builder(requirements, _state):
        calls.append(requirements)
        return [bundle()]

    text = "Из Казани в Дубай 10–17 ноября, двое взрослых"
    extracted = ParsedRequest(requirements=[
        Requirement(key="origin", value="Казань", kind="hard", source="user_text", quote="Из Казани"),
        Requirement(key="destination", value="Дубай", kind="hard", source="user_text", quote="в Дубай"),
        Requirement(key="dates", value={"start": "2026-11-10", "end": "2026-11-17"}, kind="hard", source="user_text", quote="10–17 ноября"),
        Requirement(key="adults", value=2, kind="hard", source="user_text", quote="двое взрослых"),
    ])
    orchestrator = Orchestrator(FakeLLM(extracted), bundle_builder=builder)
    text_state, text_reply, _ = await orchestrator.prepare_turn(TripState(session_id=uuid4()), text)
    assert "только" in text_reply.lower() and not calls
    assert text_state.stage == "clarify" and text_state.open_questions[0].key == "destination"

    old_state = TripState(session_id=uuid4(), requirements=[
        Requirement(key="origin", value="Казань", kind="hard", source="user_text", quote="Из Казани"),
        Requirement(key="destination", value="AER", kind="hard", source="product_scope", label="Сочи"),
    ], bundles=[bundle()], selected_bundle_id="bundle-1", stage="select")
    edited, edit_reply, _ = await orchestrator.prepare_command(
        old_state, "edit_requirement", {"key": "destination", "value": "DXB", "kind": "hard"})
    assert "только" in edit_reply.lower() and edited.stage == "clarify"
    assert edited.open_questions[0].key == "destination" and edited.bundles == []
    refused, _, _ = await orchestrator.prepare_command(edited, "select_bundle", {"bundle_id": "bundle-1"})
    assert refused.bundles == [] and refused.selected_bundle_id is None
    assert not calls

    injected_choice = old_state.model_copy(update={"destinations": [{"id": "DXB", "name": "Дубай", "feasible": True}]})
    with pytest.raises(InvalidAgentCommand, match="outside configured product coverage"):
        await orchestrator.prepare_command(injected_choice, "select_destination", {"destination_id": "DXB"})


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
    assert [x["name"] for x in trace] == ["parse_request", "build_bundles", "explain_bundle", "build_bundles_result", "state_diff"]


@pytest.mark.asyncio
async def test_insufficient_request_asks_one_question_and_does_not_search():
    called = False

    def builder(*_):
        nonlocal called
        called = True
        return []

    parsed = ParsedRequest(requirements=[Requirement(key="destination", value="Сочи", kind="hard", source="user_text", quote="Сочи")])
    orchestrator = Orchestrator(FakeLLM(parsed), bundle_builder=builder)
    state, reply, trace = await orchestrator.prepare_turn(TripState(session_id=uuid4()), "В Сочи")
    assert len(state.open_questions) == 1
    assert not called
    assert "Откуда" in reply
    assert [step["name"] for step in trace] == ["parse_request", "state_diff"]


@pytest.mark.asyncio
async def test_empty_catalog_result_is_honest():
    orchestrator = Orchestrator(FakeLLM(full_request()), bundle_builder=lambda *_: [], no_bundle_explainer=lambda *_: [])
    state, reply, _ = await orchestrator.prepare_turn(
        TripState(session_id=uuid4()), "Из Москвы в Сочи 10–17 ноября, двое взрослых",
    )
    assert not state.bundles
    assert "не найдено" in reply
    assert "вымышленные" in reply


@pytest.mark.asyncio
async def test_llm_error_falls_back_to_rules_without_mutating_input():
    orchestrator = Orchestrator(FakeLLM(error=InvalidLLMJSONError("bad json")))
    initial = TripState(session_id=uuid4())
    candidate, _reply, trace = await orchestrator.prepare_turn(initial, "Хочу в Сочи")
    assert trace[0]["name"] == "parse_request_rules"
    assert any(r.key == "destination" for r in candidate.requirements)
    assert initial.version == 0 and not initial.requirements


@pytest.mark.asyncio
async def test_single_date_answer_is_kept_and_missing_origin_is_asked_without_assuming_moscow():
    state = TripState(
        session_id=uuid4(),
        requirements=[Requirement(key="origin", value="MOW", kind="assumption", source="product_scope"),
                      Requirement(key="destination", value="AER", kind="assumption", source="product_scope")],
        open_questions=[OpenQuestion(key="dates", question="На какие даты?", why="Нужен период."),
                        OpenQuestion(key="adults", question="Сколько взрослых?", why="Нужен состав.")],
    )
    orchestrator = Orchestrator(FakeLLM(error=InvalidLLMJSONError("use deterministic fallback")), today=lambda: date(2026, 10, 25))
    after_date, reply, _ = await orchestrator.prepare_turn(state, "12 декабря")

    start = next(r for r in after_date.requirements if r.key == "travel_start_date")
    assert start.value == "2026-12-12"
    assert next(r for r in after_date.requirements if r.key == "travel_year").value == 2026
    assert "На какие даты" not in reply
    assert "откуда" in reply.lower()
    assert not any(r.key == "origin" and r.source == "product_scope" for r in after_date.requirements)
    assert [q.key for q in after_date.open_questions] == ["origin"]

    after_origin, _, _ = await Orchestrator(FakeLLM(error=InvalidLLMJSONError("use deterministic fallback")), today=lambda: date(2026, 10, 25)).prepare_turn(
        after_date, "Казань",
    )
    assert next(r.value for r in after_origin.requirements if r.key == "origin") == "Казань"
    assert [q.key for q in after_origin.open_questions] == ["duration_nights"]

    after_duration, _, _ = await Orchestrator(FakeLLM(error=InvalidLLMJSONError("use deterministic fallback")), today=lambda: date(2026, 10, 25)).prepare_turn(
        after_origin, "на неделю",
    )
    dates = next(r.value for r in after_duration.requirements if r.key == "dates")
    assert dates == {"start": "2026-12-12", "end": "2026-12-19"}
