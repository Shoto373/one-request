"""Search tools: catalog objects -> priced Offers for concrete dates and party.

Rules shared by all searches:
- ``total_rub`` covers the whole party and the whole period (contract section 4);
- capacity is checked per offer (rooms, seats, vehicles), never assumed;
- with pets in the party, objects that do not take them are skipped;
- results are sorted by ``total_rub`` and fully deterministic.
"""

from __future__ import annotations

import hashlib
import math
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from app.catalog.models import (
    MEAL_INCLUDED,
    CancelPolicy,
    Capacity,
    Flight,
    Hotel,
    Offer,
    OfferTimes,
    Party,
    RoomType,
    Train,
    TransferRoute,
)
from app.catalog.store import Catalog

TransportKind = Literal["flight", "train"]


def party_key(party: Party) -> str:
    return hashlib.sha1(party.model_dump_json().encode()).hexdigest()[:4]


def _capacity(party: Party) -> Capacity:
    return Capacity(adults=party.adults, children=party.children, pets=bool(party.pets))


def _extra(code: str, title: str, price_rub: int) -> dict:
    return {"code": code, "title": title, "price_rub": int(price_rub)}


# --------------------------------------------------------------------------- transport


def _flight_pet_extras(f: Flight, party: Party) -> list[dict] | None:
    """Pet fees for this flight, or None if some pet cannot fly."""
    extras = []
    for pet in party.pets:
        if f.pets.cabin and f.pets.cabin_max_kg and pet.weight_kg <= f.pets.cabin_max_kg:
            extras.append(_extra("pet_cabin", f"Животное в салоне ({pet.weight_kg:g} кг)", f.pets.cabin_fee_rub))
        elif f.pets.hold:
            extras.append(_extra("pet_hold", f"Животное в багажном отсеке ({pet.weight_kg:g} кг)", f.pets.hold_fee_rub))
        else:
            return None
    return extras


def flight_offers(f: Flight, party: Party) -> list[Offer]:
    extras = _flight_pet_extras(f, party) if party.pets else []
    if extras is None:
        return []
    seats = party.paying(f.infant_free_age)
    out = []
    for fare in f.fares:
        if fare.seats_left < seats:
            continue
        total = fare.price_rub * seats + sum(e["price_rub"] for e in extras)
        included = [f"cabin_bag_{fare.baggage.cabin_kg}kg"]
        if fare.baggage.checked_pieces:
            included.append(f"checked_bag_{fare.baggage.checked_pieces}x{fare.baggage.checked_kg}kg")
        penalty = (fare.refund_fee_rub or 0) * seats if fare.refundable else total
        out.append(
            Offer(
                offer_id=f"off_{f.id}_{fare.code}_{party_key(party)}",
                kind="flight",
                item_id=f.id,
                variant={
                    "title": f"{f.carrier_name} {f.flight_no}",
                    "carrier": f.carrier,
                    "carrier_name": f.carrier_name,
                    "flight_no": f.flight_no,
                    "fare": fare.code,
                    "fare_name": fare.name,
                    "refundable": fare.refundable,
                    "origin_city": f.origin_city,
                    "dest_city": f.dest_city,
                    "from_hub": f.from_hub,
                    "to_hub": f.to_hub,
                    "stops": f.stops,
                    "via": [s.to_hub for s in f.segments[:-1]],
                    "duration_min": f.duration_min,
                    "international": f.international,
                    "extras": extras,
                },
                capacity=_capacity(party),
                quantity=1,
                unit_price_rub=fare.price_rub,
                price_unit="per_person",
                units=seats,
                total_rub=total,
                included=included,
                baggage=fare.baggage.model_dump(mode="json"),
                cancel_policy=CancelPolicy(free_until=None, penalty_rub=penalty),
                times=OfferTimes(start=f.depart, end=f.arrive),
                synthetic=f.synthetic,
                source_refs=[f"flight:{f.id}", *f.source_refs],
            )
        )
    return out


def train_offers(t: Train, party: Party) -> list[Offer]:
    seats = party.paying(t.child_free_age)
    out = []
    for car in t.cars:
        if car.seats_left < seats or (party.pets and not car.pets_allowed):
            continue
        extras = [_extra("pet", f"Животное ({p.weight_kg:g} кг)", car.pet_fee_rub) for p in party.pets]
        total = car.price_rub * seats + sum(e["price_rub"] for e in extras)
        title = f"Поезд {t.train_no}" + (f" «{t.name}»" if t.name else "")
        out.append(
            Offer(
                offer_id=f"off_{t.id}_{car.car_class}_{party_key(party)}",
                kind="train",
                item_id=t.id,
                variant={
                    "title": title,
                    "train_no": t.train_no,
                    "train_name": t.name,
                    "car_class": car.car_class,
                    "car_name": car.name,
                    "origin_city": t.origin_city,
                    "dest_city": t.dest_city,
                    "from_hub": t.from_hub,
                    "to_hub": t.to_hub,
                    "duration_min": t.duration_min,
                    "international": False,
                    "extras": extras,
                },
                capacity=_capacity(party),
                quantity=1,
                unit_price_rub=car.price_rub,
                price_unit="per_person",
                units=seats,
                total_rub=total,
                included=["bedding"] if car.bedding_included else [],
                baggage={"hand_kg": t.hand_luggage_kg},
                cancel_policy=CancelPolicy(free_until=None, penalty_rub=car.refund_fee_rub * seats),
                times=OfferTimes(start=t.depart, end=t.arrive),
                synthetic=t.synthetic,
                source_refs=[f"train:{t.id}", *t.source_refs],
            )
        )
    return out


