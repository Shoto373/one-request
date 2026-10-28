"""Track A, sprint 3: three bundles, validate_plan edge cases, replan with kept/replaced."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest

from app.catalog.models import Party, Pet
from app.catalog.store import get_catalog
from app.planning.build import _assemble, build_bundles, build_trip_bundles
from app.planning.query import TripQuery
from app.planning.replan import replan, replan_bundle
from app.planning.scoring import cancel_loss
from app.planning.validate import validate_plan
from app.state.models import Bundle as StateBundle
from app.state.models import Requirement, TripState
from app.tools.search import flight_offers, search_hotels, search_transport

D = date
FAMILY = Party(adults=2, children_ages=[5])


@pytest.fixture(scope="module")
def cat():
    return get_catalog()


def _state(req: dict, bundles: list | None = None) -> TripState:
    return TripState(session_id=uuid4(), bundles=bundles or [], requirements=[
        Requirement(key=k, value=v, kind="hard", source="user_text") for k, v in req.items()])


# --------------------------------------------------------------------------- A1: three bundles


def test_three_distinct_bundles_for_family(cat) -> None:
    res = build_trip_bundles(cat, TripQuery("SVX", "AYT", D(2026, 11, 10), D(2026, 11, 17), FAMILY))
    labels = [b.label for b in res.bundles]
    assert labels == ["дешевле", "удобнее с ребёнком", "гибче отмена"]
    assert [b.id for b in res.bundles] == ["b1", "b2", "b3"]
    keys = {tuple(o.offer_id for o in b.items) for b in res.bundles}
    assert len(keys) == 3
    cheapest, comfort, flex = res.bundles
    assert cheapest.total_rub == min(b.total_rub for b in res.bundles)
    assert cancel_loss(flex.items) < cancel_loss(cheapest.items)
    assert all(b.feasible for b in res.bundles)
    for b in res.bundles:
        phrase = b.tradeoffs[0]
        assert phrase.refs and "₽" in phrase.text  # price and the trade-off in one sentence
    assert comfort.tradeoffs[0].text.startswith("+")


def test_hard_budget_applies_to_every_bundle(cat) -> None:
    res = build_trip_bundles(cat, TripQuery("MOW", "AER", D(2026, 11, 10), D(2026, 11, 17), Party(adults=2),
                                            budget_rub=32_000, budget_hard=True))
    assert res.bundles and all(b.total_rub <= 32_000 for b in res.bundles)


def test_build_bundles_contract_returns_up_to_four_state_bundles() -> None:
    req = {"origin": "Москва", "destination": "Сочи", "dates": {"start": "2026-11-10", "end": "2026-11-17"}, "adults": 2}
    bundles = [StateBundle.model_validate(b) for b in build_bundles(req, _state(req))]
    assert 2 <= len(bundles) <= 4 and bundles[0].label == "дешевле"


# --------------------------------------------------------------------------- A2: validate_plan


def _plan(cat, q: TripQuery, hotel_pred=lambda h: True, checkin=None, checkout=None):
    out = search_transport(cat, q.origin_city, q.dest_city, q.depart_date, q.party, q.kinds)[0]
    back = search_transport(cat, q.dest_city, q.origin_city, q.return_date, q.party, q.kinds)[0]
    hotels = search_hotels(cat, q.dest_city, checkin or q.depart_date, checkout or q.return_date, q.party)
    hotel = next(h for h in hotels if hotel_pred(cat.hotels[h.item_id]))
    return _assemble(cat, q, out, hotel, back)


def test_return_after_checkout_date_is_blocked(cat) -> None:
    q = TripQuery("MOW", "KZN", D(2026, 11, 13), D(2026, 11, 16), Party(adults=2), kinds=("train",))
    items, _ = _plan(cat, q, checkout=D(2026, 11, 15))  # hotel ends a night before the train back
    checks = validate_plan(cat, items, q.party)
    assert any(c.kind == "return_trip" and c.status == "block" for c in checks)


def test_long_gap_after_checkout_offers_late_checkout_or_storage(cat) -> None:
    q = TripQuery("MOW", "KZN", D(2026, 11, 13), D(2026, 11, 15), Party(adults=2), kinds=("train",))
    res = build_trip_bundles(cat, q)
    check = next(c for c in res.bundles[0].checks if c.kind == "return_trip")
    assert check.status == "warn" and {o.code for o in check.options} & {"late_checkout", "luggage_storage"}


def test_capacity_is_checked_against_rooms(cat) -> None:
    q = TripQuery("MOW", "AER", D(2026, 11, 10), D(2026, 11, 17), Party(adults=2), kinds=("flight",))
    items, _ = _plan(cat, q)
    checks = validate_plan(cat, items, Party(adults=4))
    cap = next(c for c in checks if c.kind == "capacity")
    assert cap.status == "block"


def test_pet_rules_of_carrier_hotel_and_transfer(cat) -> None:
    dog = Party(adults=2, pets=[Pet(weight_kg=7)])
    q = TripQuery("MOW", "AER", D(2026, 11, 10), D(2026, 11, 17), Party(adults=2), kinds=("flight",))
    items, _ = _plan(cat, q, hotel_pred=lambda h: not h.pets.allowed)
    pets = next(c for c in validate_plan(cat, items, dog) if c.kind == "pets")
    assert pets.status == "block" and "отель" in pets.text
    dxb = next(f for f in cat.flights.values() if f.dest_city == "DXB")
    assert flight_offers(dxb, dog) == []  # the UAE: no pet options at all


def test_night_arrival_shifts_check_in_date(cat) -> None:
    res = build_trip_bundles(cat, TripQuery("SVX", "AYT", D(2026, 11, 10), D(2026, 11, 17), Party(adults=2),
                                            kinds=("flight",), max_stops=0, limits_hard=True))
    for b in res.bundles:
        hotel = next(o for o in b.items if o.kind == "hotel")
        assert hotel.times.start.date() == D(2026, 11, 11)  # 22:30 departure lands 01:20 next day
        assert not any(c.kind == "checkin_date" and c.status == "block" for c in b.checks)


# --------------------------------------------------------------------------- A3: replan


def test_replan_dates_keep_choices_without_old_tails(cat) -> None:
    base = TripQuery("MOW", "AYT", D(2026, 11, 12), D(2026, 11, 19), FAMILY)
    old = build_trip_bundles(cat, base).bundles[0]
    new_q = replace(base, depart_date=D(2026, 11, 14), return_date=D(2026, 11, 21))
    r = replan_bundle(cat, old.items, new_q)
    assert r.bundle is not None and r.bundle.feasible
    old_numbers = {o.variant.get("flight_no") for o in old.items if o.kind == "flight"}
    new_numbers = {o.variant.get("flight_no") for o in r.bundle.items if o.kind == "flight"}
    assert old_numbers == new_numbers  # the same flights, moved to the new dates
    for o in r.bundle.items:  # nothing from the old dates survives
        assert D(2026, 11, 14) <= o.times.start.date() <= D(2026, 11, 21)
    assert r.price_delta_rub == r.bundle.total_rub - old.total_rub
    assert len(r.kept) + len(r.replaced) == len(old.items)


def test_replan_dog_replaces_only_the_hotel_side(cat) -> None:
    q = TripQuery("MOW", "AER", D(2026, 11, 10), D(2026, 11, 17), Party(adults=2), kinds=("flight",))
    items, _ = _plan(cat, q, hotel_pred=lambda h: not h.pets.allowed)
    r = replan_bundle(cat, items, replace(q, party=Party(adults=2, pets=[Pet(weight_kg=7)])))
    assert r.bundle is not None
    hotel = next(x for x in r.replaced if x["leg"] == "hotel")
    assert hotel["why"] == "отель не принимает животных"
    assert cat.hotels[next(o for o in r.bundle.items if o.kind == "hotel").item_id].pets.allowed
    kept_legs = {k["kind"] for k in r.kept}
    assert "flight" in kept_legs  # the carrier takes the dog: flights stay
    assert "с животным" in r.change


def test_replan_contract_and_infeasible_alternatives(cat) -> None:
    req = {"origin": "Москва", "destination": "Ереван", "dates": {"start": "2026-11-17", "end": "2026-11-20"}, "adults": 2}
    bundles = [StateBundle.model_validate(b) for b in build_bundles(req, _state(req))]
    state = _state(req, bundles)
    moved = dict(req, dates={"start": "2026-11-20", "end": "2026-11-23"})  # Yerevan is sold out
    out = replan(moved, state)
    assert out["bundle"] is None and out["alternatives"]
    ok = replan(dict(req, adults=3), state)
    assert ok["bundle"] is not None and ok["history_entry"]["change"].startswith("взрослых")
    assert ok["bundle"]["id"] == bundles[0].id
