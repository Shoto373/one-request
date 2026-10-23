"""build_bundles v0: one bundle — the cheapest feasible trip (no ``block`` checks).

Trip = outbound transport -> transfer -> hotel -> transfer -> return transport.
The hotel stay starts on the local arrival date (a night flight shifts check-in)
and ends on the local date of the return departure.

Two entry points:
- ``build_trip_bundles(cat, TripQuery) -> BuildResult`` — typed core with ``notes``;
- ``build_bundles(requirements, state) -> list[dict]`` — the orchestrator contract
  (track B, README «Integration»): TripState requirements in, wire bundles out.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.catalog.models import Offer
from app.catalog.store import Catalog, get_catalog
from app.planning.models import Bundle, Check, Claim, bundle_to_wire
from app.planning.query import TripQuery, query_from_requirements
from app.planning.validate import DEPART_BUFFER_MIN, EXIT_MIN, hub_class, validate_plan
from app.tools.search import search_hotels, search_transfers, search_transport

log = logging.getLogger(__name__)

TOP_TRANSPORT = 12
TOP_HOTELS = 15


@dataclass
class BuildResult:
    bundles: list[Bundle]
    notes: list[str] = field(default_factory=list)  # why options are missing, for an honest answer
    cheapest_total_rub: int | None = None  # cheapest trip ignoring the budget (for "+N ₽ к бюджету")


def _best_transfer(cat: Catalog, hub: str, hotel_id: str, q: TripQuery, at, direction) -> Offer | None:
    offers = search_transfers(cat, hub, hotel_id, q.party, at, direction)
    return offers[0] if offers else None


def _assemble(cat: Catalog, q: TripQuery, out: Offer, hotel: Offer, back: Offer) -> tuple[list[Offer], list[Check]]:
    arr_hub, dep_hub = out.variant["to_hub"], back.variant["from_hub"]
    pickup = out.times.end + timedelta(minutes=EXIT_MIN[hub_class(out)])
    to_hotel = _best_transfer(cat, arr_hub, hotel.item_id, q, pickup, "to_hotel")
    be_at_hub = back.times.start - timedelta(minutes=DEPART_BUFFER_MIN[hub_class(back)])
    to_hub = _best_transfer(cat, dep_hub, hotel.item_id, q, be_at_hub, "to_hub")
    items = [o for o in (out, to_hotel, hotel, to_hub, back) if o is not None]
    checks = validate_plan(cat, items, q.party, q.budget_rub, q.budget_hard)
    if to_hotel is None:
        checks.append(Check(kind="transfer_fit", status="block", text="Нет трансфера от прибытия до отеля в это время."))
    if to_hub is None:
        checks.append(Check(kind="transfer_fit", status="block", text="Нет трансфера из отеля к отправлению в это время."))
    return items, checks


def _tradeoffs(items: list[Offer]) -> list[Claim]:
    out = []
    for o in items:
        ref = [f"offer:{o.offer_id}"]
        if o.kind == "hotel" and not o.variant.get("refundable"):
            out.append(Claim(text=f"Невозвратный тариф отеля: при отмене теряется {o.cancel_policy.penalty_rub:,} ₽."
                             .replace(",", " "), refs=ref))
        bag = o.variant.get("baggage") or {}
        if o.kind == "flight" and bag and not bag.get("checked_pieces"):
            out.append(Claim(text=f"{o.variant['title']}: тариф без багажа, только ручная кладь {bag['cabin_kg']} кг.",
                             refs=ref))
        if o.kind == "flight" and o.variant.get("stops"):
            out.append(Claim(text=f"{o.variant['title']}: пересадка, в пути {o.variant['duration_min'] // 60} ч.",
                             refs=ref))
    return out


def build_trip_bundles(cat: Catalog, q: TripQuery) -> BuildResult:
    notes: list[str] = []
    if q.return_date <= q.depart_date:
        return BuildResult([], ["Дата обратной дороги должна быть позже даты отправления."])
    outs = search_transport(cat, q.origin_city, q.dest_city, q.depart_date, q.party, q.kinds)[:TOP_TRANSPORT]
    backs = search_transport(cat, q.dest_city, q.origin_city, q.return_date, q.party, q.kinds)[:TOP_TRANSPORT]
    if not outs:
        notes.append(f"Нет рейсов и поездов {q.origin_city}→{q.dest_city} на {q.depart_date:%d.%m} под этот состав.")
    if not backs:
        notes.append(f"Нет рейсов и поездов {q.dest_city}→{q.origin_city} на {q.return_date:%d.%m} под этот состав.")
    if notes:
        return BuildResult([], notes)

    tz = ZoneInfo(cat.cities[q.dest_city].timezone)
    hotels_by_checkin: dict[date, list[Offer]] = {}
    best: tuple[int, list[Offer], list[Check]] | None = None
    cheapest_any: int | None = None
    blocks: Counter[str] = Counter()
    for out in outs:
        # Check-in = local date of reaching the hotel; near midnight the transfer may cross it.
        arrive = out.times.end.astimezone(tz)
        checkins = sorted({arrive.date(), (arrive + timedelta(minutes=EXIT_MIN[hub_class(out)] + 120)).date()})
        for checkin in checkins:
            if checkin not in hotels_by_checkin:
                hotels_by_checkin[checkin] = (
                    search_hotels(cat, q.dest_city, checkin, q.return_date, q.party)[:TOP_HOTELS]
                    if checkin < q.return_date else []
                )
        hotels = [h for c in checkins for h in hotels_by_checkin[c]]
        for hotel in hotels:
            for back in backs:
                if best and out.total_rub + hotel.total_rub + back.total_rub >= best[0]:
                    break  # backs are sorted: nothing cheaper further on
                items, checks = _assemble(cat, q, out, hotel, back)
                total = sum(o.total_rub for o in items)
                blocked = [c for c in checks if c.status == "block"]
                if all(c.kind == "budget" for c in blocked):
                    cheapest_any = total if cheapest_any is None else min(cheapest_any, total)
                if blocked:
                    blocks.update(c.kind for c in blocked)
                    continue
                if best is None or total < best[0]:
                    best = (total, items, checks)
    if not any(hotels_by_checkin.values()):
        notes.append(f"Нет свободных отелей ({cat.cities[q.dest_city].name}) на эти даты под этот состав"
                     + (" с животным." if q.party.pets else "."))
    if best is None:
        if blocks:
            notes.append("Все сочетания не прошли проверку: " + ", ".join(f"{k} ×{n}" for k, n in blocks.most_common()))
        return BuildResult([], notes, cheapest_total_rub=cheapest_any)

    total, items, checks = best
    bundle = Bundle(
        id="b1",
        label="дешевле",
        items=items,
        total_rub=total,
        checks=checks,
        tradeoffs=_tradeoffs(items),
        risks=[Claim(text=c.text, refs=c.refs) for c in checks if c.status == "warn" and c.kind != "visa"],
    )
    return BuildResult([bundle], notes, cheapest_total_rub=total)


def build_bundles(requirements: dict[str, Any], state: Any = None) -> list[dict[str, Any]]:
    """Orchestrator contract: ``{key: value}`` requirements (+ TripState for kinds) -> wire bundles.

    Returns ``[]`` when the request cannot be resolved or nothing is feasible; the reasons
    are logged (and available from ``build_trip_bundles`` / ``explain_no_bundles``).
    """
    cat = get_catalog()
    query, problems = query_from_requirements(cat, requirements, state)
    if query is None:
        log.info("build_bundles: request not resolved: %s", problems)
        return []
    result = build_trip_bundles(cat, query)
    if not result.bundles:
        log.info("build_bundles: no feasible bundle: %s", result.notes)
    return [bundle_to_wire(b) for b in result.bundles]


def explain_no_bundles(requirements: dict[str, Any], state: Any = None) -> list[str]:
    """Human-readable reasons why ``build_bundles`` returned nothing (for an honest reply)."""
    cat = get_catalog()
    query, problems = query_from_requirements(cat, requirements, state)
    if query is None:
        return problems
    result = build_trip_bundles(cat, query)
    notes = list(result.notes)
    if query.budget_rub is not None and result.cheapest_total_rub and result.cheapest_total_rub > query.budget_rub:
        diff = result.cheapest_total_rub - query.budget_rub
        notes.append(f"Самый дешёвый вариант — {result.cheapest_total_rub:,} ₽, это на {diff:,} ₽ больше бюджета."
                     .replace(",", " "))
    return notes
