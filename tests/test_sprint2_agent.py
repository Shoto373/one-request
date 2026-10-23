from datetime import date, datetime
from uuid import uuid4

import pytest

from app.agent.destination_search import SuggestionResult
from app.agent.explain import resolve_evidence, validate_claims
from app.agent.orchestrator import OrchestrationLimitError, Orchestrator, ParsedRequest, track_a_build_bundles
from app.agent.requirements import merge_requirement_changes, next_questions, validate_extraction
from app.state.models import Claim, Requirement, TripState


class FakeLLM:
    model_fast = "test"

    def __init__(self, responses=()):
        self.responses = list(responses)

    async def chat_json(self, *_args, **_kwargs):
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def req(key, value, kind="hard", quote=None):
    return Requirement(key=key, value=value, kind=kind, source="user_text", quote=quote)


def test_extraction_provenance_budget_and_ambiguity_guards():
    text = "Строго до 100 000, недорого, без долгих перелётов и с ребёнком"
    parsed = ParsedRequest(requirements=[
        req("budget_total_rub", 100000, "soft", "Строго до 100 000"),
        req("adults", 2, "hard", "с ребёнком"),
        req("max_duration_min", 180, "hard", "без долгих перелётов"),
        Requirement(key="assumption_child_menu", value=True, kind="assumption", source="user_text", quote="с ребёнком"),
    ])
    checked = validate_extraction(parsed, text)
    by_key = {r.key: r for r in checked.requirements}
    assert by_key["budget_total_rub"].kind == "hard"
    assert by_key["budget_preference"].kind == "soft" and by_key["budget_preference"].value == "low_cost"
    assert "adults" not in by_key and "max_duration_min" not in by_key
    assert by_key["assumption_child_menu"].source == "agent" and by_key["assumption_child_menu"].quote is None
    assert by_key["children_ages"].kind == "unknown" and by_key["children_ages"].value is None


def test_budget_basis_is_normalized_from_literal_user_quote():
    text = "Бюджет до 30 000 рублей на человека"
    parsed = validate_extraction(ParsedRequest(requirements=[
        req("budget_total_rub", 30000, "soft", text),
    ]), text)
    by_key = {r.key: r for r in parsed.requirements}
    assert "budget_total_rub" not in by_key
    assert by_key["budget_per_person_rub"].kind == "hard"
    assert by_key["budget_scope"].value == "person" and by_key["budget_scope"].kind == "hard"


def test_short_answers_merge_without_losing_known_values_and_unknown_does_not_replace():
    existing = [req("origin", "Москва", quote="Из Москвы"), req("adults", 2, quote="двое взрослых")]
    merged, suppressed = merge_requirement_changes(
        existing, [req("origin", "Санкт-Петербург", quote="Санкт-Петербург"),
                   Requirement(key="adults", value=None, kind="unknown", source="missing")],
    )
    values = {r.key: r.value for r in merged}
    assert values == {"origin": "Санкт-Петербург", "adults": 2}
    assert not suppressed


def test_questions_are_stable_limited_and_not_repeated_when_known():
    reqs = [req("origin", "Москва"), req("adults", 2), req("travel_month", 11), req("duration_nights", 7)]
    first = next_questions(reqs, action="suggest_destinations", required_inputs=("origin", "adults", "travel_window"))
    assert [q.key for q in first] == []
    assert len(next_questions([], action="suggest_destinations", required_inputs=("origin", "adults", "travel_window"))) == 2
    assert all(q.key and q.why and q.question for q in next_questions([], action="suggest_destinations"))


@pytest.mark.asyncio
async def test_assumptions_are_not_hard_search_filters():
    captured = {}

    def builder(requirements, state):
        captured["requirements"] = requirements
        captured["state"] = state
        return []

    parsed = ParsedRequest(requirements=[
        req("origin", "Москва", quote="Из Москвы"), req("destination", "Сочи", quote="в Сочи"),
        req("dates", "2026-11-10/2026-11-17", quote="10–17 ноября"), req("adults", 2, quote="двое взрослых"),
        Requirement(key="assumption_child_menu", value=True, kind="assumption", source="agent"),
    ])
    orch = Orchestrator(FakeLLM([parsed]), bundle_builder=builder, no_bundle_explainer=lambda *_: [], today=lambda: date(2026, 10, 23))
    state, _, _ = await orch.prepare_turn(TripState(session_id=uuid4()), "Из Москвы в Сочи 10–17 ноября, двое взрослых")
    assert "assumption_child_menu" not in captured["requirements"]
    assert all(r.kind != "assumption" for r in captured["state"].requirements)