def search_transport(
    cat: Catalog,
    origin_city: str,
    dest_city: str,
    day: date,
    party: Party,
    kinds: tuple[TransportKind, ...] = ("flight", "train"),
) -> list[Offer]:
    """Offers departing on ``day`` (local date at the origin), cheapest first."""
    out: list[Offer] = []
    if "flight" in kinds:
        for f in cat.flights_by_route_day.get((origin_city, dest_city, day), []):
            out += flight_offers(f, party)
    if "train" in kinds:
        for t in cat.trains_by_route_day.get((origin_city, dest_city, day), []):
            out += train_offers(t, party)
    return sorted(out, key=lambda o: (o.total_rub, o.times.start, o.offer_id))


# --------------------------------------------------------------------------- hotels


def rooms_needed(room: RoomType, party: Party, max_rooms: int = 4) -> int | None:
    """Minimal number of identical rooms that fit the party (each room has an adult)."""
    with_bed = party.adults + sum(1 for a in party.children_ages if a >= 2)
    for q in range(1, min(party.adults, max_rooms) + 1):
        if (
            party.adults <= q * room.max_adults
            and party.children <= q * room.max_children
            and with_bed <= q * room.max_occupancy
        ):
            return q
    return None


def hotel_available(h: Hotel, checkin: date, checkout: date) -> bool:
    nights = [checkin + timedelta(days=i) for i in range((checkout - checkin).days)]
    for d in nights:
        if h.open_periods and not any(p.contains(d) for p in h.open_periods):
            return False
        if any(p.contains(d) for p in h.sold_out):
            return False
    return True


def hotel_accepts_pets(h: Hotel, party: Party) -> bool:
    if not party.pets:
        return True
    if not h.pets.allowed:
        return False
    return h.pets.max_weight_kg is None or all(p.weight_kg <= h.pets.max_weight_kg for p in party.pets)


def nightly_prices(h: Hotel, room: RoomType, factor: float, checkin: date, checkout: date) -> list[int]:
    out = []
    for i in range((checkout - checkin).days):
        d = checkin + timedelta(days=i)
        price = room.base_price_rub * h.month_factors[d.month - 1] * factor
        if d.weekday() in (4, 5):
            price *= h.weekend_factor
        out.append(int(round(price / 10) * 10))
    return out


def hotel_offers(h: Hotel, checkin: date, checkout: date, party: Party, tz: str) -> list[Offer]:
    nights = (checkout - checkin).days
    if nights < 1 or not hotel_available(h, checkin, checkout) or not hotel_accepts_pets(h, party):
        return []
    zone = ZoneInfo(tz)
    start = datetime.combine(checkin, h.checkin_time, zone)
    end = datetime.combine(checkout, h.checkout_time, zone)
    payable_guests = party.adults + sum(1 for a in party.children_ages if a >= h.child_free_age)
    out = []
    for room in h.rooms:
        q = rooms_needed(room, party)
        if q is None or room.inventory < q:
            continue
        extra_guests = max(0, payable_guests - 2 * q)
        for tariff in h.tariffs:
            if room.id not in tariff.room_ids:
                continue
            nightly = nightly_prices(h, room, tariff.price_factor, checkin, checkout)
            unit = int(round(sum(nightly) / nights))
            extras = []
            if extra_guests:
                extras.append(_extra("extra_guests", f"Доп. гости: {extra_guests} × {nights} ноч.",
                                     extra_guests * h.extra_guest_rub_per_night * nights))
            if party.pets and h.pets.fee_rub_per_night:
                extras.append(_extra("pet", f"Животное: {len(party.pets)} × {nights} ноч.",
                                     len(party.pets) * h.pets.fee_rub_per_night * nights))
            total = unit * nights * q + sum(e["price_rub"] for e in extras)
            if tariff.refundable and tariff.free_cancel_days is not None:
                deadline = datetime.combine(checkin - timedelta(days=tariff.free_cancel_days), time(23, 59), zone)
                cancel = CancelPolicy(free_until=deadline, penalty_rub=min(total, unit * q * tariff.penalty_nights))
            else:
                cancel = CancelPolicy(free_until=None, penalty_rub=total)
            included = list(MEAL_INCLUDED[tariff.meal]) + (["wifi"] if "wifi" in h.amenities else [])
            out.append(
                Offer(
                    offer_id=f"off_{room.id}_{tariff.id}_{checkin:%Y%m%d}_{checkout:%Y%m%d}_{party_key(party)}",
                    kind="hotel",
                    item_id=h.id,
                    variant={
                        "title": h.name,
                        "hotel_name": h.name,
                        "stars": h.stars,
                        "rating": h.rating,
                        "city_id": h.city_id,
                        "room_id": room.id,
                        "room_type": room.name,
                        "tariff_id": tariff.id,
                        "tariff": tariff.name,
                        "meal": tariff.meal,
                        "refundable": tariff.refundable,
                        "nights": nights,
                        "nightly_rub": nightly,
                        "dist_beach_km": h.dist_beach_km,
                        "dist_center_km": h.dist_center_km,
                        "extras": extras,
                    },
                    capacity=_capacity(party),
                    quantity=q,
                    unit_price_rub=unit,
                    price_unit="per_room_night",
                    units=nights,
                    total_rub=total,
                    included=included,
                    baggage=None,
                    cancel_policy=cancel,
                    times=OfferTimes(start=start, end=end),
                    synthetic=h.synthetic,
                    source_refs=[f"hotel:{h.id}", *h.source_refs],
                )
            )
    return out


