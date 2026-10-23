"""validate_plan v0: time, capacity, pets, budget and visa checks (architecture v2, section 7).

All transport times are aware datetimes; hotel dates are local dates of the stay city.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.catalog.models import Hotel, Offer, Party
from app.catalog.store import Catalog
from app.planning.models import Check, CheckOption

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


def validate_plan(
    cat: Catalog,
    items: list[Offer],
    party: Party,
    budget_rub: int | None = None,
    budget_hard: bool = False,
) -> list[Check]:
    legs = split_legs(items)
    checks: list[Check] = []
    h = cat.hotels.get(legs.hotel.item_id) if legs.hotel else None
    if h is not None:
        tz = ZoneInfo(cat.cities[h.city_id].timezone)
        checks += _arrival_checks(legs, h, tz)
        checks += _return_checks(legs, h, tz)
    checks += _transfer_checks(legs)

    bad = [o for o in items if o.capacity.adults != party.adults or o.capacity.children != party.children]
    checks.append(Check(kind="capacity", status="block" if bad else "ok", refs=[f"offer:{o.offer_id}" for o in bad],
                        text="Не все позиции рассчитаны на этот состав." if bad else
                        f"Все позиции на {party.adults} взр." + (f" и {party.children} дет." if party.children else "")))

    if party.pets:
        no = [o for o in items if not o.capacity.pets]
        checks.append(Check(kind="pets", status="block" if no else "ok", refs=[f"offer:{o.offer_id}" for o in no],
                            text="Не все позиции принимают животное." if no else
                            "Животное принимают перевозчик, отель и трансферы."))

    total = sum(o.total_rub for o in items)
    if budget_rub is not None:
        over = total - budget_rub
        if over > 0:
            checks.append(Check(kind="budget", status="block" if budget_hard else "warn",
                                text=f"Итог {total:,} ₽ выше бюджета на {over:,} ₽.".replace(",", " ")))
        else:
            checks.append(Check(kind="budget", status="ok",
                                text=f"Итог {total:,} ₽ в рамках бюджета {budget_rub:,} ₽.".replace(",", " ")))

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
