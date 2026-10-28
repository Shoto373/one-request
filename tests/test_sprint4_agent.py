from uuid import uuid4

import pytest

from app.agent.orchestrator import InvalidAgentCommand, Orchestrator, ParsedRequest
from app.agent.requirements import normalize_contextual_changes
from app.catalog.models import Offer
from app.state.models import Bundle, Requirement, TripState


class FakeLLM:
    model_fast = "test"

    def __init__(self, *responses):
        self.responses = list(responses)

    async def chat_json(self, *_args, **_kwargs):
        return self.responses.pop(0)


def req(key, value, kind="hard"):
    return Requirement(key=key, value=value, kind=kind, source="user_text")


def selected_state():
    old = Offer(offer_id="hotel-old", kind="hotel", item_id="hotel-1", title="Test hotel",
                unit_price_rub=7000, price_unit="per_trip", units=1, total_rub=7000, synthetic=True,
                times={"start": "2026-11-10T14:00:00+03:00", "end": "2026-11-17T12:00:00+03:00"})
    bundle = Bundle(id="b1", label="cheaper", items=[old], total_rub=7000)
    return TripState(session_id=uuid4(), stage="assemble", selected_destination_id="AER",
                     selected_bundle_id="b1", bundles=[bundle], requirements=[
                         req("origin", "Москва"), req("destination", "AER"),
                         req("dates", {"start": "2026-11-10", "end": "2026-11-17"}), req("adults", 2),
                     ])


def replan_result(*, checks=None):
    new_offer = Offer(offer_id="hotel-new", kind="hotel", item_id="hotel-1", title="Test hotel",
                      unit_price_rub=7200, price_unit="per_trip", units=1, total_rub=7200, synthetic=True,
                      times={"start": "2026-11-12T14:00:00+03:00", "end": "2026-11-19T12:00:00+03:00"})
    updated = Bundle(id="b1", label="cheaper", items=[new_offer], total_rub=7200, checks=checks or [])
    return {"bundles": [updated], "kept": ["object:hotel:hotel-1"], "replaced": ["offer:hotel-old→hotel-new"],
            "reasons": ["re-priced for new dates"], "price_delta_rub": 200}


@pytest.mark.asyncio
async def test_accumulated_english_replan_diff_includes_relative_dates_pet_and_followup_weight():
    calls = []

    async def replan(state, diff):
        calls.append((state, diff))
        return replan_result(checks=[{"kind": "pets", "status": "ok",
                                     "text": "Carrier, hotel and transfer checked", "refs": ["offer:hotel-new"]}])

    first, question, _ = await Orchestrator(FakeLLM(ParsedRequest()), replanner=replan).prepare_turn(
        selected_state(), "Two days later and with a dog",
    )
    assert first.open_questions and first.open_questions[0].key == "pet_weight_kg"
    assert not calls
    answer = ParsedRequest(requirements=[Requirement(
        key="pet_weight_kg", value=8, kind="hard", source="user_text", quote="8 kg",
    )])
    final, _, _ = await Orchestrator(FakeLLM(answer), replanner=replan).prepare_turn(first, "8 kg")
    assert len(calls) == 1
    source_state, diff = calls[0]
    values = {x["key"]: x["value"] for x in diff["upserts"]}
    assert values == {
        "dates": {"start": "2026-11-12", "end": "2026-11-19"},
        "pets": {"kind": "dog"}, "pet_weight_kg": 8,
    }
    assert source_state.selected_bundle_id == "b1"
    assert source_state.bundles[0].items[0].offer_id == "hotel-old"
    assert {r.key: r.value for r in source_state.requirements}["origin"] == "Москва"
    assert final.history[-1].change


def test_english_budget_reduction_without_amount_asks_instead_of_guessing():
    state = selected_state()
    parsed, question = normalize_contextual_changes(ParsedRequest(), "Lower the budget", state)
    assert not parsed.requirements
    assert question and question.key == "budget_total_rub"


@pytest.mark.asyncio
async def test_anywhere_keeps_configured_sochi_destination_and_asks_for_origin(monkeypatch):
    monkeypatch.setenv("WORLDWIDE_MODE", "false")
    parsed = ParsedRequest(requirements=[
        Requirement(key="travel_month", value=11, kind="hard", source="user_text", quote="November"),
        Requirement(key="budget_preference", value="low_cost", kind="soft", source="user_text", quote="cheap"),
    ])
    calls = []

    async def suggest(requirements, state):
        calls.append(requirements)
        return {"destinations": [], "alternatives": [], "notes": [], "required_inputs": []}

    result, _, _ = await Orchestrator(FakeLLM(parsed), destination_suggester=suggest).prepare_turn(
        TripState(session_id=uuid4()), "I want to go anywhere warm in November",
    )
    destination = next(r for r in result.requirements if r.key == "destination")
    assert destination.value == "Сочи" and destination.source == "product_scope"  # city name, not catalog id
    assert len(result.open_questions) == 1 and result.open_questions[0].key == "origin"
    assert not calls


@pytest.mark.asyncio
async def test_cheap_and_five_stars_are_soft_tradeoffs_checked_by_catalog_not_contradiction():
    parsed = ParsedRequest(requirements=[
        Requirement(key="origin", value="Москва", kind="hard", source="user_text", quote="Moscow"),
        Requirement(key="destination", value="AER", kind="hard", source="user_text", quote="Sochi"),
        Requirement(key="dates", value={"start": "2026-11-10", "end": "2026-11-17"}, kind="hard", source="user_text", quote="10–17 November"),
        Requirement(key="adults", value=2, kind="hard", source="user_text", quote="two adults"),
        Requirement(key="budget_preference", value="low_cost", kind="soft", source="user_text", quote="cheap"),
        Requirement(key="lodging_stars", value=5, kind="soft", source="user_text", quote="five-star"),
    ])
    seen = []
    async def builder(requirements, state):
        seen.append(requirements)
        return []

    state, reply, trace = await Orchestrator(FakeLLM(parsed), bundle_builder=builder).prepare_turn(
        TripState(session_id=uuid4()), "From Moscow to Sochi 10–17 November, two adults: cheap but five-star hotel",
    )
    assert seen and seen[0]["budget_preference"] == "low_cost" and seen[0]["lodging_stars"] == 5, (reply, state.open_questions, trace)


@pytest.mark.asyncio
async def test_unknown_alternative_and_cross_session_bundle_ids_are_rejected():
    state = selected_state()
    agent = Orchestrator(FakeLLM())
    for command, payload in [
        ("accept_alternative", {"alternative_id": "alt-from-another-session"}),
        ("select_bundle", {"bundle_id": "bundle-from-another-session"}),
    ]:
        with pytest.raises(InvalidAgentCommand):
            await agent.prepare_command(state, command, payload)
