"""Normalize the documented Tutu MCP responses into the project's Offer contract."""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.catalog.models import Offer, OfferTimes


def _number(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and number >= 0 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _rubles(amount: Any) -> tuple[int, str] | None:
    number = _number(amount)
    if number is None:
        return None
    whole = int(number.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return whole, format(number, "f")


def _dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        out = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return out if out.tzinfo is not None else None
    except ValueError:
        return None


def _price(data: dict[str, Any], kind: str) -> tuple[int, str, str] | None:
    if kind == "train":
        source = data.get("price_party")
        basis = "party_estimate_same_fare"
    else:
        source = data.get("price")
        basis = "round_trip_party_total" if data.get("is_round_trip") else "stay_total" if kind == "hotel" else "party_total"
    if not isinstance(source, dict) or source.get("currency") != "RUB":
        return None
    converted = _rubles(source.get("amount"))
    return (converted[0], converted[1], basis) if converted else None


def _clean_facts(value: Any) -> Any:
    """Keep compact provider facts; provider text is data, never agent instructions."""
    if isinstance(value, list):
        return [_clean_facts(x) for x in value[:20]]
    if isinstance(value, dict):
        return {str(k): _clean_facts(v) for k, v in list(value.items())[:40]
                if k not in {"description", "room_description", "policy", "photos"}}
    if isinstance(value, str):
        return value[:500]
    return value


def _selected_fare(row: dict[str, Any], kind: str) -> dict[str, Any] | None:
    fares = row.get("variants") or row.get("fares") or []
    if isinstance(fares, dict):
        fares = [fares]
    if not isinstance(fares, list):
        return None
    base_price = row.get("price") if kind == "flight" else row.get("price")
    base_amount = _number((base_price or {}).get("amount")) if isinstance(base_price, dict) else None
    base_currency = (base_price or {}).get("currency") if isinstance(base_price, dict) else None
    for fare in fares:
        fare_price = fare.get("price") if isinstance(fare, dict) else None
        amount = _number((fare_price or {}).get("amount")) if isinstance(fare_price, dict) else None
        currency = (fare_price or {}).get("currency") if isinstance(fare_price, dict) else None
        if base_amount is not None and amount == base_amount and currency == base_currency:
            return fare
    return fares[0] if len(fares) == 1 and isinstance(fares[0], dict) else None


def transport_offer(
    row: dict[str, Any], *, kind: Literal["flight", "train"], direction: str,
    search_args: dict[str, Any], fetched_at: str, resolved_places: dict[str, Any] | None = None,
) -> Offer | None:
    cost = _price(row, kind)
    if cost is None:
        return None
    total, raw_amount, basis = cost
    start = _dt(row.get("departure_at"))
    end = _dt(row.get("arrival_at"))
    legs = row.get("legs") if isinstance(row.get("legs"), list) else []
    if kind == "flight" and legs:
        outbound = next((leg for leg in legs if leg.get("label") == "outbound"), legs[0])
        segments = outbound.get("segments") or []
        if segments:
            start = _dt(segments[0].get("departure_at")) or start
            end = _dt(segments[-1].get("arrival_at")) or end
    if start is None or end is None:
        return None
    supplier_id = str(row.get("offer_id") or "")
    if not supplier_id:
        return None
    companies = row.get("carriers") or []
    transport = row.get("transport") or ("avia" if kind == "flight" else "railway")
    fare = _selected_fare(row, kind)
    conditions = (fare or {}).get("conditions") or {}
    baggage = {key: _clean_facts(conditions[key]) for key in ("baggage", "cabin_baggage") if key in conditions}
    leg_durations = [leg.get("duration_min") for leg in legs if isinstance(leg.get("duration_min"), (int, float))]
    duration_min = sum(leg_durations) if leg_durations else row.get("duration_min")
    title = (", ".join(str(x) for x in companies) + " · " if companies else "")
    if kind == "train":
        leg = (legs or [{}])[0]
        first = ((leg.get("segments") or [{}])[0])
        title += f"Поезд {first.get('voyage_no') or row.get('train_number') or ''}".strip()
    else:
        first = ((next((leg for leg in legs if leg.get("label") == "outbound"), legs[0] if legs else {}).get("segments") or [{}])[0])
        title += str(first.get("voyage_no") or "Авиаперелёт туда и обратно")
        cabin = {"S": "премиум-эконом", "C": "бизнес-класс", "F": "первый класс"}.get(str(search_args.get("service_class") or ""))
        if cabin:
            title += f" · {cabin}"
    summary = {
        "direction": direction,
        "transport": transport,
        "service_class": search_args.get("service_class") if kind == "flight" else None,
        "price_amount": raw_amount,
        "price_basis": basis,
        "currency": "RUB",
        "party_price_note": row.get("price_party") if kind == "train" else None,
        "duration_min": duration_min,
        "legs": _clean_facts(legs),
        "segments_count": row.get("segments_count"),
        "variants": _clean_facts(row.get("variants") or row.get("fares")),
        "fare_conditions": _clean_facts(conditions) if fare else None,
        "baggage": baggage or None,
        "checkout_ref": _clean_facts(row.get("checkout_ref")),
        "details_ref": _clean_facts(row.get("details_ref")),
        "search_results_url": row.get("search_results_url"),
        "resolved_places": _clean_facts(resolved_places) if resolved_places else None,
        "pet_status": None,
        "cancellation_status": conditions.get("refundable"),
    }
    source_url = row.get("search_results_url")
    if not source_url:
        if kind == "flight":
            from_code = search_args.get("origin") or (resolved_places or {}).get("from", {}).get("geo_id") or "MOW"
            to_code = search_args.get("destination") or (resolved_places or {}).get("to", {}).get("geo_id") or "AER"
            dep_date = search_args.get("departure_date", "")
            adults = search_args.get("adults", 1)
            children = search_args.get("children", 0)
            infants = search_args.get("infants", 0)
            pax = f"{adults}{children}{infants}"
            if dep_date:
                try:
                    dt_obj = date.fromisoformat(str(dep_date))
                    dt_str = dt_obj.strftime("%d%m%Y")
                    source_url = f"https://avia.tutu.ru/offers/?passengers={pax}&route[0]={from_code}-{dt_str}-{to_code}"
                except Exception:
                    source_url = "https://avia.tutu.ru/"
            else:
                source_url = "https://avia.tutu.ru/"
        elif kind == "train":
            source_url = "https://www.tutu.ru/poezda/"
    offer_id = f"tutu:{kind}:{direction}:{supplier_id}"
    resolved_from = (resolved_places or {}).get("from") or {}
    resolved_to = (resolved_places or {}).get("to") or {}
    return Offer(
        offer_id=offer_id, kind=kind, item_id=f"tutu:{transport}:{supplier_id}", title=title.strip(),
        origin_city=str(resolved_from.get("name") or search_args.get("origin") or ""),
        destination_city=str(resolved_to.get("name") or search_args.get("destination") or ""),
        origin_provider_id=str(resolved_from.get("geo_id") or "") or None,
        destination_provider_id=str(resolved_to.get("geo_id") or "") or None,
        variant=summary, quantity=1, unit_price_rub=total, price_unit="per_trip", units=1, total_rub=total,
        included=[], baggage=None, times=OfferTimes(start=start, end=end), synthetic=False,
        source_refs=[f"tutu:{transport}:{supplier_id}", f"mcp:{'search_avia' if kind == 'flight' else 'search_rail'}"],
        provider="tutu", provider_offer_id=supplier_id,
        fetched_at=_dt(fetched_at), currency="RUB",
        data_mode="estimated" if kind == "train" else "live",
        search_params=dict(search_args), source_url=source_url,
        revalidation={"tool": "search_avia" if kind == "flight" else "search_rail",
                      "details_ref": summary["details_ref"], "search_args": dict(search_args)},
    )


def _pet_fact(details: dict[str, Any] | None) -> bool | None:
    if not details:
        return None
    hotel = details.get("hotel") or {}
    text_parts = []
    for group in hotel.get("amenity_groups") or []:
        text_parts.extend(str(x) for x in group.get("amenities") or [])
    text_parts.extend(str(x) for x in hotel.get("amenities") or [])
    text = " ".join(text_parts).lower().replace("ё", "е")
    if any(x in text for x in ("с питомцами нельзя", "питомцы запрещены", "без животных", "не допускаются животные")):
        return False
    if any(x in text for x in ("можно с питом", "разрешено с питом", "питомцы разрешены", "pet friendly")):
        return True
    return None


def hotel_offer(
    row: dict[str, Any], *, search_args: dict[str, Any], fetched_at: str,
    details: dict[str, Any] | None = None, city_timezone: str = "Europe/Moscow",
    resolved_destination: dict[str, Any] | None = None,
) -> Offer | None:
    best = row.get("best_offer") if isinstance(row.get("best_offer"), dict) else {}
    supplier_id = str(row.get("tutu_offer_id") or row.get("hotel_id") or "")
    hotel_id = str(row.get("hotel_id") or row.get("hotel_geo_id") or "")
    stay = details.get("stay", {}) if details else {}
    checkin_raw = stay.get("check_in") or search_args.get("check_in")
    checkout_raw = stay.get("check_out") or search_args.get("check_out")
    if not supplier_id or not hotel_id or not checkin_raw or not checkout_raw:
        return None
    try:
        checkin = date.fromisoformat(str(checkin_raw))
        checkout = date.fromisoformat(str(checkout_raw))
    except ValueError:
        return None
    hotel_details = details.get("hotel", {}) if details else {}
    cin = hotel_details.get("check_in_time")
    cout = hotel_details.get("check_out_time")
    def hotel_at(day: date, raw_time: Any) -> datetime | None:
        if not isinstance(raw_time, str):
            return None
        try:
            return datetime.combine(day, time.fromisoformat(raw_time), tzinfo=ZoneInfo(city_timezone))
        except ValueError:
            return None
    start, end = hotel_at(checkin, cin), hotel_at(checkout, cout)
    rate = None
    if details:
        all_rates = []
        for room in details.get("rooms") or []:
            for candidate in room.get("rates") or []:
                if ((candidate.get("price") or {}).get("currency") == "RUB"
                        and _rubles((candidate.get("price") or {}).get("amount"))):
                    all_rates.append((room, candidate))
        listing_price = _rubles((best.get("price") or {}).get("amount"))
        if listing_price:
            rate = next(((room, candidate) for room, candidate in all_rates
                         if _rubles((candidate.get("price") or {}).get("amount"))[0] == listing_price[0]), None)
        # Details return actual dated room rates. If the compact listing has moved
        # between calls, use the cheapest returned exact rate and its own hash;
        # never attach listing terms/price to a different detailed rate.
        if rate is None and all_rates:
            rate = min(all_rates, key=lambda pair: _rubles(pair[1]["price"]["amount"])[0])
    room_obj, rate = rate if rate else ({}, None)
    source_price = (rate or {}).get("price") or best.get("price")
    cost = _price({"price": source_price}, "hotel")
    if cost is None:
        return None
    total, raw_amount, basis = cost
    meal = (rate or {}).get("meal") or {}
    terms = {
        "guest_composition": _clean_facts(stay.get("guests") or {"adults": search_args.get("adults"), "children_ages": search_args.get("children_ages")}),
        "stay_nights": (checkout - checkin).days,
        "price_basis": "stay_total",
        "price_amount": raw_amount,
        "currency": "RUB",
        "breakfast_included": best.get("breakfast_included") if rate is None else rate.get("breakfast_included"),
        "meal_name": best.get("meal_name") if rate is None else meal.get("name"),
        "free_cancellation": best.get("free_cancellation") if rate is None else rate.get("free_cancellation"),
        "free_cancellation_until": (rate or {}).get("free_cancellation_until"),
        "pay_at_hotel": (rate or {}).get("pay_at_hotel", best.get("pay_at_hotel")),
        "pay_online": (rate or {}).get("pay_online", best.get("pay_online")),
        "check_in_time": cin,
        "check_out_time": cout,
        "room_name": room_obj.get("room_name"),
        "offerpack_hash": (rate or {}).get("offerpack_hash"),
        "rate_id": (rate or {}).get("id"),
        "rate_price_amount": ((rate or {}).get("price") or {}).get("amount"),
        "mandatory_charges": _clean_facts((rate or {}).get("mandatory_charges")),
        "room_size_sqm": room_obj.get("room_size_sqm", best.get("room_size_sqm")),
        "max_occupancy": room_obj.get("max_occupancy"),
        "pet_allowed": _pet_fact(details),
        "amenities": _clean_facts(hotel_details.get("amenity_groups")),
        "checkout_ref": _clean_facts(row.get("checkout_ref")),
        "details_ref": {"product_type": "hotels", "hotel_id": hotel_id, "check_in": checkin.isoformat(),
                        "check_out": checkout.isoformat(), "adults": search_args.get("adults"),
                        "children_ages": search_args.get("children_ages") or []},
        "search_results_url": best.get("checkout_url") or row.get("checkout_url"),
        "provider_search_id": (row.get("checkout_ref") or {}).get("search_id"),
    }
    source_url = best.get("checkout_url") or (row.get("checkout_ref") or {}).get("fallback_url")
    exact_rate_id = (rate or {}).get("offerpack_hash") or (rate or {}).get("id") or supplier_id
    return Offer(
        offer_id=f"tutu:hotel:{hotel_id}:{exact_rate_id}:{checkin:%Y%m%d}:{checkout:%Y%m%d}", kind="hotel",
        item_id=f"tutu:hotel:{hotel_id}",
        destination_city=str((resolved_destination or {}).get("name") or search_args.get("city_name") or "") or None,
        destination_provider_id=str((resolved_destination or {}).get("geo_id") or search_args.get("geo_id") or "") or None,
        title=(str(row.get("name") or f"Отель в {search_args.get('city_name') or 'городе назначения'}")
               + (f" · {terms['room_name']}" if terms["room_name"] else "")),
        variant={"provider": "tutu", "provider_offer_id": str(exact_rate_id), "hotel_id": hotel_id,
                 "hotel_name": row.get("name"), "stars": row.get("stars"), "rating": row.get("rating"),
                 "address": row.get("address"), "price_amount": raw_amount, "price_basis": "stay_total",
                 "currency": "RUB", "terms": terms, "price_rounding": "rounded half-up to whole ruble"},
        quantity=1, unit_price_rub=total, price_unit="per_trip", units=1, total_rub=total,
        included=["breakfast"] if terms["breakfast_included"] is True else [],
        times=OfferTimes(start=start, end=end) if start is not None and end is not None else None,
        synthetic=False, source_refs=[f"tutu:hotel:{hotel_id}", f"mcp:search_hotels", *(
            ["mcp:get_offer_details"] if details else [])],
        provider="tutu", provider_offer_id=str(exact_rate_id), fetched_at=_dt(fetched_at), currency="RUB",
        data_mode="live", search_params=dict(search_args), source_url=source_url,
        revalidation={"tool": "get_offer_details", "args": terms["details_ref"]},
    )
