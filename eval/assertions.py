"""Independent scenario assertions over an order snapshot (sprint 4, track A, A2).

These checks deliberately do not call ``validate_plan``, the search tools or any other
``app.planning`` code: they re-derive facts from the raw catalog and the snapshot, so a bug in
the planner cannot hide itself. Input is the snapshot the cart/order stores
(``{bundle, requirements}``) or, before checkout, the selected bundle with the state requirements.

Each assertion returns ``(passed, detail)``; ``None`` as ``passed`` means "not applicable".
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.catalog.store import Catalog, get_catalog

Result = tuple[bool | None, str]
DEPART_BUFFER_MIN = {"airport_intl": 120, "airport_dom": 90, "station": 40}


# --------------------------------------------------------------------------- snapshot access


class Snapshot:
    """Bundle + requirements as stored in a cart/order snapshot or a TripState."""

    def __init__(self, bundle: dict[str, Any] | None, requirements: list[dict[str, Any]] | dict[str, Any],
                 state: dict[str, Any] | None = None, order: dict[str, Any] | None = None):
        self.bundle = bundle or {}
        if isinstance(requirements, dict):
            requirements = [{"key": k, "value": v, "kind": "hard"} for k, v in requirements.items()]
        self.reqs = {r["key"]: r for r in requirements or [] if r.get("kind") != "unknown"}
        self.state = state or {}
        self.order = order

    @classmethod
    def from_state(cls, state: dict[str, Any], order: dict[str, Any] | None = None) -> Snapshot:
        if order and isinstance(order.get("snapshot"), dict):
            snap = order["snapshot"]
            return cls(snap.get("bundle"), snap.get("requirements", []), state, order)
        bundles = state.get("bundles") or []
        selected = state.get("selected_bundle_id")
        bundle = next((b for b in bundles if b.get("id") == selected), bundles[0] if bundles else None)
        return cls(bundle, state.get("requirements", []), state)

    @property
    def items(self) -> list[dict[str, Any]]:
        return self.bundle.get("items", [])

    def value(self, *keys: str) -> Any:
        for k in keys:
            if k in self.reqs and self.reqs[k].get("value") not in (None, ""):
                return self.reqs[k]["value"]
        return None

    def kind(self, key: str) -> str | None:
        return self.reqs.get(key, {}).get("kind")

    # -- party, independent of app.planning.query
    @property
    def adults(self) -> int:
        v = self.value("adults")
        return int(v) if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()) else 1

    @property
    def children_ages(self) -> list[int]:
        v = self.value("children_ages")
        if isinstance(v, list):
            return [int(x.get("age", 7)) if isinstance(x, dict) else int(x) for x in v]
        if isinstance(v, (int, float)):
            return [int(v)]
        count = self.value("children_count")
        return [7] * int(count) if isinstance(count, (int, float)) else []

    @property
    def has_pet(self) -> bool:
        v = self.value("pets")
        return bool(v) and v not in ("нет", "no", False, 0)

    @property
    def pet_kg(self) -> float:
        w = self.value("pet_weight_kg")
        return float(w) if isinstance(w, (int, float)) else 10.0

    @property
    def dates(self) -> tuple[date, date] | None:
        v = self.value("dates")
        if isinstance(v, dict) and v.get("start") and v.get("end"):
            return date.fromisoformat(str(v["start"])[:10]), date.fromisoformat(str(v["end"])[:10])
        return None


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _by_kind(snap: Snapshot, *kinds: str) -> list[dict[str, Any]]:
    return [o for o in snap.items if o.get("kind") in kinds]


def _transport(snap: Snapshot) -> list[dict[str, Any]]:
    return sorted(_by_kind(snap, "flight", "train"), key=lambda o: o["times"]["start"])


def _hub_class(cat: Catalog, o: dict[str, Any]) -> str:
    if o["kind"] == "train":
        return "station"
    f = cat.flights.get(o["item_id"])
    return "airport_intl" if f and f.international else "airport_dom"


# --------------------------------------------------------------------------- assertions


def snapshot_total_matches_items(snap: Snapshot, cat: Catalog) -> Result:
    if not snap.items:
        return None, "нет комплекта"
    bad = []
    for o in snap.items:
        extras = sum(int(e.get("price_rub", 0)) for e in (o.get("variant") or {}).get("extras", []))
        if o["unit_price_rub"] * o["units"] * o["quantity"] + extras != o["total_rub"]:
            bad.append(o["offer_id"])
    total = sum(o["total_rub"] for o in snap.items)
    ok = not bad and total == snap.bundle.get("total_rub")
    if snap.order is not None:
        ok = ok and snap.order.get("total_rub") == total
    return ok, f"items={total}, bundle={snap.bundle.get('total_rub')}, bad={bad}"


def snapshot_within_hard_budget(snap: Snapshot, cat: Catalog) -> Result:
    limit, key = snap.value("budget_total_rub"), "budget_total_rub"
    if limit is None and snap.value("budget_per_person_rub") is not None:
        limit, key = snap.value("budget_per_person_rub") * (snap.adults + len(snap.children_ages)), "budget_per_person_rub"
    if limit is None or snap.kind(key) != "hard" or not snap.items:
        return None, "нет жёсткого бюджета"
    total = sum(o["total_rub"] for o in snap.items)
    return total <= int(limit), f"total={total}, hard limit={limit}"


def offers_exist_in_catalog(snap: Snapshot, cat: Catalog) -> Result:
    index = {"flight": cat.flights, "train": cat.trains, "hotel": cat.hotels, "transfer": cat.transfers}
    missing = [o["item_id"] for o in snap.items if o["item_id"] not in index.get(o["kind"], {})]
    return (not missing) if snap.items else None, f"missing={missing}"


def prices_recomputed_from_catalog(snap: Snapshot, cat: Catalog) -> Result:
    """Unit prices re-read from raw catalog objects: fares, car classes, transfer tariffs, hotel nights."""
    if not snap.items:
        return None, "нет комплекта"
    bad = []
    for o in snap.items:
        v = o.get("variant") or {}
        if o["kind"] == "flight":
            fare = next((f for f in cat.flights[o["item_id"]].fares if f.code == v.get("fare")), None)
            ok = fare is not None and fare.price_rub == o["unit_price_rub"]
        elif o["kind"] == "train":
            car = next((c for c in cat.trains[o["item_id"]].cars if c.car_class == v.get("car_class")), None)
            ok = car is not None and car.price_rub == o["unit_price_rub"]
        elif o["kind"] == "transfer":
            ok = cat.transfers[o["item_id"]].price_rub == o["unit_price_rub"]
        else:
            h = cat.hotels[o["item_id"]]
            room = next((r for r in h.rooms if r.id == v.get("room_id")), None)
            tariff = next((t for t in h.tariffs if t.id == v.get("tariff_id")), None)
            ok = room is not None and tariff is not None
            if ok:
                start = _dt(o["times"]["start"]).date()
                nightly = []
                for i in range(o["units"]):
                    d = start + timedelta(days=i)
                    p = room.base_price_rub * h.month_factors[d.month - 1] * tariff.price_factor
                    if d.weekday() in (4, 5):
                        p *= h.weekend_factor
                    nightly.append(int(round(p / 10) * 10))
                ok = abs(sum(nightly) / len(nightly) - o["unit_price_rub"]) <= 1
        if not ok:
            bad.append(o["offer_id"])
    return not bad, f"bad={bad}"


def party_in_every_offer(snap: Snapshot, cat: Catalog) -> Result:
    """Each offer is priced for the whole party: seats, rooms (by catalog capacity), vehicles."""
    if not snap.items:
        return None, "нет комплекта"
    adults, ages = snap.adults, snap.children_ages
    people = adults + len(ages)
    bags = adults + sum(1 for a in ages if a >= 2)
    bad = []
    for o in snap.items:
        cap = o.get("capacity") or {}
        if cap.get("adults") != adults or cap.get("children") != len(ages):
            bad.append(f"{o['offer_id']}: состав {cap}")
            continue
        if o["kind"] == "flight":
            free = cat.flights[o["item_id"]].infant_free_age
            if o["units"] != adults + sum(1 for a in ages if a >= free):
                bad.append(f"{o['offer_id']}: мест {o['units']}")
        elif o["kind"] == "train":
            free = cat.trains[o["item_id"]].child_free_age
            if o["units"] != adults + sum(1 for a in ages if a >= free):
                bad.append(f"{o['offer_id']}: мест {o['units']}")
        elif o["kind"] == "hotel":
            h = cat.hotels[o["item_id"]]
            room = next((r for r in h.rooms if r.id == (o.get("variant") or {}).get("room_id")), None)
            with_bed = adults + sum(1 for a in ages if a >= 2)
            q = o["quantity"]
            if room is None or adults > room.max_adults * q or len(ages) > room.max_children * q \
                    or with_bed > room.max_occupancy * q:
                bad.append(f"{o['offer_id']}: номер не вмещает {people}")
        elif o["kind"] == "transfer":
            r = cat.transfers[o["item_id"]]
            if r.price_unit == "per_vehicle" and (people > r.capacity_pax * o["quantity"]
                                                 or bags > r.capacity_bags * o["quantity"]):
                bad.append(f"{o['offer_id']}: машина не вмещает")
    return not bad, "; ".join(bad)


def dates_match_requirements(snap: Snapshot, cat: Catalog) -> Result:
    """Departure on the start date, return on the end date (local), hotel nights cover the stay."""
    dates = snap.dates
    tr = _transport(snap)
    if dates is None or not tr:
        return None, "нет точных дат"
    start, end = dates
    out, back = tr[0], tr[-1]
    origin_tz = cat.cities[out["variant"]["origin_city"]].timezone
    dest_tz = cat.cities[out["variant"]["dest_city"]].timezone
    out_day = _dt(out["times"]["start"]).astimezone(ZoneInfo(origin_tz)).date()
    back_day = _dt(back["times"]["start"]).astimezone(ZoneInfo(dest_tz)).date() if len(tr) > 1 else None
    hotel = next(iter(_by_kind(snap, "hotel")), None)
    problems = []
    if out_day != start:
        problems.append(f"вылет {out_day} ≠ {start}")
    if back_day is not None and back_day != end:
        problems.append(f"обратно {back_day} ≠ {end}")
    if hotel:
        arrival = _dt(out["times"]["end"]).astimezone(ZoneInfo(dest_tz))
        to_hotel = next((o for o in _by_kind(snap, "transfer") if (o.get("variant") or {}).get("direction") == "to_hotel"), None)
        if to_hotel:
            arrival = _dt(to_hotel["times"]["end"]).astimezone(ZoneInfo(dest_tz))
        checkin = _dt(hotel["times"]["start"]).astimezone(ZoneInfo(dest_tz)).date()
        checkout = _dt(hotel["times"]["end"]).astimezone(ZoneInfo(dest_tz)).date()
        if checkin != arrival.date():
            problems.append(f"заезд {checkin} ≠ дата прибытия {arrival.date()}")
        if back_day is not None and checkout != back_day:
            problems.append(f"выезд {checkout} ≠ дата обратной дороги {back_day}")
        if (checkout - checkin).days != hotel["units"]:
            problems.append("число ночей не совпадает с датами отеля")
    return not problems, "; ".join(problems)


def no_stale_offers(snap: Snapshot, cat: Catalog) -> Result:
    """After a date change nothing from the old dates survives: every item lies inside the new trip."""
    dates = snap.dates
    if dates is None or not snap.items:
        return None, "нет точных дат"
    lo, hi = dates[0] - timedelta(days=0), dates[1] + timedelta(days=2)
    stale = [o["offer_id"] for o in snap.items if not (lo <= _dt(o["times"]["start"]).date() <= hi)]
    return not stale, f"stale={stale}"


def pet_allowed_everywhere(snap: Snapshot, cat: Catalog) -> Result:
    """Carrier, hotel and transfer rules re-read from the catalog for the declared pet weight."""
    if not snap.has_pet:
        return None, "без животного"
    if not snap.items:
        return None, "нет комплекта"
    kg, bad = snap.pet_kg, []
    for o in snap.items:
        v = o.get("variant") or {}
        if o["kind"] == "flight":
            p = cat.flights[o["item_id"]].pets
            if not ((p.cabin and p.cabin_max_kg and kg <= p.cabin_max_kg) or p.hold):
                bad.append(f"{o['offer_id']}: перевозчик")
        elif o["kind"] == "train":
            car = next((c for c in cat.trains[o["item_id"]].cars if c.car_class == v.get("car_class")), None)
            if car is None or not car.pets_allowed:
                bad.append(f"{o['offer_id']}: вагон")
        elif o["kind"] == "hotel":
            h = cat.hotels[o["item_id"]]
            if not h.pets.allowed or (h.pets.max_weight_kg is not None and kg > h.pets.max_weight_kg):
                bad.append(f"{o['offer_id']}: отель")
        elif o["kind"] == "transfer" and not cat.transfers[o["item_id"]].pets_allowed:
            bad.append(f"{o['offer_id']}: трансфер")
    return not bad, "; ".join(bad)


def transport_kind_respected(snap: Snapshot, cat: Catalog) -> Result:
    want = str(snap.value("transport") or "").lower()
    if snap.kind("transport") != "hard" or not snap.items or want in ("", "either", "any", "любой"):
        return None, "нет жёсткого транспорта"
    kinds = {o["kind"] for o in _transport(snap)}
    expected = "train" if any(w in want for w in ("train", "поезд", "жд")) else "flight"
    return kinds == {expected}, f"transport={sorted(kinds)}, expected={expected}"


def night_arrival_has_reception(snap: Snapshot, cat: Catalog) -> Result:
    """Arrival at the hotel while the desk is closed only with a keybox/late arrival option."""
    hotel = next(iter(_by_kind(snap, "hotel")), None)
    to_hotel = next((o for o in _by_kind(snap, "transfer") if (o.get("variant") or {}).get("direction") == "to_hotel"), None)
    if hotel is None or to_hotel is None:
        return None, "нет отеля или трансфера"
    h = cat.hotels[hotel["item_id"]]
    tz = cat.cities[h.city_id].timezone
    at = _dt(to_hotel["times"]["end"]).astimezone(ZoneInfo(tz)).time()
    hours = h.reception.hours
    if hours.round_the_clock:
        return True, "круглосуточная стойка"
    is_open = hours.opens <= at < hours.closes if hours.opens <= hours.closes else (at >= hours.opens or at < hours.closes)
    return is_open or h.reception.late_arrival_possible, f"прибытие {at:%H:%M}, стойка {hours.opens}–{hours.closes}"


def return_transfer_in_time(snap: Snapshot, cat: Catalog) -> Result:
    tr = _transport(snap)
    to_hub = next((o for o in _by_kind(snap, "transfer") if (o.get("variant") or {}).get("direction") == "to_hub"), None)
    if len(tr) < 2 or to_hub is None:
        return None, "нет обратной дороги или трансфера"
    back = tr[-1]
    latest = _dt(back["times"]["start"]) - timedelta(minutes=DEPART_BUFFER_MIN[_hub_class(cat, back)])
    return _dt(to_hub["times"]["end"]) <= latest, f"трансфер до {to_hub['times']['end']}, нужно до {latest.isoformat()}"


def lodging_wish_respected(snap: Snapshot, cat: Catalog) -> Result:
    hotel = next(iter(_by_kind(snap, "hotel")), None)
    stars, meal = snap.value("lodging_stars"), snap.value("meal_preference")
    hard_stars = snap.kind("lodging_stars") == "hard" and stars is not None
    hard_meal = snap.kind("meal_preference") == "hard" and meal is not None
    if hotel is None or not (hard_stars or hard_meal):
        return None, "нет жёстких требований к отелю"
    h = cat.hotels[hotel["item_id"]]
    ok = True
    if hard_stars:
        ok = ok and (h.stars or 0) >= int(stars)
    if hard_meal and any(w in str(meal).lower() for w in ("all", "всё", "все")):
        ok = ok and (hotel.get("variant") or {}).get("meal") == "AI"
    return ok, f"stars={h.stars}, meal={(hotel.get('variant') or {}).get('meal')}"


def honest_no_bundle(snap: Snapshot, cat: Catalog) -> Result:
    """Impossible request: no bundle at all and either confirmed changes or a clear note."""
    state = snap.state
    no_bundles = not state.get("bundles")
    changes = state.get("proposed_changes") or []
    confirmed = all(c.get("confirmed") and c.get("refs") for c in changes)
    return no_bundles and confirmed, f"bundles={len(state.get('bundles') or [])}, changes={len(changes)}"


def order_snapshot_frozen(snap: Snapshot, cat: Catalog) -> Result:
    if snap.order is None:
        return None, "заказа нет"
    o = snap.order
    s = o.get("snapshot") or {}
    total = sum(i["total_rub"] for i in (s.get("bundle") or {}).get("items", []))
    return o.get("status") == "paid" and o.get("total_rub") == total == (s.get("bundle") or {}).get("total_rub"), \
        f"status={o.get('status')}, order={o.get('total_rub')}, items={total}"


def single_order_on_double_pay(snap: Snapshot, cat: Catalog) -> Result:
    """Paying twice (same key, new key) returns the first order again: exactly one order exists."""
    info = (snap.order or {}).get("eval_double_pay")
    if not info:
        return None, "повторной оплаты не было"
    first = info.get("first_order_id")
    ids = info.get("repeat_order_ids") or []
    return bool(first) and all(i == first for i in ids) and all(info.get("repeat_replay") or [False]), \
        f"first={first}, repeats={ids}"


CHECKS: dict[str, Callable[[Snapshot, Catalog], Result]] = {
    "snapshot_total_matches_items": snapshot_total_matches_items,
    "snapshot_within_hard_budget": snapshot_within_hard_budget,
    "offers_exist_in_catalog": offers_exist_in_catalog,
    "prices_recomputed_from_catalog": prices_recomputed_from_catalog,
    "party_in_every_offer": party_in_every_offer,
    "dates_match_requirements": dates_match_requirements,
    "no_stale_offers": no_stale_offers,
    "pet_allowed_everywhere": pet_allowed_everywhere,
    "transport_kind_respected": transport_kind_respected,
    "night_arrival_has_reception": night_arrival_has_reception,
    "return_transfer_in_time": return_transfer_in_time,
    "lodging_wish_respected": lodging_wish_respected,
    "honest_no_bundle": honest_no_bundle,
    "order_snapshot_frozen": order_snapshot_frozen,
    "single_order_on_double_pay": single_order_on_double_pay,
}


def run_check(name: str, state: dict[str, Any], order: dict[str, Any] | None = None,
              cat: Catalog | None = None) -> dict[str, str]:
    """Runner-facing entry: ``{name, status: passed|failed|skipped, detail}``."""
    snap = Snapshot.from_state(state, order)
    passed, detail = CHECKS[name](snap, cat or get_catalog())
    status = "skipped" if passed is None else "passed" if passed else "failed"
    return {"name": name, "status": status, "detail": detail}
