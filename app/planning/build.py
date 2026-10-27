"""build_bundles: up to three verified bundles — дешевле, удобнее под этого пользователя, гибче отмена.

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
from app.planning.models import CHECK_KIND_RU, Bundle, Check, Claim, bundle_to_wire, rub
from app.planning.query import MEAL_RANK, TripQuery, query_from_requirements
from app.planning.scoring import (
    FLEX_WEIGHT,
    cancel_loss,
    cheapest_phrase,
    comfort_label,
    comfort_penalty,
    comfort_phrase,
    flex_phrase,
    phrase_claim,
)
from app.planning.validate import DEPART_BUFFER_MIN, EXIT_MIN, hub_class, validate_plan
from app.tools.search import search_hotels, search_transfers, search_transport

log = logging.getLogger(__name__)

TOP_TRANSPORT = 12
TOP_HOTELS = 15
WISH_HOTELS = 6


@dataclass
class BuildResult:
    bundles: list[Bundle]
    notes: list[str] = field(default_factory=list)  # why options are missing, for an honest answer
    cheapest_total_rub: int | None = None  # cheapest trip ignoring the budget (for "+N ₽ к бюджету")


def _best_transfer(cat: Catalog, hub: str, hotel_id: str, q: TripQuery, at, direction,
                   prefer_mode: str | None = None) -> Offer | None:
    offers = search_transfers(cat, hub, hotel_id, q.party, at, direction)
    if prefer_mode:
        same = [o for o in offers if o.variant.get("mode") == prefer_mode]
        if same:
            return same[0]
    return offers[0] if offers else None


def _assemble(cat: Catalog, q: TripQuery, out: Offer, hotel: Offer, back: Offer,
              prefer_modes: dict[str, str] | None = None) -> tuple[list[Offer], list[Check]]:
    prefer_modes = prefer_modes or {}
    arr_hub, dep_hub = out.variant["to_hub"], back.variant["from_hub"]
    pickup = out.times.end + timedelta(minutes=EXIT_MIN[hub_class(out)])
    to_hotel = _best_transfer(cat, arr_hub, hotel.item_id, q, pickup, "to_hotel", prefer_modes.get("to_hotel"))
    be_at_hub = back.times.start - timedelta(minutes=DEPART_BUFFER_MIN[hub_class(back)])
    to_hub = _best_transfer(cat, dep_hub, hotel.item_id, q, be_at_hub, "to_hub", prefer_modes.get("to_hub"))
    items = [o for o in (out, to_hotel, hotel, to_hub, back) if o is not None]
    checks = validate_plan(cat, items, q.party, q.budget_rub, q.budget_hard)
    why = "который берёт животное" if q.party.pets else "в это время"
    if to_hotel is None:
        checks.append(Check(kind="transfer_fit", status="block", text=f"Нет трансфера от прибытия до отеля {why}."))
    if to_hub is None:
        checks.append(Check(kind="transfer_fit", status="block", text=f"Нет трансфера из отеля к отправлению {why}."))
    return items, checks


def _tradeoffs(items: list[Offer]) -> list[Claim]:
    out = []
    for o in items:
        ref = [f"offer:{o.offer_id}"]
        if o.kind == "hotel" and not o.variant.get("refundable"):
            out.append(Claim(text=f"Невозвратный тариф отеля: при отмене теряется {rub(o.cancel_policy.penalty_rub)}.",
                             refs=ref))
        bag = o.variant.get("baggage") or {}
        if o.kind == "flight" and bag and not bag.get("checked_pieces"):
            out.append(Claim(text=f"{o.variant['title']}: тариф без багажа, только ручная кладь {bag['cabin_kg']} кг.",
                             refs=ref))
        if o.kind == "flight" and o.variant.get("stops"):
            out.append(Claim(text=f"{o.variant['title']}: пересадка, в пути {o.variant['duration_min'] // 60} ч.",
                             refs=ref))
    return out


def hotel_fits(cat: Catalog, o: Offer, q: TripQuery) -> bool:
    """Stars and meal: a hard requirement filters hotels, a soft one only ranks (see scoring)."""
    return not q.lodging_hard or meets_wish(cat, o, q)


def meets_wish(cat: Catalog, o: Offer, q: TripQuery) -> bool:
    if q.min_stars and (cat.hotels[o.item_id].stars or 0) < q.min_stars:
        return False
    return not q.meal or MEAL_RANK.get(o.variant.get("meal", "RO"), 0) >= MEAL_RANK[q.meal]


def fits_limits(o: Offer, q: TripQuery) -> bool:
    if q.max_stops is not None and o.variant.get("stops", 0) > q.max_stops:
        return False
    return q.max_travel_min is None or o.variant.get("duration_min", 0) <= q.max_travel_min


def limits_text(q: TripQuery) -> str:
    parts = []
    if q.max_stops == 0:
        parts.append("без пересадок")
    if q.max_travel_min is not None:
        parts.append(f"в пути до {q.max_travel_min // 60} ч")
    return ", ".join(parts)


def build_trip_bundles(cat: Catalog, q: TripQuery, variants: int = 3) -> BuildResult:
    """Up to three verified bundles: дешевле / удобнее / гибче отмена (``variants=1``: only the cheapest).

    Soft travel limits are tried first and dropped (with a note) if nothing fits; hard ones never.
    """
    if q.max_stops is None and q.max_travel_min is None:
        return _build(cat, q, limited=False, variants=variants)
    res = _build(cat, q, limited=True, variants=variants)
    if res.bundles or q.limits_hard:
        if not res.bundles:
            res.notes.append(f"Нет вариантов с ограничением «{limits_text(q)}».")
        return res
    relaxed = _build(cat, q, limited=False, variants=variants)
    if relaxed.bundles:
        relaxed.notes.append(f"Вариантов «{limits_text(q)}» нет — показан ближайший без этого ограничения.")
        for b in relaxed.bundles:
            b.risks.append(Claim(text=f"Не выполнено пожелание «{limits_text(q)}».", refs=[f"offer:{b.items[0].offer_id}"]))
    return relaxed


@dataclass
class _Candidate:
    total: int
    items: list[Offer]
    checks: list[Check]

    @property
    def key(self) -> tuple[str, ...]:
        return tuple(o.offer_id for o in self.items)


class _TopK:
    """Best ``k`` distinct candidates by score; ``bound`` is the worst kept score (inf until full)."""

    def __init__(self, k: int):
        self.k = k
        self.items: list[tuple[float, _Candidate]] = []

    def push(self, score: float, cand: _Candidate) -> None:
        if any(c.key == cand.key for _, c in self.items):
            return
        self.items.append((score, cand))
        self.items.sort(key=lambda x: (x[0], x[1].key))
        del self.items[self.k:]

    @property
    def bound(self) -> float:
        return self.items[-1][0] if len(self.items) == self.k else float("inf")

    def first_not_in(self, taken: set[tuple[str, ...]]) -> _Candidate | None:
        return next((c for _, c in self.items if c.key not in taken), None)


def _build(cat: Catalog, q: TripQuery, limited: bool, variants: int = 3) -> BuildResult:
    notes: list[str] = []
    if q.return_date <= q.depart_date:
        return BuildResult([], ["Дата обратной дороги должна быть позже даты отправления."])
    from app.catalog.world_resolver import init_world_catalog
    init_world_catalog(cat)
    from app.catalog.ostrovok_live import ensure_ostrovok_hotels
    ensure_ostrovok_hotels(cat, q.dest_city)
    outs = search_transport(cat, q.origin_city, q.dest_city, q.depart_date, q.party, q.kinds)
    backs = search_transport(cat, q.dest_city, q.origin_city, q.return_date, q.party, q.kinds)
    if limited:
        outs, backs = [o for o in outs if fits_limits(o, q)], [o for o in backs if fits_limits(o, q)]
    outs, backs = outs[:TOP_TRANSPORT], backs[:TOP_TRANSPORT]
    if not outs:
        notes.append(f"Нет рейсов и поездов {q.origin_city}→{q.dest_city} на {q.depart_date:%d.%m} под этот состав.")
    if not backs:
        notes.append(f"Нет рейсов и поездов {q.dest_city}→{q.origin_city} на {q.return_date:%d.%m} под этот состав.")
    if notes:
        return BuildResult([], notes)

    k = 3 if variants > 1 else 1
    tops = {"cheap": _TopK(k)}
    if variants > 1:
        tops["comfort"], tops["flex"] = _TopK(k), _TopK(k)
    tz = ZoneInfo(cat.cities[q.dest_city].timezone)
    hotels_by_checkin: dict[date, list[Offer]] = {}
    cheapest_any: int | None = None
    blocks: Counter[str] = Counter()
    for out in outs:
        # Check-in = local date of reaching the hotel; near midnight the transfer may cross it.
        arrive = out.times.end.astimezone(tz)
        checkins = sorted({arrive.date(), (arrive + timedelta(minutes=EXIT_MIN[hub_class(out)] + 120)).date()})
        for checkin in checkins:
            if checkin not in hotels_by_checkin:
                found = search_hotels(cat, q.dest_city, checkin, q.return_date, q.party) if checkin < q.return_date else []
                found = [h for h in found if hotel_fits(cat, h, q)]
                if variants > 1:
                    cheap_cands = found[:12]
                    comfort_cands = sorted(
                        found,
                        key=lambda o: (
                            -(cat.hotels[o.item_id].stars or 0),
                            -(1 if q.meal and o.variant.get("meal") == q.meal else 0),
                            -(cat.hotels[o.item_id].rating or 0),
                            cat.hotels[o.item_id].dist_center_km,
                            o.total_rub,
                        ),
                    )[:12]
                    flex_cands = sorted(
                        [o for o in found if o.variant.get("refundable")],
                        key=lambda o: (
                            -(cat.hotels[o.item_id].rating or 0),
                            o.total_rub,
                        ),
                    )[:8]
                    seen_off_ids = set()
                    pool = []
                    for o in cheap_cands + comfort_cands + flex_cands:
                        if o.offer_id not in seen_off_ids:
                            seen_off_ids.add(o.offer_id)
                            pool.append(o)
                else:
                    pool = found[:TOP_HOTELS]
                hotels_by_checkin[checkin] = pool
        hotels = [h for c in checkins for h in hotels_by_checkin[c]]
        for hotel in hotels:
            for back in backs:
                # Every objective is the total plus a non-negative penalty: the total is a lower bound.
                if out.total_rub + hotel.total_rub + back.total_rub >= max(t.bound for t in tops.values()):
                    break  # backs are sorted: nothing better further on
                items, checks = _assemble(cat, q, out, hotel, back)
                total = sum(o.total_rub for o in items)
                blocked = [c for c in checks if c.status == "block"]
                if all(c.kind == "budget" for c in blocked):
                    cheapest_any = total if cheapest_any is None else min(cheapest_any, total)
                if blocked:
                    blocks.update(c.kind for c in blocked)
                    continue
                cand = _Candidate(total, items, checks)
                tops["cheap"].push(total, cand)
                if variants > 1:
                    tops["comfort"].push(total + comfort_penalty(cat, items, checks, q.party, q), cand)
                    tops["flex"].push(total + FLEX_WEIGHT * cancel_loss(items), cand)
    if not any(hotels_by_checkin.values()):
        extra = []
        if q.party.pets:
            extra.append("с животным")
        if q.lodging_hard and q.min_stars:
            extra.append(f"от {q.min_stars}★")
        if q.lodging_hard and q.meal:
            extra.append(f"с питанием не хуже {q.meal}")
        notes.append(f"Нет свободных отелей ({cat.cities[q.dest_city].name}) на эти даты под этот состав"
                     + (f" ({', '.join(extra)})." if extra else "."))
    cheapest = tops["cheap"].first_not_in(set())
    if cheapest is None:
        if blocks:
            # Two check-in dates are tried around midnight arrivals; the wrong one always fails "checkin_date".
            kinds = [k for k, _ in blocks.most_common() if k != "checkin_date" or len(blocks) == 1]
            reasons = ", ".join(CHECK_KIND_RU.get(k, k) for k in kinds)
            if q.party.pets and "transfer_fit" in kinds:
                reasons += "; ни один трансфер здесь не берёт животное"
            notes.append(f"Ни одно сочетание транспорта и отеля не прошло проверку ({reasons}).")
        return BuildResult([], notes, cheapest_total_rub=cheapest_any)

    bundles = [_bundle("b1", "дешевле", cheapest, cheapest_phrase(cheapest.items))]
    taken = {cheapest.key}
    if variants > 1:
        comfortable = tops["comfort"].first_not_in(taken)
        if comfortable is not None:
            label = comfort_label(q.party, q.max_stops == 0, q)
            bundles.append(_bundle("b2", label, comfortable, comfort_phrase(cat, comfortable.items, cheapest.items)))
            taken.add(comfortable.key)
        flexible = tops["flex"].first_not_in(taken)
        if flexible is not None and cancel_loss(flexible.items) < cancel_loss(cheapest.items):
            bundles.append(_bundle(f"b{len(bundles) + 1}", "гибче отмена", flexible,
                                   flex_phrase(flexible.items, cheapest.items)))
    return BuildResult(bundles, notes, cheapest_total_rub=cheapest.total)


def _bundle(bundle_id: str, label: str, cand: _Candidate, phrase: str) -> Bundle:
    return Bundle(
        id=bundle_id,
        label=label,
        items=cand.items,
        total_rub=cand.total,
        checks=cand.checks,
        tradeoffs=[phrase_claim(phrase, cand.items)] + _tradeoffs(cand.items),
        risks=[Claim(text=c.text, refs=c.refs) for c in cand.checks if c.status == "warn" and c.kind != "visa"],
    )


def build_bundles(requirements: dict[str, Any], state: Any = None) -> list[dict[str, Any]] | Any:
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
    result = build_trip_bundles(cat, query, variants=1)
    notes = list(result.notes)
    if query.budget_rub is not None and result.cheapest_total_rub and result.cheapest_total_rub > query.budget_rub:
        diff = result.cheapest_total_rub - query.budget_rub
        notes.append(f"Самый дешёвый вариант — {rub(result.cheapest_total_rub)}, это на {rub(diff)} больше бюджета.")
    return notes
