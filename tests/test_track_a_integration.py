"""Track A <-> B/C boundary: orchestrator build_bundles, TripState models, checkout validate_bundle."""

from __future__ import annotations

import copy
from uuid import uuid4

import pytest

from app.agent.orchestrator import track_a_build_bundles
from app.agent.replanning import track_a_replan
from app.catalog.store import get_catalog
from app.planning.build import build_bundles, explain_no_bundles
from app.planning.query import parse_budget, parse_children, query_from_requirements, resolve_city
from app.planning.validate import validate_bundle
from app.state.models import Bundle as StateBundle
from app.state.models import Requirement, TripState

SOCHI = {"origin": "Москва", "destination": "Сочи", "dates": {"start": "2026-11-10", "end": "2026-11-17"}, "adults": 2}


def _state(req: dict, kinds: dict | None = None) -> TripState:
    kinds = kinds or {}
    return TripState(
        session_id=uuid4(),
        requirements=[Requirement(key=k, value=v, kind=kinds.get(k, "hard"), source="user_text") for k, v in req.items()],
    )


@pytest.mark.parametrize(
    ("text", "city"),
    [("Москва", "MOW"), ("из Москвы", "MOW"), ("СПб", "LED"), ("Питер", "LED"), ("Екатеринбурга", "SVX"),
     ("в Казань", "KZN"), ("Казани", "KZN"), ("Сочи", "AER"), ("Адлер", "AER"), ("Анталию", "AYT"),
     ("Шарм-эль-Шейх", "SSH"), ("Дубай", "DXB"), ("Ереван", "EVN"), ("Istanbul", "IST"), ("AYT", "AYT"),
     ("SVO", "MOW"), ("Париж", None)],
)
def test_resolve_city(text: str, city: str | None) -> None:
    assert resolve_city(get_catalog(), text) == city


def test_parsers() -> None:
    assert parse_budget("до 100 тысяч") == 100_000
    assert parse_budget(150000) == 150_000
    assert parse_children("ребёнок 5 лет") == [5]
    assert parse_children([{"age": 3}, 9]) == [3, 9]
    assert len(parse_children(2)) == 2


def test_orchestrator_contract_returns_state_bundles() -> None:
    state = _state(SOCHI)
    bundles = track_a_build_bundles(SOCHI, state)  # the exact call path of app/agent/orchestrator.py
    assert 1 <= len(bundles) <= 3 and bundles[0].id == "b1"
    b = bundles[0]
    assert isinstance(b, StateBundle)
    assert b.total_rub == sum(o.total_rub for o in b.items)
    assert all(isinstance(opt, str) for c in b.checks for opt in c.options)
    assert all(o.title for o in b.items)
    assert all(o.baggage is None or isinstance(o.baggage, str) for o in b.items)


def test_unresolved_request_is_empty_with_reasons() -> None:
    req = dict(SOCHI, destination="Париж")
    assert build_bundles(requirements=req, state=_state(req)) == []
    assert any("Париж" in r for r in explain_no_bundles(req, _state(req)))


def test_hard_budget_comes_from_state_kind() -> None:
    req = {"origin": "Москва", "destination": "Дубай", "dates": {"start": "2026-11-10", "end": "2026-11-17"},
           "adults": 3, "budget_total_rub": 30000}
    q, _ = query_from_requirements(get_catalog(), req, _state(req))
    assert q is not None and q.budget_hard
    assert build_bundles(requirements=req, state=_state(req)) == []
    assert any("больше бюджета" in n for n in explain_no_bundles(req, _state(req)))
    soft = _state(req, {"budget_total_rub": "soft"})
    q, _ = query_from_requirements(get_catalog(), req, soft)
    assert q is not None and not q.budget_hard


def test_train_requirement() -> None:
    req = {"origin": "Москва", "destination": "Казань", "dates": {"start": "2026-11-13", "end": "2026-11-15"},
           "adults": 2, "transport": "поездом"}
    bundles = build_bundles(requirements=req, state=_state(req))
    assert bundles and {o["kind"] for o in bundles[0]["items"]} == {"train", "transfer", "hotel"}