def search_hotels(
    cat: Catalog,
    city_id: str,
    checkin: date,
    checkout: date,
    party: Party,
    cheapest_per_hotel: bool = False,
) -> list[Offer]:
    """Room+tariff offers for the stay; ``cheapest_per_hotel`` keeps one offer per hotel."""
    tz = cat.cities[city_id].timezone
    out: list[Offer] = []
    for h in cat.hotels_by_city.get(city_id, []):
        offers = sorted(hotel_offers(h, checkin, checkout, party, tz), key=lambda o: (o.total_rub, o.offer_id))
        out += offers[:1] if cheapest_per_hotel else offers
    return sorted(out, key=lambda o: (o.total_rub, o.offer_id))


# --------------------------------------------------------------------------- transfers


def transfer_offer(
    r: TransferRoute, party: Party, start: datetime, direction: Literal["to_hotel", "to_hub"], tz: str
) -> Offer | None:
    local = start.astimezone(ZoneInfo(tz))
    if not r.hours.is_open(local.time()):
        return None
    if party.pets and not r.pets_allowed:
        return None
    people, bags = party.people, party.bag_count
    if r.price_unit == "per_vehicle":
        quantity = max(math.ceil(people / r.capacity_pax), math.ceil(bags / r.capacity_bags) if bags else 1)
        units = 1
    else:
        if people > r.capacity_pax or bags > r.capacity_bags:
            return None
        quantity, units = 1, max(1, party.paying(r.child_free_age))
    extras = [_extra("pet", "Перевозка животного", r.pet_fee_rub)] if party.pets and r.pet_fee_rub else []
    total = r.price_rub * units * quantity + sum(e["price_rub"] for e in extras)
    end = local + timedelta(minutes=r.duration_min)
    mode_name = {"taxi": "Такси", "minivan": "Минивэн", "shuttle": "Шаттл", "public": "Общественный транспорт"}[r.mode]
    return Offer(
        offer_id=f"off_{r.id}_{local:%Y%m%d%H%M}_{direction}_{party_key(party)}",
        kind="transfer",
        item_id=r.id,
        variant={
            "title": f"{mode_name}, {r.distance_km:g} км, ~{r.duration_min} мин",
            "mode": r.mode,
            "provider": r.provider,
            "direction": direction,
            "hub_id": r.hub_id,
            "hotel_id": r.hotel_id,
            "distance_km": r.distance_km,
            "duration_min": r.duration_min,
            "extras": extras,
        },
        capacity=_capacity(party),
        quantity=quantity,
        unit_price_rub=r.price_rub,
        price_unit=r.price_unit,
        units=units,
        total_rub=total,
        included=["child_seat"] if r.child_seat and party.children else [],
        baggage={"bags": bags},
        cancel_policy=CancelPolicy(free_until=local - timedelta(hours=r.free_cancel_hours), penalty_rub=total),
        times=OfferTimes(start=local, end=end),
        synthetic=r.synthetic,
        source_refs=[f"transfer:{r.id}", *r.source_refs],
    )


def search_transfers(
    cat: Catalog,
    hub_id: str,
    hotel_id: str,
    party: Party,
    at: datetime,
    direction: Literal["to_hotel", "to_hub"] = "to_hotel",
) -> list[Offer]:
    """``to_hotel``: pickup at the hub at ``at``. ``to_hub``: be at the hub by ``at``."""
    tz = cat.hubs[hub_id].timezone
    out = []
    for r in cat.transfers_by_pair.get((hub_id, hotel_id), []):
        start = at if direction == "to_hotel" else at - timedelta(minutes=r.duration_min)
        offer = transfer_offer(r, party, start, direction, tz)
        if offer:
            out.append(offer)
    return sorted(out, key=lambda o: (o.total_rub, o.offer_id))
