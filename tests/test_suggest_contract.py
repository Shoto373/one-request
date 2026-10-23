"""Track A <-> B, sprint 2: app.planning.suggest through B's adapter, evidence and alternatives."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from app.agent.destination_search import suggest_destinations as b_suggest
from app.agent.explain import resolve_evidence
from app.agent.orchestrator import Orchestrator
from app.catalog.store import get_catalog
from app.planning.build import build_bundles
from app.planning.query import query_from_requirements
from app.planning.suggest import suggest_destinations
from app.state.models import Requirement, TripState

# Requirement keys exactly as B's parse_request produces them (app/agent/requirements.py).
VAGUE = {"origin": "Москва", "travel_month": 11, "adults": 2, "children_ages": [5], "duration_nights": 7,
         "budget_preference": "low_cost", "travel_time_preference": "short", "trip_style": "тёплое море"}
SOFT = {"budget_preference", "travel_time_preference", "trip_style"}
DXB = {"origin": "Москва", "destination": "Дубай", "dates": {"start": "2026-11-10", "end": "2026-11-17"},
       "adults": 3, "budget_total_rub": 30000, "budget_scope": "trip"}


def _state(req: dict, soft: set[str] = frozenset()) -> TripState:
    return TripState(session_id=uuid4(), requirements=[
        Requirement(key=k, value=v, kind="soft" if k in soft else "hard", source="user_text") for k, v in req.items()])


def test_vague_request_through_b_adapter() -> None:
    result = asyncio.run(b_suggest(VAGUE, _state(VAGUE, SOFT)))
    assert 1 <= len(result.destinations) <= 3
    assert {d["id"] for d in result.destinations} <= {"AYT", "SSH", "DXB"}
    for d in result.destinations:
        assert d["feasible"] is True
        facts, sources = resolve_evidence(d["evidence"], destination=d)
        assert len(facts) == len(d["evidence"])  # every fact resolves for B's explain and UI tooltips
        assert any(f.kind == "climate_statistic" for f in facts)
        assert sources[f"trip:{d['id']}:total_rub"]["value"] == d["est_total_rub"] == d["bundle"]["total_rub"]


def test_b_keys_are_understood() -> None:
    cat = get_catalog()
    req = dict(VAGUE, pets=True, pet_weight_kg=6, max_duration_min=240,
               destination="Анталья", dates={"start": "2026-11-12", "end": "2026-11-19"}, date_flex_days=2)
    q, problems = query_from_requirements(cat, req, _state(req))
    assert not problems
    assert q.party.pets[0].weight_kg == 6 and q.party.children_ages == [5]
    assert q.max_travel_min == 240 and q.date_flex_days == 2


def test_impossible_dubai_alternatives_pass_b_validation() -> None:
    result = asyncio.run(b_suggest(DXB, _state(DXB)))
    assert len(result.alternatives) == 2
    orch = Orchestrator(None)
    accepted = asyncio.run(orch._validated_alternatives(result.alternatives))
    assert [a["id"] for a in accepted] == [a.id for a in result.alternatives]
    labels = " ".join(a.label for a in result.alternatives)
    assert "бюджет" in labels.lower()


def test_accepting_alternative_builds_its_bundle() -> None:
    alt = suggest_destinations(DXB, _state(DXB))["alternatives"][0]
    updated = dict(DXB)
    for change in alt["changes"]:
        updated[change["key"]] = change["value"]
    bundles = build_bundles(updated, _state(updated))
    assert bundles and bundles[0]["total_rub"] == alt["total_rub"]


def test_outside_catalog_has_no_alternatives() -> None:
    paris = {"origin": "Москва", "destination": "Париж", "dates": {"start": "2026-11-10", "end": "2026-11-17"},
             "adults": 2}
    result = suggest_destinations(paris, _state(paris))
    assert not result["destinations"] and not result["alternatives"]
    assert "Париж" in result["notes"][0]


def test_missing_inputs_are_reported() -> None:
    assert suggest_destinations({"travel_month": 11}, _state({"travel_month": 11}))["required_inputs"] == ["origin"]
    out = suggest_destinations({"origin": "Москва"}, _state({"origin": "Москва"}))
    assert out["required_inputs"] == ["travel_window"]


def test_feasible_concrete_trip_needs_no_alternatives() -> None:
    req = {"origin": "Москва", "destination": "Сочи", "dates": {"start": "2026-11-10", "end": "2026-11-17"}, "adults": 2}
    result = suggest_destinations(req, _state(req))
    assert result["alternatives"] == [] and result["notes"]
