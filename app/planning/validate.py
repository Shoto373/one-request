"""validate_plan v0: time, capacity, pets, budget and visa checks (architecture v2, section 7).

All transport times are aware datetimes; hotel dates are local dates of the stay city.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.catalog.models import Capacity, Hotel, Offer, Party
from app.catalog.store import Catalog, get_catalog
from app.planning.models import Check, CheckOption, check_to_wire, rub
from app.planning.query import merged_requirements, party_and_budget
from app.tools.search import flight_offers, hotel_offers, train_offers, transfer_offer

# Minutes from arrival to pickup (baggage, passport control).
EXIT_MIN = {"airport_intl": 45, "airport_dom": 30, "station": 15}
# Minutes before departure to be at the hub.
DEPART_BUFFER_MIN = {"airport_intl": 120, "airport_dom": 90, "station": 40}
LONG_WAIT_MIN = 180


@dataclass
class Legs:
    outbound: Offer | None
    to_hotel: Offer | None
    hotel: Offer | None
    to_hub: Offer | None
    inbound: Offer | None


def split_legs(items: list[Offer]) -> Legs:
    transport = sorted((o for o in items if o.kind in ("flight", "train")), key=lambda o: o.times.start)
    transfers = {o.variant.get("direction"): o for o in items if o.kind == "transfer"}
    hotel = next((o for o in items if o.kind == "hotel"), None)
    return Legs(
        outbound=transport[0] if transport else None,
        to_hotel=transfers.get("to_hotel"),
        hotel=hotel,
        to_hub=transfers.get("to_hub"),
        inbound=transport[-1] if len(transport) > 1 else None,
    )


def hub_class(o: Offer) -> str:
    if o.kind == "train":
        return "station"
    return "airport_intl" if o.variant.get("international") else "airport_dom"


def _hm(dt: datetime) -> str:
    return dt.strftime("%d.%m %H:%M")


def _fmt_min(m: int) -> str:
    h, mm = divmod(int(m), 60)
    return f"{h} ч {mm:02d} мин" if h else f"{mm} мин"


def arrival_at_hotel(legs: Legs) -> datetime | None:
    if legs.to_hotel:
        return legs.to_hotel.times.end
    if legs.outbound:
        return legs.outbound.times.end + timedelta(minutes=EXIT_MIN[hub_class(legs.outbound)] + 30)
    return None


def leave_hotel(legs: Legs) -> datetime | None:
    if legs.to_hub:
        return legs.to_hub.times.start
    if legs.inbound:
        return legs.inbound.times.start - timedelta(minutes=DEPART_BUFFER_MIN[hub_class(legs.inbound)] + 60)
    return None


def _arrival_checks(legs: Legs, h: Hotel, tz: ZoneInfo) -> list[Check]:
    checks: list[Check] = []
    arrive = arrival_at_hotel(legs)
    hotel = legs.hotel
    if arrive is None or hotel is None:
        return checks
    arrive = arrive.astimezone(tz)
    checkin = hotel.times.start.astimezone(tz)
    refs = [f"offer:{hotel.offer_id}", f"hotel:{h.id}"]

    if arrive.date() == checkin.date():
        checks.append(Check(kind="checkin_date", status="ok", refs=refs,
                            text=f"Прибытие в отель {_hm(arrive)}, заезд {checkin:%d.%m}."))
    elif arrive.date() > checkin.date():
        checks.append(Check(kind="checkin_date", status="block", refs=refs,
                            text=f"Заезд {checkin:%d.%m}, а прибытие только {_hm(arrive)}: оплаченная ночь пропадёт.",
                            options=[CheckOption(code="shift_checkin", text=f"Сдвинуть заезд на {arrive:%d.%m}")]))
    else:
        checks.append(Check(kind="checkin_date", status="block", refs=refs,
                            text=f"Прибытие {_hm(arrive)}, а заезд только {checkin:%d.%m}: ночь без отеля.",
                            options=[CheckOption(code="shift_checkin", text=f"Заезд с {arrive:%d.%m}")]))

    hours = h.reception.hours
    if not hours.is_open(arrive.time()):
        window = f"{hours.opens:%H:%M}–{hours.closes:%H:%M}" if hours.opens and hours.closes else ""
        if h.reception.late_arrival_possible:
            checks.append(Check(kind="late_arrival", status="warn", refs=refs,
                                text=f"Стойка работает {window}, прибытие в {arrive:%H:%M}. "
                                     + (h.reception.note or "Поздний заезд по договорённости с отелем.")))
        else:
            checks.append(Check(kind="late_arrival", status="block", refs=refs,
                                text=f"Стойка работает {window}, прибытие в {arrive:%H:%M} — заселиться нельзя.",
                                options=[CheckOption(code="other_hotel", text="Отель с круглосуточной стойкой"),
                                         CheckOption(code="other_transport", text="Рейс с прибытием в часы работы стойки")]))
    else:
        checks.append(Check(kind="late_arrival", status="ok", refs=refs,
                            text="Стойка открыта в момент прибытия."
                            if not hours.round_the_clock else "Круглосуточная стойка."))

    if arrive.date() == checkin.date() and arrive < checkin:
        wait = int((checkin - arrive).total_seconds() // 60)
        if wait > 60:
            opts = [CheckOption(code="wait", text=f"Ждать заселения {_fmt_min(wait)}", price_rub=0)]
            if h.luggage_storage:
                opts.append(CheckOption(code="luggage_storage", text="Оставить багаж на стойке", price_rub=0))
            ec = h.early_checkin
            if ec.available and ec.from_ is not None:
                usable_from = max(ec.from_, arrive.time())
                note = "гарантирован" if ec.guaranteed else "по наличию, не гарантирован"
                opts.append(CheckOption(code="early_checkin", text=f"Ранний заезд с {usable_from:%H:%M} ({note})",
                                        price_rub=ec.price_rub, guaranteed=ec.guaranteed))
            opts.append(CheckOption(code="prev_night", text="Забронировать ночь накануне — номер готов сразу",
                                    price_rub=hotel.unit_price_rub * hotel.quantity, guaranteed=True))
            checks.append(Check(kind="early_arrival", status="warn", refs=refs, options=opts,
                                text=f"Прибытие {arrive:%H:%M}, заселение с {checkin:%H:%M}: ожидание {_fmt_min(wait)}."))
    return checks


def _transfer_checks(legs: Legs) -> list[Check]:
    checks: list[Check] = []
    if legs.outbound and legs.hotel:
        if legs.to_hotel is None:
            checks.append(Check(kind="transfer_fit", status="warn", text="Трансфер до отеля не выбран."))
        else:
            ready = legs.outbound.times.end + timedelta(minutes=EXIT_MIN[hub_class(legs.outbound)])
            if legs.to_hotel.times.start < ready:
                checks.append(Check(kind="transfer_fit", status="warn", refs=[f"offer:{legs.to_hotel.offer_id}"],
                                    text="Подача трансфера раньше, чем вы успеете получить багаж."))
    if legs.inbound and legs.hotel:
        if legs.to_hub is None:
            checks.append(Check(kind="transfer_fit", status="warn", text="Трансфер на обратную дорогу не выбран."))
        else:
            buffer = DEPART_BUFFER_MIN[hub_class(legs.inbound)]
            latest = legs.inbound.times.start - timedelta(minutes=buffer)
            if legs.to_hub.times.end > latest:
                checks.append(Check(kind="transfer_fit", status="block", refs=[f"offer:{legs.to_hub.offer_id}"],
                                    text=f"Трансфер привозит позже, чем нужно быть на месте (за {_fmt_min(buffer)} до отправления)."))
    if not checks and legs.to_hotel and legs.to_hub:
        checks.append(Check(kind="transfer_fit", status="ok", text="Трансферы стыкуются с транспортом."))
    return checks


def _return_checks(legs: Legs, h: Hotel, tz: ZoneInfo) -> list[Check]:
    hotel, leave = legs.hotel, leave_hotel(legs)
    if hotel is None or leave is None:
        return []
    leave = leave.astimezone(tz)
    checkout = hotel.times.end.astimezone(tz)
    refs = [f"offer:{hotel.offer_id}", f"hotel:{h.id}"]
    if leave.date() > checkout.date():
        return [Check(kind="return_trip", status="block", refs=refs,
                      text=f"Отель до {checkout:%d.%m}, а выезд в {_hm(leave)}: не хватает ночи.",
                      options=[CheckOption(code="extend_stay", text="Добавить ночь в отеле")])]
    if leave.date() < checkout.date():
        return [Check(kind="return_trip", status="warn", refs=refs,
                      text=f"Выезд {_hm(leave)} раньше даты выезда {checkout:%d.%m}: последняя ночь не используется.")]
    if leave > checkout:
        gap = int((leave - checkout).total_seconds() // 60)
        if gap > LONG_WAIT_MIN:
            opts = []
            lc = h.late_checkout
            if lc.available and lc.until is not None:
                note = "гарантирован" if lc.guaranteed else "по наличию"
                opts.append(CheckOption(code="late_checkout", text=f"Поздний выезд до {lc.until:%H:%M} ({note})",
                                        price_rub=lc.price_rub, guaranteed=lc.guaranteed))
            if h.luggage_storage:
                opts.append(CheckOption(code="luggage_storage", text="Оставить багаж в отеле", price_rub=0))
            opts.append(CheckOption(code="wait", text="Погулять до выезда", price_rub=0))
            return [Check(kind="return_trip", status="warn", refs=refs, options=opts,
                          text=f"Выезд из номера в {checkout:%H:%M}, из отеля — в {leave:%H:%M}: {_fmt_min(gap)} без номера.")]
    return [Check(kind="return_trip", status="ok", refs=refs,
                  text=f"Выезд из отеля {_hm(leave)} — успеваете на обратную дорогу.")]


def _capacity_problems(cat: Catalog, o: Offer, party: Party) -> str | None:
    """Re-check capacity against the catalog object, not only the offer's own label."""
    name = o.title or o.item_id
    if o.capacity is None or o.capacity.adults != party.adults or o.capacity.children != party.children:
        return f"{name}: рассчитано на другой состав"
    if o.kind == "hotel":
        h = cat.hotels.get(o.item_id)
        room = next((r for r in h.rooms if r.id == o.variant.get("room_id")), None) if h else None
        if room is None:
            return None
        with_bed = party.adults + sum(1 for a in party.children_ages if a >= 2)
        if (party.adults > room.max_adults * o.quantity or party.children > room.max_children * o.quantity
                or with_bed > room.max_occupancy * o.quantity):
            return f"{name}: {o.quantity} × «{room.name}» не вмещает {party.people} гостей"
    if o.kind == "transfer":
        r = cat.transfers.get(o.item_id)
        if r is None:
            return None
        if r.price_unit == "per_vehicle":
            if party.people > r.capacity_pax * o.quantity or party.bag_count > r.capacity_bags * o.quantity:
                return f"{name}: {o.quantity} машин(ы) не вмещают {party.people} чел. и {party.bag_count} мест багажа"
        elif party.people > r.capacity_pax:
            return f"{name}: нет мест на {party.people} чел."
    return None


