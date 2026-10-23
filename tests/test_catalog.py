"""Track A: seed validity, search contract and build_bundles v0 on the planted scenarios."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app.catalog.models import Party, Pet
from app.catalog.store import Catalog, seed_dir
from app.planning.build import build_trip_bundles
from app.planning.query import TripQuery
from app.planning.validate import validate_plan
from app.tools.search import search_hotels, search_transfers, search_transport

D = date


@pytest.fixture(scope="session")
def cat() -> Catalog:
    return Catalog.load()


def test_seed_loads_and_is_small(cat: Catalog) -> None:
    assert len(cat.cities) == 11
    assert {c.id for c in cat.cities.values() if "destination" in c.roles} == {
        "AER", "KGD", "KZN", "IST", "AYT", "SSH", "DXB", "EVN"}
    assert all(6 <= len(cat.hotels_by_city[c]) <= 8 for c in ("AER", "KGD", "KZN", "IST", "AYT", "SSH", "DXB", "EVN"))
    assert len(cat.climate) == 8
    size = sum(p.stat().st_size for p in seed_dir().glob("*.json"))
    assert size < 30_000_000
    assert cat.meta is not None and len(cat.meta.edge_cases) >= 8


def test_references_resolve(cat: Catalog) -> None:
    for c in cat.cities.values():
        assert all(h in cat.hubs for h in c.hub_ids)
    for f in cat.flights.values():
        assert f.from_hub in cat.hubs and f.to_hub in cat.hubs
        assert f.arrive > f.depart
    for t in cat.trains.values():
        assert t.from_hub in cat.hubs and t.to_hub in cat.hubs
    for r in cat.transfers.values():
        assert r.hub_id in cat.hubs and r.hotel_id in cat.hotels


def test_required_trains_exist(cat: Catalog) -> None:
    routes = {(t.origin_city, t.dest_city) for t in cat.trains.values()}
    for pair in [("MOW", "KZN"), ("KZN", "MOW"), ("MOW", "AER"), ("AER", "MOW"),
                 ("LED", "MOW"), ("MOW", "LED"), ("SVX", "KZN"), ("KZN", "SVX")]:
        assert pair in routes


def test_offer_totals_cover_party_and_period(cat: Catalog) -> None:
    party = Party(adults=2, children_ages=[5])
    offers = search_hotels(cat, "AER", D(2026, 11, 10), D(2026, 11, 17), party)
    assert offers
    for o in offers:
        assert o.units == 7
        assert o.total_rub == o.unit_price_rub * o.units * o.quantity + sum(e["price_rub"] for e in o.variant["extras"])
    flights = search_transport(cat, "MOW", "AER", D(2026, 11, 10), party, ("flight",))
    assert flights and all(o.units == 3 for o in flights)


def test_capacity_needs_second_room(cat: Catalog) -> None:
    party = Party(adults=4, children_ages=[8, 10, 12])
    for o in search_hotels(cat, "KZN", D(2026, 11, 13), D(2026, 11, 15), party):
        room = cat.hotels[o.item_id].room(o.variant["room_id"])
        assert o.quantity * room.max_occupancy >= 7
        assert o.quantity * room.max_adults >= 4


def test_sprint1_scenario_moscow_sochi(cat: Catalog) -> None:
    """«Из Москвы в Сочи 10–17 ноября, двое взрослых» -> one feasible bundle."""
    q = TripQuery("MOW", "AER", D(2026, 11, 10), D(2026, 11, 17), Party(adults=2))
    res = build_trip_bundles(cat, q)
    assert 1 <= len(res.bundles) <= 3, res.notes
    assert res.bundles[0].label == "дешевле" and res.bundles[0].total_rub == min(b.total_rub for b in res.bundles)
    b = res.bundles[0]
    assert [o.kind for o in b.items][1:4] == ["transfer", "hotel", "transfer"]
    assert b.items[0].kind in ("flight", "train") and b.items[-1].kind in ("flight", "train")
    assert b.total_rub == sum(o.total_rub for o in b.items)
    assert b.feasible


def test_night_arrival_without_reception_is_blocked(cat: Catalog) -> None:
    party = Party(adults=2)
    night = [o for o in search_transport(cat, "SVX", "AYT", D(2026, 11, 10), party, ("flight",))
             if o.variant["stops"] == 0 and o.times.end.hour < 6]
    assert night, "planted SVX->AYT night arrival is missing"
    out = night[0]
    arrival_day = out.times.end.date()
    assert arrival_day == D(2026, 11, 11)
    no_night = [h for h in cat.hotels_by_city["AYT"] if not h.reception.hours.round_the_clock
                and not h.reception.late_arrival_possible]
    assert no_night
    hotel = search_hotels(cat, "AYT", arrival_day, D(2026, 11, 17), party)
    hotel = next(o for o in hotel if o.item_id == no_night[0].id)
    taxi = search_transfers(cat, out.variant["to_hub"], hotel.item_id, party, out.times.end, "to_hotel")
    checks = validate_plan(cat, [out, taxi[0], hotel], party)
    assert any(c.kind == "late_arrival" and c.status == "block" for c in checks)
    # Same hotel booked from the departure date -> check-in date mismatch is blocked as well.
    wrong = search_hotels(cat, "AYT", D(2026, 11, 10), D(2026, 11, 17), party)
    wrong = next(o for o in wrong if o.item_id == no_night[0].id)
    checks = validate_plan(cat, [out, taxi[0], wrong], party)
    assert any(c.kind == "checkin_date" and c.status == "block" for c in checks)


def test_bundle_skips_blocked_hotel_for_night_arrival(cat: Catalog) -> None:
    q = TripQuery("SVX", "AYT", D(2026, 11, 10), D(2026, 11, 17), Party(adults=2), kinds=("flight",))
    res = build_trip_bundles(cat, q)
    assert res.bundles, res.notes
    assert res.bundles[0].feasible


def test_dubai_with_dog_is_infeasible(cat: Catalog) -> None:
    q = TripQuery("MOW", "DXB", D(2026, 11, 10), D(2026, 11, 17), Party(adults=2, pets=[Pet(weight_kg=7)]))
    res = build_trip_bundles(cat, q)
    assert not res.bundles and res.notes


def test_pets_filter_hotels_and_transfers(cat: Catalog) -> None:
    party = Party(adults=2, pets=[Pet(weight_kg=6)])
    offers = search_hotels(cat, "SSH", D(2026, 11, 10), D(2026, 11, 17), party)
    assert all(cat.hotels[o.item_id].pets.allowed for o in offers)
    assert all(not r.pets_allowed for r in cat.transfers.values() if r.city_id == "SSH")


def test_yerevan_sold_out_dates(cat: Catalog) -> None:
    assert not search_hotels(cat, "EVN", D(2026, 11, 20), D(2026, 11, 23), Party(adults=2))
    assert search_hotels(cat, "EVN", D(2026, 11, 10), D(2026, 11, 13), Party(adults=2))


def test_antalya_closed_hotels_disappear_in_november(cat: Catalog) -> None:
    closed = [h.id for h in cat.hotels_by_city["AYT"] if h.open_periods]
    assert len(closed) == 2
    ids = {o.item_id for o in search_hotels(cat, "AYT", D(2026, 11, 10), D(2026, 11, 17), Party(adults=2))}
    assert ids and not ids & set(closed)


def test_train_only_weekend_to_kazan(cat: Catalog) -> None:
    q = TripQuery("MOW", "KZN", D(2026, 11, 13), D(2026, 11, 15), Party(adults=2), kinds=("train",))
    res = build_trip_bundles(cat, q)
    assert res.bundles, res.notes
    assert {o.kind for o in res.bundles[0].items} == {"train", "transfer", "hotel"}


def test_budget_hard_reports_cheapest(cat: Catalog) -> None:
    q = TripQuery("MOW", "DXB", D(2026, 11, 10), D(2026, 11, 17), Party(adults=3), budget_rub=30_000, budget_hard=True)
    res = build_trip_bundles(cat, q)
    assert not res.bundles
    assert res.cheapest_total_rub and res.cheapest_total_rub > 30_000


def test_out_of_window_has_no_transport(cat: Catalog) -> None:
    assert not search_transport(cat, "MOW", "AER", D(2027, 2, 10), Party(adults=1))


def test_times_are_timezone_aware(cat: Catalog) -> None:
    f = next(iter(cat.flights.values()))
    assert isinstance(f.depart, datetime) and f.depart.utcoffset() is not None