@pytest.mark.asyncio
async def test_assumptions_are_excluded_from_suggestion_filter_payload():
    captured = {}
    async def suggest(requirements, state):
        captured.update(requirements=requirements, state=state)
        return SuggestionResult()

    state = _complete_state()
    state.requirements.append(Requirement(key="assumption_child_menu", value=True, kind="assumption", source="agent"))
    orch = Orchestrator(FakeLLM([]), destination_suggester=suggest)
    await orch._suggest(state, [])
    assert "assumption_child_menu" not in captured["requirements"]
    assert any(r.key == "assumption_child_menu" for r in captured["state"].requirements)


@pytest.mark.asyncio
async def test_removed_calendar_assumption_stays_suppressed_on_following_turn():
    state = _complete_state()
    state.requirements.append(Requirement(key="travel_year", value=2026, kind="assumption", source="calendar_resolution"))
    state.suppressed_assumptions = ["travel_year"]
    orch = Orchestrator(FakeLLM([ParsedRequest()]), destination_suggester=lambda *_: SuggestionResult())
    candidate, _, _ = await orch.prepare_turn(state, "Продолжай")
    assert "travel_year" not in {r.key for r in candidate.requirements}


@pytest.mark.asyncio
async def test_orchestrator_step_limit_returns_no_partial_result():
    parsed = ParsedRequest(requirements=[
        req("origin", "Москва", quote="Из Москвы"), req("adults", 2, quote="двое взрослых"),
        req("dates", {"start": "2026-11-10", "end": "2026-11-17"}, quote="10–17 ноября"),
    ])
    orch = Orchestrator(FakeLLM([parsed]), max_steps=1, destination_suggester=lambda *_: SuggestionResult())
    initial = TripState(session_id=uuid4())
    with pytest.raises(OrchestrationLimitError, match="лимит 1 шагов"):
        await orch.prepare_turn(initial, "Из Москвы 10–17 ноября, двое взрослых")
    assert initial.version == 0 and not initial.requirements


def _complete_state():
    return TripState(session_id=uuid4(), requirements=[
        req("origin", "Москва"), req("adults", 2),
        req("dates", {"start": "2026-11-10", "end": "2026-11-17"}),
    ])


@pytest.mark.asyncio
async def test_destinations_only_come_from_tool_and_are_marked_feasible():
    result = SuggestionResult(destinations=[
        {"id": "real", "name": "Анталья", "feasible": True,
         "evidence": [{"ref": "climate:AYT:11.sea_temp", "value": 23.6, "role": "fit"}]},
        {"id": "unconfirmed", "name": "Фантазия", "feasible": False},
    ])
    orch = Orchestrator(FakeLLM([]), destination_suggester=lambda *_: result)
    candidate, _ = await orch._suggest(_complete_state(), [])
    assert [item["id"] for item in candidate.destinations] == ["real"]
    assert candidate.destinations[0]["why"][0]["refs"] == ["climate:AYT:11.sea_temp"]


@pytest.mark.asyncio
async def test_accepting_confirmed_alternative_applies_exact_change_and_searches_again():
    calls = []

    async def suggester(requirements, state):
        calls.append(requirements.copy())
        return SuggestionResult(destinations=[{"id": "AYT", "name": "Анталья", "feasible": True}])

    state = _complete_state()
    state.requirements.append(req("budget_total_rub", 30000))
    state.requirements.append(req("budget_scope", "total"))
    state.proposed_changes = [{
        "id": "dates-plus-week", "label": "изменить даты", "confirmed": True,
        "changes": [{"key": "dates", "value": {"start": "2026-11-17", "end": "2026-11-24"}, "kind": "hard"}],
        "refs": ["catalog:availability:alt1"],
        "sources": {"catalog:availability:alt1": {"value": "available", "label": "availability", "source": "catalog", "source_refs": ["fixture:availability"]}},
    }]
    orch = Orchestrator(FakeLLM([]), destination_suggester=suggester)
    candidate, _, trace = await orch.prepare_command(state, "accept_alternative", {"alternative_id": "dates-plus-week"})
    assert calls and calls[0]["budget_total_rub"] == 30000
    assert candidate.requirements[[r.key for r in candidate.requirements].index("dates")].value["start"] == "2026-11-17"
    assert candidate.requirements[[r.key for r in candidate.requirements].index("budget_total_rub")].kind == "hard"
    assert any(item["name"] == "suggest_destinations" for item in trace)