def _capacity_check(cat: Catalog, items: list[Offer], party: Party) -> Check:
    problems = [(o, p) for o in items if (p := _capacity_problems(cat, o, party))]
    if problems:
        return Check(kind="capacity", status="block", refs=[f"offer:{o.offer_id}" for o, _ in problems],
                     text="; ".join(p for _, p in problems) + ".",
                     options=[CheckOption(code="more_rooms", text="Добавить номер или машину побольше")])
    who = f"{party.adults} взр." + (f" и {party.children} дет." if party.children else "")
    return Check(kind="capacity", status="ok", text=f"Номера и трансферы вмещают {who} и багаж.")


def _pet_problem(cat: Catalog, o: Offer, party: Party) -> str | None:
    """Carrier, hotel and transfer rules from the catalog; every pet must pass all three."""
    name = o.title or o.item_id
    for pet in party.pets:
        if o.kind == "flight":
            f = cat.flights.get(o.item_id)
            if f and not ((f.pets.cabin and f.pets.cabin_max_kg and pet.weight_kg <= f.pets.cabin_max_kg) or f.pets.hold):
                return f"{name}: перевозчик не берёт животное {pet.weight_kg:g} кг" + (f" ({f.pets.note})" if f.pets.note else "")
        elif o.kind == "train":
            t = cat.trains.get(o.item_id)
            car = next((c for c in t.cars if c.car_class == o.variant.get("car_class")), None) if t else None
            if car and not car.pets_allowed:
                return f"{name}: в этом вагоне животных не перевозят"
        elif o.kind == "hotel":
            h = cat.hotels.get(o.item_id)
            if h and (not h.pets.allowed or (h.pets.max_weight_kg is not None and pet.weight_kg > h.pets.max_weight_kg)):
                return f"{name}: отель не принимает животное {pet.weight_kg:g} кг"
        elif o.kind == "transfer":
            r = cat.transfers.get(o.item_id)
            if r and not r.pets_allowed:
                return f"{name}: трансфер без животных"
    if o.capacity is not None and not o.capacity.pets:
        return f"{name}: цена посчитана без животного — сбор за перевозку не учтён"
    return None


