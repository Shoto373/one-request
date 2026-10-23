"""Track A, sprint 2: suggest_destinations, minimal changes for infeasible requests, edge cases."""

from __future__ import annotations

import json
from datetime import date
from uuid import uuid4

import pytest

from app.catalog.models import Party, Pet
from app.catalog.store import get_catalog
from app.planning.alternatives import minimal_changes, trip_alternatives
from app.planning.build import build_trip_bundles
from app.planning.destinations import (
    destination_query_from_requirements,
    parse_month,
    parse_nights,
    suggest_destinations,
    suggest_destinations_report,
)
from app.planning.query import TripQuery, query_from_requirements
from app.state.models import Requirement, TripState
from app.tools.climate import get_climate

D = date
VAGUE = {"origin": "Москва", "destination_type": "тёплое море", "month": "ноябрь", "budget": "недорого",
         "children": 1, "flight_duration": "без долгих перелётов"}


@pytest.fixture(scope="module")
def cat():
    return get_catalog()


def test_climate_for_all_destinations(cat) -> None:
    for city in ("AER", "KGD", "KZN", "IST", "AYT", "SSH", "DXB", "EVN"):
        assert len(cat.climate[city].months) == 12
    nov = get_climate(cat, "AYT", 11)
    assert nov is not None and nov.sea_temp and 20 < nov.sea_temp < 27
    assert get_climate(cat, "KZN", 11).sea_temp is None


def test_parsers() -> None:
    assert parse_month("в ноябре") == 11
    assert parse_month("2026-12") == 12
    assert parse_month({"start": "2026-11-10", "end": "2026-11-17"}) == 11
    assert parse_nights("неделя", "") == 7
    assert parse_nights("10 дней", "") == 9
    assert parse_nights(None, "на выходные в казань") == 2


def test_checkpoint_vague_request(cat) -> None:
    """«к тёплому морю в ноябре, недорого, с ребёнком, без долгих перелётов» -> 3 feasible warm-sea options."""
    report = suggest_destinations_report(VAGUE)
    options = report["options"]
    assert len(options) == 3
    assert {o["id"] for o in options} <= {"AYT", "SSH", "DXB"}
    for o in options:
        assert o["feasible"] and o["bundle"]["total_rub"] == o["est_total_rub"]
        assert o["climate"]["month"] == 11 and o["climate"]["sea_temp"] >= 21
        assert {b["factor"] for b in o["score_breakdown"]} == {"weather", "price", "travel", "visa", "match"}
        assert abs(sum(b["contribution"] for b in o["score_breakdown"]) - o["score"]) < 0.01
        assert any(r.startswith("climate:") for c in o["why"] for r in c["refs"])
        assert all(c["refs"] for c in o["why"])
    assert [o["rank"] for o in options] == [1, 2, 3]
    assert any("7 ночей" in a for a in report["assumptions"])
    json.dumps(report)  # JSON-ready for TripState and SSE


def test_destinations_fit_trip_state(cat) -> None:
    options = suggest_destinations(VAGUE)
    state = TripState(session_id=uuid4(), destinations=options)
    assert state.destinations[0]["id"] == options[0]["id"]


def test_only_feasible_destinations_with_dog(cat) -> None:
    req = dict(VAGUE, pets="собака 7 кг")
    report = suggest_destinations_report(req, limit=8)
    ids = {o["id"] for o in report["options"]}
    rejected = {r["id"] for r in report["rejected"]}
    assert {"SSH", "DXB"} <= rejected  # no pet transfers / no pets into the UAE
    assert ids and not ids & {"SSH", "DXB"}


def test_hard_sea_filters_non_beach(cat) -> None:
    state = TripState(session_id=uuid4(), requirements=[
        Requirement(key="sea", value="тёплое море", kind="hard", source="user_text", quote="тёплое море")])
    req = {"origin": "Москва", "month": "ноябрь", "sea": "тёплое море"}
    report = suggest_destinations_report(req, state, limit=8)
    assert {o["id"] for o in report["options"]} <= {"AYT", "SSH", "DXB"}
    assert {"KZN", "EVN", "AER"} <= {r["id"] for r in report["rejected"]}