@pytest.mark.asyncio
async def test_dubai_impossible_budget_requires_explicit_accept_then_real_build_succeeds():
    text = "Из Москвы в Дубай 10–17 ноября 2026, трое взрослых, строго 30 000 рублей на всю поездку."
    parsed = ParsedRequest(requirements=[
        req("origin", "Москва", quote="Из Москвы"), req("destination", "Дубай", quote="в Дубай"),
        req("dates", {"start": "2026-11-10", "end": "2026-11-17"}, quote="10–17 ноября 2026"),
        req("adults", 3, quote="трое взрослых"),
        req("budget_total_rub", 30000, quote="строго 30 000 рублей на всю поездку"),
        req("budget_scope", "trip", quote="на всю поездку"),
    ])
    accepted_requirements = {
        "origin": "Москва", "destination": "Дубай",
        "dates": {"start": "2026-11-10", "end": "2026-11-17"}, "adults": 3,
        "budget_total_rub": 349500, "budget_scope": "trip",
    }
    catalog_state = TripState(session_id=uuid4(), requirements=[
        Requirement(key=key, value=value, kind="hard", source="user_edit")
        for key, value in accepted_requirements.items()
    ])
    fixture_bundle = track_a_build_bundles(accepted_requirements, catalog_state)[0]
    minimum_total = fixture_bundle.total_rub
    source_refs = [f"offer:{offer.offer_id}" for offer in fixture_bundle.items]

    async def fixture_suggestions(*_):
        return SuggestionResult(alternatives=[{
            "id": "budget-to-minimum", "label": f"Поднять общий бюджет до {minimum_total:,} ₽", "confirmed": True,
            "changes": [{"key": "budget_total_rub", "value": minimum_total, "kind": "hard"}],
            "refs": ["catalog:build:DXB:cheapest_total"],
            "evidence": [{"ref": "catalog:build:DXB:cheapest_total", "value": minimum_total,
                          "label": "Минимальная стоимость поездки по каталогу", "kind": "catalog_estimate",
                          "source": "Каталог и расчёт build_bundles", "source_refs": source_refs, "role": "risk"}],
        }])

    orch = Orchestrator(
        FakeLLM([parsed]), bundle_builder=track_a_build_bundles, destination_suggester=fixture_suggestions,
        now=datetime.fromisoformat("2026-10-23T12:00:00+03:00"),
    )
    state, reply, _ = await orch.prepare_turn(TripState(session_id=uuid4()), text)
    assert not state.bundles and state.proposed_changes[0]["id"] == "budget-to-minimum"
    assert next(r.value for r in state.requirements if r.key == "budget_total_rub") == 30000
    assert "Ничего не изменено" in reply
    state, _, trace = await orch.prepare_command(state, "accept_alternative", {"alternative_id": "budget-to-minimum"})
    assert next(r.value for r in state.requirements if r.key == "budget_total_rub") == minimum_total
    assert [(b.id, b.total_rub) for b in state.bundles] == [("b1", minimum_total)]
    assert any(step["name"] == "build_bundles" for step in trace)


def test_claim_refs_and_numbers_must_match_resolved_catalog_data():
    facts, sources = resolve_evidence([{"ref": "climate:AYT:11.sea_temp", "value": 23.6, "role": "risk"}])
    assert facts and facts[0].value == 23.6 and sources[facts[0].ref]["kind"] == "climate_statistic"
    assert validate_claims([Claim(text="Температура моря 23,6 °C", refs=[facts[0].ref])], facts)
    assert not validate_claims([Claim(text="Температура моря 27 °C", refs=[facts[0].ref])], facts)
    assert not validate_claims([Claim(text="Температура моря 23,6 °C", refs=["climate:XXX:11.sea_temp"])], facts)