def _pets_check(cat: Catalog, items: list[Offer], party: Party) -> Check:
    problems = [(o, p) for o in items if (p := _pet_problem(cat, o, party))]
    if problems:
        return Check(kind="pets", status="block", refs=[f"offer:{o.offer_id}" for o, _ in problems],
                     text="; ".join(p for _, p in problems) + ".",
                     options=[CheckOption(code="other_transport", text="Перевозчик, отель и трансфер, которые берут животных")])
    return Check(kind="pets", status="ok", text="Животное принимают перевозчик, отель и трансферы.",
                 refs=[f"offer:{o.offer_id}" for o in items])


def validate_plan(
    cat: Catalog,
    items: list[Offer],
    party: Party,
    budget_rub: int | None = None,
    budget_hard: bool = False,
) -> list[Check]:
    untimed = [o for o in items if o.times is None]
    legs = split_legs([o for o in items if o.times is not None])
    checks: list[Check] = []
    if untimed:
        checks.append(Check(kind="availability", status="block", refs=[f"offer:{o.offer_id}" for o in untimed],
                            text="У части позиций нет времени — состыковки не проверить."))
    h = cat.hotels.get(legs.hotel.item_id) if legs.hotel else None
    if h is not None:
        tz = ZoneInfo(cat.cities[h.city_id].timezone)
        checks += _arrival_checks(legs, h, tz)
        checks += _return_checks(legs, h, tz)
    checks += _transfer_checks(legs)

    checks.append(_capacity_check(cat, items, party))
    if party.pets:
        checks.append(_pets_check(cat, items, party))

    total = sum(o.total_rub for o in items)
    if budget_rub is not None:
        over = total - budget_rub
        if over > 0:
            checks.append(Check(kind="budget", status="block" if budget_hard else "warn",
                                text=f"Итог {rub(total)} выше бюджета на {rub(over)}."))
        else:
            checks.append(Check(kind="budget", status="ok",
                                text=f"Итог {rub(total)} в рамках бюджета {rub(budget_rub)}."))

    if h is not None:
        city = cat.cities[h.city_id]
        visa = city.visa
        nights = legs.hotel.units if legs.hotel else 0
        if visa.regime == "domestic":
            checks.append(Check(kind="visa", status="ok", text=visa.note, refs=[f"city:{city.id}:visa"]))
        elif visa.max_stay_days is not None and nights > visa.max_stay_days:
            checks.append(Check(kind="visa", status="block", refs=[f"city:{city.id}:visa"],
                                text=f"Поездка {nights} ноч. длиннее безвизового срока {visa.max_stay_days} дн. {visa.note}"))
        else:
            checks.append(Check(kind="visa", status="warn", refs=[f"city:{city.id}:visa"],
                                text=f"{city.country}: {visa.note} Документы: {', '.join(visa.documents)}."))
    return checks