def test_checkout_revalidation_ok_and_price_change_blocks() -> None:
    state = _state(SOCHI)
    bundle = build_bundles(requirements=SOCHI, state=state)[0]
    state_json = state.model_dump(mode="json")
    checks = validate_bundle(bundle, state_json)
    assert not [c for c in checks if c["status"] == "block"]
    assert any(c["kind"] == "availability" and c["status"] == "ok" for c in checks)

    tampered = copy.deepcopy(bundle)
    hotel = next(o for o in tampered["items"] if o["kind"] == "hotel")
    hotel["unit_price_rub"] -= 100
    hotel["total_rub"] = hotel["unit_price_rub"] * hotel["units"] * hotel["quantity"]
    checks = validate_bundle(tampered, state_json)
    assert any(c["kind"] == "availability" and c["status"] == "block" for c in checks)


def test_bundle_outside_catalog_keeps_stored_checks() -> None:
    stored = [{"kind": "pets", "status": "block", "text": "Отель не принимает собак"}]
    fake = {"id": "b1", "items": [{"offer_id": "o1", "kind": "hotel", "item_id": "not-in-catalog",
                                   "unit_price_rub": 100, "price_unit": "per_trip", "units": 1, "total_rub": 100}],
            "total_rub": 100, "checks": stored}
    assert validate_bundle(fake, None) == stored


def test_checkout_revalidation_without_state_uses_capacity() -> None:
    bundle = build_bundles(requirements=SOCHI, state=_state(SOCHI))[0]
    checks = validate_bundle(bundle, None)
    assert not [c for c in checks if c["status"] == "block"]


def test_real_track_a_replan_receives_accumulated_dates_pet_and_weight_and_returns_current_offers():
    old_requirements = {
        "origin": "Москва", "destination": "AYT",
        "dates": {"start": "2026-11-12", "end": "2026-11-19"},
        "adults": 2, "children_ages": [5],
    }
    initial = _state(old_requirements)
    raw = build_bundles(requirements=old_requirements, state=initial)
    assert raw, "fixed A catalog fixture should produce the old selected bundle"
    old_bundle = StateBundle.model_validate(raw[0])
    initial.bundles, initial.selected_bundle_id = [old_bundle], old_bundle.id
    initial.requirements.extend([
        Requirement(key="dates", value={"start": "2026-11-14", "end": "2026-11-21"}, kind="hard", source="user_edit"),
        Requirement(key="pets", value={"kind": "dog"}, kind="hard", source="user_edit"),
        Requirement(key="pet_weight_kg", value=8, kind="hard", source="user_edit"),
    ])
    # Remove old duplicated dates entry while preserving the rest of the selected-state context.
    dedup = {}
    for requirement in initial.requirements:
        dedup[requirement.key] = requirement
    initial.requirements = list(dedup.values())
    accumulated = {"upserts": [
        {"key": "dates", "value": {"start": "2026-11-14", "end": "2026-11-21"}, "kind": "hard"},
        {"key": "pets", "value": {"kind": "dog"}, "kind": "hard"},
        {"key": "pet_weight_kg", "value": 8, "kind": "hard"},
    ], "remove_keys": []}

    import asyncio
    result = asyncio.run(track_a_replan(initial, accumulated))
    assert result["input_changes"] == accumulated
    assert result["selected_bundle_id"] == old_bundle.id
    assert result["bundles"], result["notes"]
    new_bundle = StateBundle.model_validate(result["bundles"][0])
    assert new_bundle.total_rub == sum(item.total_rub for item in new_bundle.items)
    assert result["price_delta_rub"] == new_bundle.total_rub - old_bundle.total_rub
    assert all(item.times is None or item.times.start.date().isoformat() >= "2026-11-14" for item in new_bundle.items)
    hotel = next(item for item in new_bundle.items if item.kind == "hotel")
    assert hotel.times.start.date().isoformat() == "2026-11-14"
    assert hotel.times.end.date().isoformat() == "2026-11-21"
    assert any(check.kind == "pets" and check.status == "ok" for check in new_bundle.checks)
