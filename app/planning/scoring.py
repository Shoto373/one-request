"""Bundle objectives for build_bundles (sprint 3, A1).

Three bundles answer three questions: what is cheapest, what is most comfortable for *this*
party, what is easiest to cancel. Comfort and flexibility are expressed in roubles added to the
price, so all three objectives share one search and one lower bound (the plain total).
"""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo

from app.catalog.models import Offer, Party
from app.catalog.store import Catalog
from app.planning.models import Check, Claim, rub

FLEX_WEIGHT = 0.8  # each rouble lost on cancellation counts as 0.8 rouble of price
UNMET_WISH = 1_000_000  # an unmet stated hotel wish outweighs any price difference
MEAL_RANK = {"RO": 0, "BB": 1, "HB": 2, "FB": 3, "AI": 4}


def _transport(items: list[Offer]) -> list[Offer]:
    return [o for o in items if o.kind in ("flight", "train")]


def comfort_penalty(cat: Catalog, items: list[Offer], checks: list[Check], party: Party, q: Any = None) -> int:
    """Roubles of discomfort for this party: stops, long travel, night hours, waiting, hotel quality."""
    kids = party.children > 0
    people = party.people
    penalty = 0
    for o in _transport(items):
        stops = int(o.variant.get("stops", 0))
        hours = int(o.variant.get("duration_min", 0)) / 60
        penalty += stops * 4000 * people
        penalty += int(max(0.0, hours - 3) * 600 * people)
        city = cat.cities.get(o.variant.get("origin_city", ""))
        if city:
            dep = o.times.start.astimezone(ZoneInfo(city.timezone))
            if dep.hour < 6:
                penalty += 6000 if kids else 3000
        dest = cat.cities.get(o.variant.get("dest_city", ""))
        if dest:
            arr = o.times.end.astimezone(ZoneInfo(dest.timezone))
            if arr.hour < 6:
                penalty += 8000 if kids else 4000
        if o.kind == "train" and o.variant.get("car_class") in ("platzkart", "sitting") and hours > 6:
            penalty += 3000 * (2 if kids else 1)
    for c in checks:
        if c.status == "warn" and c.kind not in ("visa", "budget"):
            penalty += 3500 if kids else 2500
    hotel = next((o for o in items if o.kind == "hotel"), None)
    if hotel is not None:
        h = cat.hotels[hotel.item_id]
        penalty += int(max(0.0, 9.0 - h.rating) * 3000)
        if (h.stars or 3) < 3:
            penalty += 4000
        # Stated stars/meal wishes («желательно 5 звёзд», «лучше всё включено») come first for the
        # comfortable bundle whenever any feasible hotel meets them: a lexicographic, not a price, trade-off.
        if q is not None and q.min_stars and (h.stars or 0) < q.min_stars:
            penalty += UNMET_WISH
        if q is not None and q.meal and MEAL_RANK.get(hotel.variant.get("meal", "RO"), 0) < MEAL_RANK[q.meal]:
            penalty += UNMET_WISH
        if kids:
            if not (h.kids.kids_club or h.kids.kids_pool):
                penalty += 6000
            if hotel.variant.get("meal") == "RO":
                penalty += 4000
    for o in items:
        if o.kind == "transfer" and o.variant.get("mode") == "public":
            penalty += 1500 if (kids or party.bag_count > 2) else 500
    return penalty


def cancel_loss(items: list[Offer]) -> int:
    """Roubles lost if the whole trip is cancelled today."""
    return sum(o.cancel_policy.penalty_rub if o.cancel_policy else o.total_rub for o in items)


def comfort_label(party: Party, prefer_direct: bool, q: Any = None) -> str:
    wishes = []
    if q is not None and q.min_stars:
        wishes.append(f"от {q.min_stars}★")
    if q is not None and q.meal == "AI":
        wishes.append("всё включено")
    if wishes:
        return "удобнее: " + ", ".join(wishes)
    if party.children:
        return "удобнее с ребёнком"
    if party.pets:
        return "удобнее с животным"
    if prefer_direct:
        return "удобнее: без пересадок"
    return "удобнее"


# --------------------------------------------------------------------------- one-phrase trade-offs


def _hotel(items: list[Offer]) -> Offer | None:
    return next((o for o in items if o.kind == "hotel"), None)


def _travel_min(items: list[Offer]) -> int:
    return sum(int(o.variant.get("duration_min", 0)) for o in _transport(items))


