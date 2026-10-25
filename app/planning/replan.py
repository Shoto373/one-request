"""replan (sprint 3, A3, architecture section 8): re-check every dependent item, replace only failures.

Dependency graph: dates -> transport, hotel, transfers; party -> rooms, seats, transfers, price;
pet -> carrier, hotel, transfer; budget -> everything; destination -> everything.
Every item of the old bundle is re-priced and re-validated under the new conditions; keeping an
item is the result of that check, not an assumption. Replacements prefer the user's previous
choice: the same flight number or train on the new date, the same hotel/room/tariff, the same
transfer mode. Among feasible plans the one with the fewest replacements wins, then the cheapest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.catalog.models import Offer, Party
from app.catalog.store import Catalog, get_catalog
from app.planning.build import _assemble, fits_limits, hotel_fits
from app.planning.models import Bundle, Check, Claim, bundle_to_wire, rub
from app.planning.query import TripQuery, query_from_requirements
from app.planning.validate import EXIT_MIN, hub_class, split_legs
from app.tools.search import hotel_available, hotel_accepts_pets, rooms_needed, search_hotels, search_transport

TOP_TRANSPORT = 6
TOP_HOTELS = 8
LEG_WEIGHT = {"outbound": 1.0, "hotel": 1.0, "inbound": 1.0, "to_hotel": 0.5, "to_hub": 0.5}


@dataclass
class ReplanResult:
    bundle: Bundle | None
    kept: list[dict[str, Any]] = field(default_factory=list)
    replaced: list[dict[str, Any]] = field(default_factory=list)
    price_delta_rub: int | None = None
    change: str = ""
    notes: list[str] = field(default_factory=list)

    def history_entry(self) -> dict[str, Any]:
        """Shape of TripState.history items (B fills ``version``)."""
        return {
            "change": self.change,
            "kept": [k["title"] for k in self.kept],
            "replaced": [f"{r['from']['title']} → {r['to']['title'] if r['to'] else '—'} ({r['why']})" for r in self.replaced],
            "price_delta_rub": self.price_delta_rub,
        }

    def to_wire(self) -> dict[str, Any]:
        return {
            "bundle": bundle_to_wire(self.bundle) if self.bundle else None,
            "kept": self.kept,
            "replaced": self.replaced,
            "price_delta_rub": self.price_delta_rub,
            "change": self.change,
            "notes": self.notes,
            "history_entry": self.history_entry(),
        }


# --------------------------------------------------------------------------- old conditions


@dataclass
class OldTrip:
    legs: Any
    origin: str
    dest: str
    depart: date
    ret: date
    adults: int
    children: int
    pets: bool


def old_trip(cat: Catalog, items: list[Offer]) -> OldTrip:
    legs = split_legs(items)
    out, back = legs.outbound, legs.inbound
    origin = out.variant["origin_city"]
    dest = out.variant["dest_city"]
    cap = next((o.capacity for o in items if o.capacity), None)
    return OldTrip(
        legs=legs, origin=origin, dest=dest,
        depart=out.times.start.astimezone(ZoneInfo(cat.cities[origin].timezone)).date(),
        ret=back.times.start.astimezone(ZoneInfo(cat.cities[dest].timezone)).date() if back else out.times.end.date(),
        adults=cap.adults if cap else 1, children=cap.children if cap else 0, pets=bool(cap and cap.pets),
    )


def describe_change(cat: Catalog, old: OldTrip, q: TripQuery) -> str:
    parts = []
    if (q.origin_city, q.dest_city) != (old.origin, old.dest):
        parts.append(f"направление: {cat.cities[old.dest].name} → {cat.cities[q.dest_city].name}")
    shift = (q.depart_date - old.depart).days
    nights_old, nights_new = (old.ret - old.depart).days, (q.return_date - q.depart_date).days
    if shift:
        parts.append(f"даты на {abs(shift)} дн. {'позже' if shift > 0 else 'раньше'}")
    if nights_new != nights_old:
        parts.append(f"{nights_new} ноч. вместо {nights_old}")
    if q.party.adults != old.adults:
        parts.append(f"взрослых: {old.adults} → {q.party.adults}")
    if q.party.children != old.children:
        parts.append(f"детей: {old.children} → {q.party.children}")
    if bool(q.party.pets) != old.pets:
        parts.append("с животным" if q.party.pets else "без животного")
    if q.budget_rub is not None:
        parts.append(f"бюджет {rub(q.budget_rub)}")
    return ", ".join(parts) or "перепроверка без изменений условий"


# --------------------------------------------------------------------------- preferences


def _minutes(o: Offer, tz: str) -> int:
    t = o.times.start.astimezone(ZoneInfo(tz))
    return t.hour * 60 + t.minute


def _number(o: Offer) -> str | None:
    return o.variant.get("flight_no") or o.variant.get("train_no")


def _fare(o: Offer) -> str | None:
    return o.variant.get("fare") or o.variant.get("car_class")


def transport_rank(o: Offer, old: Offer | None, tz: str) -> tuple:
    if old is None:
        return (3, 0, o.total_rub)
    if _number(o) == _number(old) and _fare(o) == _fare(old):
        level = 0
    elif _number(o) == _number(old):
        level = 1
    elif o.variant.get("carrier") and o.variant.get("carrier") == old.variant.get("carrier"):
        level = 2
    else:
        level = 3
    return (level, abs(_minutes(o, tz) - _minutes(old, tz)), o.total_rub)


def hotel_rank(o: Offer, old: Offer | None) -> tuple:
    if old is None or o.item_id != old.item_id:
        return (3, o.total_rub)
    if o.variant.get("room_id") == old.variant.get("room_id"):
        return (0 if o.variant.get("tariff_id") == old.variant.get("tariff_id") else 1, o.total_rub)
    return (2, o.total_rub)


def _transport_kept(new: Offer, old: Offer | None) -> bool:
    return old is not None and _number(new) == _number(old) and _fare(new) == _fare(old)


def _hotel_kept(new: Offer, old: Offer | None) -> bool:
    return old is not None and hotel_rank(new, old)[0] == 0


def _transfer_kept(new: Offer | None, old: Offer | None) -> bool:
    return bool(new and old and new.item_id == old.item_id)


def _replacements(new: dict[str, Offer | None], old: dict[str, Offer | None]) -> float:
    score = 0.0
    for leg in ("outbound", "inbound"):
        if not _transport_kept(new[leg], old[leg]):
            score += LEG_WEIGHT[leg]
    if not _hotel_kept(new["hotel"], old["hotel"]):
        score += LEG_WEIGHT["hotel"] * (0.5 if old["hotel"] and new["hotel"].item_id == old["hotel"].item_id else 1)
    for leg in ("to_hotel", "to_hub"):
        if not _transfer_kept(new[leg], old[leg]):
            score += LEG_WEIGHT[leg]
    return score


# --------------------------------------------------------------------------- why something was replaced


def _transport_why(cat: Catalog, old: Offer, q: TripQuery, day: date, outbound: bool) -> str:
    number = _number(old)
    origin, dest = (q.origin_city, q.dest_city) if outbound else (q.dest_city, q.origin_city)
    pool = cat.flights_by_route_day.get((origin, dest, day), []) if old.kind == "flight" else \
        cat.trains_by_route_day.get((origin, dest, day), [])
    same = [x for x in pool if (getattr(x, "flight_no", None) or getattr(x, "train_no", None)) == number]
    if not same:
        return f"{number} не {'летает' if old.kind == 'flight' else 'ходит'} {day:%d.%m}"
    obj = same[0]
    if q.party.pets:
        if old.kind == "flight":
            p = obj.pets
            if not all((p.cabin and p.cabin_max_kg and pet.weight_kg <= p.cabin_max_kg) or p.hold for pet in q.party.pets):
                return "перевозчик не берёт животное" + (f": {p.note}" if p.note else "")
        else:
            car = next((c for c in obj.cars if c.car_class == _fare(old)), None)
            if car and not car.pets_allowed:
                return "в этом вагоне животных не перевозят"
    if not fits_limits(old, q) and (q.max_stops is not None or q.max_travel_min is not None):
        return "не подходит под ограничения дороги"
    return "не складывается с остальным планом по времени или цене"


def _hotel_why(cat: Catalog, old: Offer, q: TripQuery, checkin: date) -> str:
    h = cat.hotels.get(old.item_id)
    if h is None or h.city_id != q.dest_city:
        return "другое направление"
    if not hotel_available(h, checkin, q.return_date):
        return f"нет мест или закрыт на {checkin:%d.%m}–{q.return_date:%d.%m}"
    if not hotel_accepts_pets(h, q.party):
        return "отель не принимает животных"
    room = next((r for r in h.rooms if r.id == old.variant.get("room_id")), None)
    if room and (rooms_needed(room, q.party) is None or room.inventory < (rooms_needed(room, q.party) or 99)):
        return f"номер «{room.name}» не вмещает новый состав"
    if q.budget_hard and q.budget_rub is not None:
        return "дороже нового бюджета в общем итоге"
    return "не проходит проверку состыковки"


def _transfer_why(new_hotel_changed: bool, old: Offer, new: Offer | None, q: TripQuery, cat: Catalog) -> str:
    if new_hotel_changed:
        return "отель заменён"
    if new is not None and new.variant.get("hub_id") != old.variant.get("hub_id"):
        return "прибытие в другой аэропорт или вокзал"
    route = cat.transfers.get(old.item_id)
    if route and q.party.pets and not route.pets_allowed:
        return "не берёт животных"
    return "время прибытия изменилось — этот трансфер в новое время не работает или дороже"


def _brief(o: Offer | None) -> dict[str, Any] | None:
    if o is None:
        return None
    return {"offer_id": o.offer_id, "kind": o.kind, "title": o.title or o.item_id,
            "start": o.times.start.isoformat() if o.times else None, "total_rub": o.total_rub}


# --------------------------------------------------------------------------- core


def replan_bundle(cat: Catalog, old_bundle_items: list[Offer], q: TripQuery,
                  bundle_id: str = "b1", label: str = "пересобран") -> ReplanResult:
    old = old_trip(cat, old_bundle_items)
    change = describe_change(cat, old, q)
    same_route = (q.origin_city, q.dest_city) == (old.origin, old.dest)
    o_legs = old.legs
    old_map = {"outbound": o_legs.outbound, "to_hotel": o_legs.to_hotel, "hotel": o_legs.hotel,
               "to_hub": o_legs.to_hub, "inbound": o_legs.inbound}
    if not same_route:
        old_map = {k: None for k in old_map}

    tz_origin = cat.cities[q.origin_city].timezone
    tz_dest = cat.cities[q.dest_city].timezone
    outs = [o for o in search_transport(cat, q.origin_city, q.dest_city, q.depart_date, q.party, q.kinds)
            if q.limits_hard is False or fits_limits(o, q)]
    backs = [o for o in search_transport(cat, q.dest_city, q.origin_city, q.return_date, q.party, q.kinds)
             if q.limits_hard is False or fits_limits(o, q)]
    outs = sorted(outs, key=lambda o: transport_rank(o, old_map["outbound"], tz_origin))[:TOP_TRANSPORT]
    backs = sorted(backs, key=lambda o: transport_rank(o, old_map["inbound"], tz_dest))[:TOP_TRANSPORT]
    prefer = {d: o.variant.get("mode") for d, o in (("to_hotel", old_map["to_hotel"]), ("to_hub", old_map["to_hub"])) if o}

    best: tuple[tuple, list[Offer], list[Check], dict] | None = None
    hotels_cache: dict[date, list[Offer]] = {}
    tz = ZoneInfo(tz_dest)
    for out in outs:
        arrive = out.times.end.astimezone(tz)
        checkins = sorted({arrive.date(), (arrive + timedelta(minutes=EXIT_MIN[hub_class(out)] + 120)).date()})
        hotels: list[Offer] = []
        for checkin in checkins:
            if checkin not in hotels_cache:
                found = search_hotels(cat, q.dest_city, checkin, q.return_date, q.party) if checkin < q.return_date else []
                found = [h for h in found if hotel_fits(cat, h, q)]
                hotels_cache[checkin] = sorted(found, key=lambda o: hotel_rank(o, old_map["hotel"]))[:TOP_HOTELS]
            hotels += hotels_cache[checkin]
        for hotel in hotels:
            for back in backs:
                items, checks = _assemble(cat, q, out, hotel, back, prefer)
                if any(c.status == "block" for c in checks):
                    continue
                legs = split_legs(items)
                new_map = {"outbound": legs.outbound, "to_hotel": legs.to_hotel, "hotel": legs.hotel,
                           "to_hub": legs.to_hub, "inbound": legs.inbound}
                key = (_replacements(new_map, old_map), sum(o.total_rub for o in items))
                if best is None or key < best[0]:
                    best = (key, items, checks, new_map)

    if best is None:
        return ReplanResult(bundle=None, change=change,
                            notes=[f"Под новые условия ({change}) план не собирается."])

    _, items, checks, new_map = best
    total = sum(o.total_rub for o in items)
    old_total = sum(o.total_rub for o in old_bundle_items)
    kept, replaced = [], []
    hotel_changed = not (old_map["hotel"] and new_map["hotel"].item_id == old_map["hotel"].item_id)
    checkin = new_map["hotel"].times.start.astimezone(tz).date()
    for leg in ("outbound", "to_hotel", "hotel", "to_hub", "inbound"):
        o_old, o_new = old_map[leg], new_map[leg]
        if o_old is None and same_route:
            continue
        if leg in ("outbound", "inbound"):
            same = _transport_kept(o_new, o_old)
        elif leg == "hotel":
            same = _hotel_kept(o_new, o_old)
        else:
            same = _transfer_kept(o_new, o_old)
        if same:
            notes = []
            if o_old.times and o_new.times and o_old.times.start.date() != o_new.times.start.date():
                notes.append(f"тот же вариант на новую дату {o_new.times.start:%d.%m}")
            if o_new.quantity != o_old.quantity:
                unit = "номера" if o_new.kind == "hotel" else "машины"
                notes.append(f"{o_new.quantity} {unit} вместо {o_old.quantity}")
            if o_new.total_rub != o_old.total_rub:
                notes.append(f"цена {rub(o_old.total_rub)} → {rub(o_new.total_rub)}")
            kept.append({**_brief(o_new), "note": "; ".join(notes) or "без изменений"})
            continue
        if not same_route:
            why = "другое направление"
        elif leg in ("outbound", "inbound"):
            day = q.depart_date if leg == "outbound" else q.return_date
            why = _transport_why(cat, o_old, q, day, outbound=leg == "outbound")
        elif leg == "hotel":
            why = _hotel_why(cat, o_old, q, checkin)
            if why == "не проходит проверку состыковки" and new_map["hotel"].item_id == o_old.item_id:
                why = "тот же отель, но этот номер или тариф недоступен под новые условия"
        else:
            why = _transfer_why(hotel_changed, o_old, o_new, q, cat)
        replaced.append({"leg": leg, "from": _brief(o_old), "to": _brief(o_new), "why": why})

    risks = [Claim(text=c.text, refs=c.refs) for c in checks if c.status == "warn" and c.kind != "visa"]
    summary = Claim(text=f"Пересборка ({change}): сохранено {len(kept)}, заменено {len(replaced)}, "
                         f"итог {rub(total)} ({'+' if total >= old_total else '−'}{rub(abs(total - old_total))}).",
                    refs=[f"offer:{o.offer_id}" for o in items])
    bundle = Bundle(id=bundle_id, label=label, items=items, total_rub=total, checks=checks,
                    tradeoffs=[summary], risks=risks)
    return ReplanResult(bundle=bundle, kept=kept, replaced=replaced, price_delta_rub=total - old_total, change=change)


# --------------------------------------------------------------------------- orchestrator contract


def _old_bundle(state: Any, bundle_id: str | None) -> dict[str, Any] | None:
    bundles = getattr(state, "bundles", None)
    if bundles is None and isinstance(state, dict):
        bundles = state.get("bundles")
    bundles = [b.model_dump(mode="json") if hasattr(b, "model_dump") else b for b in bundles or []]
    selected = bundle_id or (getattr(state, "selected_bundle_id", None) if not isinstance(state, dict)
                             else state.get("selected_bundle_id"))
    return next((b for b in bundles if b.get("id") == selected), bundles[0] if bundles else None)


def replan(requirements: dict[str, Any], state: Any, bundle_id: str | None = None,
           changes: dict[str, Any] | None = None) -> dict[str, Any] | Any:
    """Rebuild the selected (or given) bundle of ``state`` under the new ``requirements``.

    Returns ``{bundle, kept, replaced, price_delta_rub, change, notes, history_entry, alternatives}``.
    ``changes`` is the complete typed diff from the last successfully selected plan;
    requirements remains the full current requirement set used for all calculations.
    ``bundle`` keeps the old bundle's id and label. When nothing fits, ``bundle`` is None and
    ``alternatives`` holds confirmed minimal changes (same format as suggest_destinations).
    """
    from app.providers.tutu.planner import replan_tutu, tutu_live_enabled
    if tutu_live_enabled():
        return replan_tutu(state, changes or {})
    cat = get_catalog()
    old = _old_bundle(state, bundle_id)
    q, problems = query_from_requirements(cat, requirements, state)
    if q is None:
        return {"bundle": None, "kept": [], "replaced": [], "price_delta_rub": None, "change": "",
                "notes": problems, "history_entry": None, "alternatives": [], "input_changes": changes or {}}
    if old is None or not old.get("items"):
        return {"bundle": None, "kept": [], "replaced": [], "price_delta_rub": None, "change": "",
                "notes": ["Нет выбранного комплекта для пересборки — нужен обычный подбор."],
                "history_entry": None, "alternatives": [], "input_changes": changes or {}}
    items = [Offer.model_validate(i) for i in old["items"]]
    result = replan_bundle(cat, items, q, bundle_id=old.get("id", "b1"), label=old.get("label", "пересобран"))
    out = result.to_wire()
    out["alternatives"] = []
    out["input_changes"] = changes or {}
    if result.bundle is None:
        from app.planning.alternatives import minimal_changes
        from app.planning.suggest import _alternatives

        out["alternatives"] = _alternatives(cat, minimal_changes(cat, q))
    return out