# --------------------------------------------------------------------------- checkout re-validation

_VARIANT_KEYS = {"flight": ("fare",), "train": ("car_class",), "hotel": ("room_id", "tariff_id"), "transfer": ("direction",)}


def _same_offer(a: Offer, b: Offer) -> bool:
    return (
        a.kind == b.kind
        and a.item_id == b.item_id
        and a.times.start == b.times.start
        and all(a.variant.get(k) == b.variant.get(k) for k in _VARIANT_KEYS[a.kind])
    )


def current_offer(cat: Catalog, o: Offer, party: Party) -> Offer | None:
    """The same offer priced again from the catalog now (None if it no longer exists)."""
    if o.kind == "flight":
        f = cat.flights.get(o.item_id)
        cands = flight_offers(f, party) if f else []
    elif o.kind == "train":
        t = cat.trains.get(o.item_id)
        cands = train_offers(t, party) if t else []
    elif o.kind == "hotel":
        h = cat.hotels.get(o.item_id)
        if h is None:
            return None
        tz = cat.cities[h.city_id].timezone
        zone = ZoneInfo(tz)
        cands = hotel_offers(h, o.times.start.astimezone(zone).date(), o.times.end.astimezone(zone).date(), party, tz)
    else:
        r = cat.transfers.get(o.item_id)
        if r is None:
            return None
        found = transfer_offer(r, party, o.times.start, o.variant.get("direction", "to_hotel"), cat.hubs[r.hub_id].timezone)
        cands = [found] if found else []
    return next((c for c in cands if _same_offer(c, o)), None)