def _stops(items: list[Offer]) -> int:
    return sum(int(o.variant.get("stops", 0)) for o in _transport(items))


def _no_bag(items: list[Offer]) -> bool:
    return any(o.kind == "flight" and not (o.variant.get("baggage") or {}).get("checked_pieces") for o in items)


def _night(cat: Catalog, items: list[Offer]) -> bool:
    for o in _transport(items):
        origin, dest = cat.cities.get(o.variant.get("origin_city", "")), cat.cities.get(o.variant.get("dest_city", ""))
        if origin and o.times.start.astimezone(ZoneInfo(origin.timezone)).hour < 6:
            return True
        if dest and o.times.end.astimezone(ZoneInfo(dest.timezone)).hour < 6:
            return True
    return False


def cheapest_phrase(items: list[Offer]) -> str:
    sacrifices = []
    hotel = _hotel(items)
    if hotel is not None and not hotel.variant.get("refundable"):
        sacrifices.append("невозвратный отель")
    if _no_bag(items):
        sacrifices.append("без багажа")
    if _stops(items):
        sacrifices.append("с пересадкой")
    if any(o.kind == "transfer" and o.variant.get("mode") == "public" for o in items):
        sacrifices.append("трансфер общественным транспортом")
    total = sum(o.total_rub for o in items)
    tail = f"; чем жертвуем: {', '.join(sacrifices)}" if sacrifices else "; без заметных компромиссов"
    return f"Самый дешёвый — {rub(total)}{tail}."


def comfort_phrase(cat: Catalog, items: list[Offer], base: list[Offer]) -> str:
    gains = []
    if _stops(items) < _stops(base):
        gains.append("без пересадок")
    saved = _travel_min(base) - _travel_min(items)
    if saved >= 60:
        gains.append(f"в дороге на {saved // 60} ч меньше (туда и обратно)")
    hotel, base_hotel = _hotel(items), _hotel(base)
    if hotel and base_hotel and hotel.item_id != base_hotel.item_id:
        h, bh = cat.hotels[hotel.item_id], cat.hotels[base_hotel.item_id]
        if h.rating > bh.rating:
            gains.append(f"отель с рейтингом {h.rating:g} вместо {bh.rating:g}")
        if (h.stars or 0) > (bh.stars or 0):
            gains.append(f"отель {h.stars}★ вместо {bh.stars}★")
        if (h.kids.kids_club or h.kids.kids_pool) and not (bh.kids.kids_club or bh.kids.kids_pool):
            gains.append("детский клуб в отеле")
    if hotel and base_hotel and hotel.variant.get("meal") != base_hotel.variant.get("meal"):
        gains.append(f"питание: {hotel.variant.get('tariff', '').split(',')[0].lower()}")
    if _no_bag(base) and not _no_bag(items):
        gains.append("с багажом")
    if _night(cat, base) and not _night(cat, items):
        gains.append("без ночных вылетов и прилётов")
    if any(o.kind == "transfer" and o.variant.get("mode") in ("taxi", "minivan") for o in items) and any(
            o.kind == "transfer" and o.variant.get("mode") == "public" for o in base):
        gains.append("трансфер на машине")
    diff = sum(o.total_rub for o in items) - sum(o.total_rub for o in base)
    head = f"+{rub(diff)} к самому дешёвому" if diff > 0 else "Та же цена"
    return f"{head}: {', '.join(gains) or 'удобнее по времени и условиям'}."


def flex_phrase(items: list[Offer], base: list[Offer]) -> str:
    gains = []
    hotel = _hotel(items)
    if hotel is not None and hotel.cancel_policy and hotel.cancel_policy.free_until:
        gains.append(f"бесплатная отмена отеля до {hotel.cancel_policy.free_until:%d.%m}")
    if any(o.kind == "flight" and o.variant.get("refundable") for o in items):
        gains.append("возвратный тариф перелёта")
    loss, base_loss = cancel_loss(items), cancel_loss(base)
    diff = sum(o.total_rub for o in items) - sum(o.total_rub for o in base)
    head = f"+{rub(diff)} к самому дешёвому" if diff > 0 else "Та же цена"
    gains.append(f"при отмене теряете {rub(loss)} вместо {rub(base_loss)}")
    return f"{head}: {', '.join(gains)}."


def phrase_claim(text: str, items: list[Offer]) -> Claim:
    return Claim(text=text, refs=[f"offer:{o.offer_id}" for o in items])