def test_missing_origin_is_a_problem(cat) -> None:
    report = suggest_destinations_report({"month": "ноябрь"})
    assert not report["options"] and report["problems"][0].startswith("origin")


def test_month_outside_window(cat) -> None:
    dq, problems = destination_query_from_requirements(cat, {"origin": "MOW", "month": "июль"})
    assert dq is None and "окна каталога" in problems[0]


def test_impossible_dubai_gives_two_changes(cat) -> None:
    """«Дубай на неделю на троих за 30 000» -> nothing fits, two minimal changes with their price."""
    q = TripQuery("MOW", "DXB", D(2026, 11, 10), D(2026, 11, 17), Party(adults=3), budget_rub=30_000)
    changes = minimal_changes(cat, q)
    assert len(changes) == 2 and len({c.kind for c in changes}) == 2
    assert all(c.bundle is not None and c.total_rub == c.bundle.total_rub for c in changes)
    assert changes[0].over_budget_rub <= changes[1].over_budget_rub


def test_hard_requirements_need_consent(cat) -> None:
    q = TripQuery("MOW", "DXB", D(2026, 11, 10), D(2026, 11, 17), Party(adults=3), budget_rub=30_000,
                  budget_hard=True, dest_hard=True, dates_hard=True)
    changes = minimal_changes(cat, q)
    assert changes and all(c.relaxes_hard for c in changes)
    assert all(c.to_wire()["needs_consent"] for c in changes)


def test_sold_out_dates_suggest_date_shift(cat) -> None:
    q = TripQuery("MOW", "EVN", D(2026, 11, 20), D(2026, 11, 23), Party(adults=2))
    changes = minimal_changes(cat, q)
    assert changes[0].kind == "dates"
    assert build_trip_bundles(cat, TripQuery("MOW", "EVN", D(2026, 11, 23), D(2026, 11, 26), Party(adults=2))).bundles


def test_dog_to_dubai_suggests_other_destination(cat) -> None:
    q = TripQuery("MOW", "DXB", D(2026, 11, 10), D(2026, 11, 17), Party(adults=2, pets=[Pet(weight_kg=6)]))
    changes = minimal_changes(cat, q)
    assert changes and changes[0].kind == "destination" and changes[0].change["to"] not in ("DXB", "SSH")


def test_feasible_trip_needs_no_changes(cat) -> None:
    assert minimal_changes(cat, TripQuery("MOW", "AER", D(2026, 11, 10), D(2026, 11, 17), Party(adults=2))) == []


def test_trip_alternatives_contract() -> None:
    req = {"origin": "Москва", "destination": "Дубай", "dates": {"start": "2026-11-10", "end": "2026-11-17"},
           "adults": 3, "budget_total_rub": 30000}
    out = trip_alternatives(req)
    assert len(out) == 2 and all({"kind", "text", "total_rub", "needs_consent", "change", "bundle"} <= set(c) for c in out)
    json.dumps(out)


def test_soft_travel_limit_falls_back_with_risk(cat) -> None:
    req = {"origin": "Москва", "destination": "Дубай", "dates": {"start": "2026-11-10", "end": "2026-11-17"},
           "adults": 2, "flight_duration": "без долгих перелётов"}
    q, _ = query_from_requirements(cat, req)
    assert q.max_travel_min == 300 and not q.limits_hard
    res = build_trip_bundles(cat, q)
    assert res.bundles and any("Не выполнено пожелание" in r.text for r in res.bundles[0].risks)


def test_direct_only_is_respected(cat) -> None:
    q = TripQuery("SVX", "KGD", D(2026, 11, 10), D(2026, 11, 17), Party(adults=1), kinds=("flight",), max_stops=0,
                  limits_hard=True)
    res = build_trip_bundles(cat, q)
    for b in res.bundles:
        assert all(o.variant.get("stops", 0) == 0 for o in b.items if o.kind == "flight")


def test_suggest_is_deterministic() -> None:
    assert suggest_destinations(VAGUE) == suggest_destinations(VAGUE)