def availability_checks(cat: Catalog, items: list[Offer], party: Party) -> list[Check]:
    checks = []
    for o in items:
        now = current_offer(cat, o, party) if o.times is not None else None
        name = o.title or o.variant.get("title") or o.item_id
        if now is None:
            checks.append(Check(kind="availability", status="block", refs=[f"offer:{o.offer_id}"],
                                text=f"{name}: предложение больше недоступно.",
                                options=[CheckOption(code="rebuild", text="Пересобрать комплект")]))
        elif now.total_rub != o.total_rub:
            checks.append(Check(kind="availability", status="block", refs=[f"offer:{o.offer_id}"],
                                text=f"{name}: цена изменилась — было {rub(o.total_rub)}, стало {rub(now.total_rub)}.",
                                options=[CheckOption(code="rebuild", text="Пересобрать комплект")]))
    if not checks:
        checks.append(Check(kind="availability", status="ok", text="Все предложения актуальны, цены не изменились."))
    return checks


def _party_from_capacity(items: list[Offer]) -> Party:
    cap = next((o.capacity for o in items if o.capacity), None) or Capacity(adults=1, children=0, pets=False)
    return Party(adults=max(1, cap.adults), children_ages=[7] * cap.children)


def _in_catalog(cat: Catalog, o: Offer) -> bool:
    index = {"flight": cat.flights, "train": cat.trains, "hotel": cat.hotels, "transfer": cat.transfers}[o.kind]
    return o.item_id in index


def validate_bundle(bundle: dict[str, Any], state: Any = None) -> list[dict[str, Any]]:
    """Checkout re-validation (track C cart): plan checks + offers still exist at the same price.

    ``bundle`` and ``state`` are JSON dicts as stored in TripState; returns wire checks
    (``options`` as strings). Any ``block`` means the bundle must not be paid.
    A bundle with no item from the track A catalog (test fixtures, hand-made bundles)
    has nothing to re-check here: its stored checks are returned unchanged.
    """
    cat = get_catalog()
    items = [Offer.model_validate(i) for i in bundle.get("items") or []]
    if not any(_in_catalog(cat, o) for o in items):
        return list(bundle.get("checks") or [])
    req, kinds_by_key = merged_requirements({}, state)
    budget, hard = None, False
    if req:
        party, budget, hard = party_and_budget(req, kinds_by_key)
    else:
        party = _party_from_capacity(items)
    checks = validate_plan(cat, items, party, budget, hard) + availability_checks(cat, items, party)
    return [check_to_wire(c) for c in checks]
