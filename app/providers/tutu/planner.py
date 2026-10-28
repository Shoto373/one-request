"""Bounded live planner for Russian origin cities to the fixed Sochi destination."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from contextvars import ContextVar
from contextlib import contextmanager
import os
import re

log = logging.getLogger(__name__)
from datetime import datetime, timezone, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.catalog.store import get_catalog
from app.state.models import Bundle, Claim, Check
from app.planning.query import MEAL_RANK, TripQuery, parse_meal, query_from_requirements, resolve_city
from app.providers.base import ProviderError
from app.providers.tutu.adapter import hotel_offer, transport_offer
from app.providers.tutu.client import TutuMCPClient, TutuMCPError
from app.product_scope import destination_ids, fixed_destination_id, is_worldwide_enabled
from app.planning.build import _best_transfer
from app.tools.search import search_hotels as search_ostrovok_hotels

_trace_recorder: ContextVar[Any] = ContextVar("tutu_trace_recorder", default=None)
_force_demo: ContextVar[bool] = ContextVar("tutu_force_demo", default=False)


@contextmanager
def demo_planning_context():
    """Keep fixed offline evals isolated even when the application is configured live."""
    token = _force_demo.set(True)
    try:
        yield
    finally:
        _force_demo.reset(token)


def use_trace_recorder(recorder):
    return _trace_recorder.set(recorder)


def reset_trace_recorder(token) -> None:
    _trace_recorder.reset(token)


async def _mcp_call(client: TutuMCPClient, tool: str, args: dict[str, Any]):
    recorder = _trace_recorder.get()
    if recorder:
        recorder({"step_type": "tool", "name": tool, "input": args, "output": None, "status": "calling"})
    try:
        result = await client.call(tool, args)
    except Exception as exc:
        if recorder:
            recorder({"step_type": "tool", "name": tool, "input": args,
                      "output": {"error": type(exc).__name__}, "status": "error"})
        raise
    if recorder:
        key = "offers" if tool in {"search_avia", "search_rail"} else "hotels" if tool == "search_hotels" else None
        rows = result.data.get(key) if key else None
        meta = result.data.get("meta") or {}
        compact_meta = {k: meta.get(k) for k in ("transport", "round_trip", "return_date", "page_size",
                                                    "total_returned", "total_matched", "has_more", "from", "to") if k in meta}
        if isinstance(meta.get("pricing"), dict):
            compact_meta["pricing"] = {k: meta["pricing"].get(k) for k in ("basis", "passengers") if k in meta["pricing"]}
        recorder({"step_type": "tool", "name": tool, "input": args,
                  "output": {"count": len(rows) if isinstance(rows, list) else None,
                             "meta": compact_meta}, "status": "ok"})
    return result


class TutuScopeError(ProviderError):
    pass


class TutuSearchEmpty(ProviderError):
    pass


class TutuPlaceResolutionError(ProviderError):
    """The provider needs the user to disambiguate or approve another city."""

    def __init__(self, message: str, *, candidate: str | None = None):
        super().__init__(message)
        self.candidate = candidate


class TutuOriginAmbiguous(TutuPlaceResolutionError):
    pass


class TutuOriginUnavailable(TutuPlaceResolutionError):
    pass


class TutuOriginOutsideRussia(TutuPlaceResolutionError):
    pass


def tutu_live_enabled() -> bool:
    return not _force_demo.get() and os.getenv("TRAVEL_DATA_PROVIDER", "demo").strip().lower() == "tutu"


def _mapped_requirements(requirements: dict[str, Any], state: Any) -> dict[str, Any]:
    req = dict(requirements)
    cat = get_catalog()
    destination_value = next((req.get(key) for key in ("destination", "dest", "destination_city", "to", "city")
                              if req.get(key) not in (None, "")), None)
    if not destination_value:
        if not is_worldwide_enabled():
            req["destination"] = fixed_destination_id()
    else:
        destination = resolve_city(cat, destination_value)
        if not is_worldwide_enabled() and destination not in destination_ids():
            names = ", ".join(cat.cities[city_id].name for city_id in destination_ids())
            raise TutuScopeError(f"Текущее покрытие прототипа включает только: {names}. Направление без вашего согласия не меняю.")
    return req


def _origin_resolution_error(error: Exception, origin_name: str) -> TutuPlaceResolutionError | None:
    text = getattr(error, "upstream_message", None) or ""
    low = text.lower()
    if "could not resolve" in low:
        if "share this name" in low or "which one" in low:
            return TutuOriginAmbiguous(
                f"Туту нашёл несколько городов с названием «{origin_name}». Уточните область или район отправления. {text}"
            )
        return TutuOriginAmbiguous(
            f"Туту не смог определить город отправления «{origin_name}». Уточните написание, область или район."
        )
    if "no avia_id" in low or "no rail" in low or "no railway" in low or "no train" in low:
        match = re.search(r"(?:answers this wording with|suggests)\s+[«\"]([^»\"]+)[»\"]", text, re.I)
        candidate = match.group(1).strip() if match else None
        suggestion = (f"Туту предлагает проверить город «{candidate}». " if candidate else "")
        return TutuOriginUnavailable(
            f"Туту не подтвердил отправление из «{origin_name}». {suggestion}Город не меняю автоматически; если согласны, укажите его явно.",
            candidate=candidate,
        )
    return None


def assert_tutu_scope(requirements: dict[str, Any], state: Any = None) -> None:
    _mapped_requirements(requirements, state)


def _parse_query(requirements: dict[str, Any], state: Any) -> TripQuery:
    req = _mapped_requirements(requirements, state)
    query, problems = query_from_requirements(get_catalog(), req, state, allow_external_origin=True)
    if query is None:
        raise TutuSearchEmpty("Для поиска Туту нужны точные даты и состав поездки. " + " ".join(problems))
    return query


def _origin_for_provider(requirements: dict[str, Any]) -> str:
    value = next((requirements.get(key) for key in ("origin", "origin_city", "from", "departure_city")
                  if requirements.get(key) not in (None, "")), None)
    if isinstance(value, dict):
        value = value.get("city") or value.get("name") or value.get("value") or value.get("id")
    if not isinstance(value, str) or not value.strip():
        raise TutuSearchEmpty("Уточните город отправления, чтобы проверить доступность Туту.")
    raw = value.strip()
    cat = get_catalog()
    city_id = resolve_city(cat, raw)
    if city_id in cat.cities:
        city = cat.cities[city_id]
        is_cyrillic = bool(re.search(r"[а-яё]", city.name.lower()))
        if city.country_code and city.country_code not in ("RU", "XX") and not is_cyrillic:
            raise TutuOriginOutsideRussia(
                f"Город отправления «{city.name}» находится вне России. В прототипе можно указать город отправления в России."
            )
        return city.name
    low = raw.lower()
    if low.endswith("ани"):
        return raw[:-3] + "ань"
    if low.endswith("ери"):
        return raw[:-3] + "ерь"
    if low.endswith("ерми"):
        return raw[:-4] + "ермь"
    if low.endswith("квы"):
        return raw[:-3] + "ква"
    if low.endswith("ары"):
        return raw[:-3] + "ара"
    if low.endswith("фы"):
        return raw[:-2] + "фа"
    if low.endswith("омы"):
        return raw[:-3] + "ома"
    if low.endswith("бурга") or low.endswith("града") or low.endswith("инска") or low.endswith("ова") or low.endswith("ева") or low.endswith("ежа"):
        return raw[:-1]
    return raw

def _explicitly_non_russian(place: Any) -> bool:
    if not isinstance(place, dict):
        return False
    code = str(place.get("country_code") or place.get("country_iso2") or "").upper()
    if code:
        return code != "RU"
    country = str(place.get("country") or "").strip().lower()
    return bool(country and country not in {"россия", "russia", "российская федерация", "russian federation"})


_SERVICE_CLASS = {"y": "Y", "economy": "Y", "эконом": "Y", "s": "S", "premium": "S", "premium_economy": "S",
                  "комфорт": "S", "c": "C", "business": "C", "бизнес": "C", "f": "F", "first": "F", "первый": "F"}


def _service_class(value: Any) -> str | None:
    """IATA service class for search_avia from a requirement value (Y/S/C/F, economy/business/...)."""
    if not isinstance(value, str):
        return None
    return _SERVICE_CLASS.get(value.strip().lower().replace(" ", "_").replace("-", "_"))


def _service_classes(requirements: dict[str, Any]) -> tuple[str | None, str | None]:
    """(outbound, return) flight classes; one value applies to both directions."""
    value = requirements.get("flight_class")
    if isinstance(value, dict):
        return _service_class(value.get("outbound")), _service_class(value.get("return"))
    return _service_class(value), _service_class(value)


def _ostrovok_hotel_url(row: dict[str, Any], city_id: str, checkin, checkout, adults: int) -> str | None:
    """Ostrovok page of a Tutu hotel (Tutu sells Ostrovok inventory); Ostrovok resolves the alias itself."""
    from app.catalog.ostrovok_live import CITY_SLUG_MAP
    alias, hotel_id = row.get("alias"), row.get("hotel_id") or row.get("hotel_geo_id")
    if not alias or not hotel_id or city_id not in CITY_SLUG_MAP:
        return None
    country, city = CITY_SLUG_MAP[city_id]
    return (f"https://ostrovok.ru/hotel/{country}/{city}/mid{hotel_id}/{alias}/"
            f"?dates={checkin:%d.%m.%Y}-{checkout:%d.%m.%Y}&guests={adults}")


def _return_departure(transports: list[Any]) -> datetime | None:
    """Departure of the way back: the second offer, or the return leg of a round-trip flight."""
    if len(transports) > 1:
        return transports[1].times.start
    legs = transports[0].variant.get("legs") or []
    back = next((leg for leg in legs if leg.get("label") == "return"), None)
    segments = (back or {}).get("segments") or []
    raw = (segments[0].get("departure_at") if segments else None) or (back or {}).get("departure_at")
    try:
        return datetime.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def _estimated_transfer(cat: Any, hub_id: str, query: TripQuery, at: datetime, direction: str,
                        hotel: Any, mode: str):
    """Taxi estimate between the arrival hub and the hotel; not bookable, clearly marked as an estimate."""
    from math import asin, cos, radians, sin, sqrt
    from app.catalog.models import Offer, OfferTimes

    hub = cat.hubs.get(hub_id) if hub_id else None
    city = cat.cities.get(query.dest_city)
    km = 20.0
    if hub is not None and city is not None and (hub.city_id == query.dest_city or hub_id in city.hub_ids):
        dlat, dlon = radians(city.coord.lat - hub.coord.lat), radians(city.coord.lon - hub.coord.lon)
        a = sin(dlat / 2) ** 2 + cos(radians(hub.coord.lat)) * cos(radians(city.coord.lat)) * sin(dlon / 2) ** 2
        km = max(5.0, round(2 * 6371 * asin(sqrt(a)) * 1.3, 1))  # road ≈ 1.3 × straight line
    minutes = int(15 + km * 1.5)
    cars = 2 if query.party.people > 4 else 1
    price = int(round((800 + 40 * km) / 10) * 10)
    place = "вокзал" if mode == "rail" else "аэропорт"
    title = (f"Трансфер {place} → отель (ориентировочно), ~{km:g} км, ~{minutes} мин" if direction == "to_hotel"
             else f"Трансфер отель → {place} (ориентировочно), ~{km:g} км, ~{minutes} мин")
    start = at if direction == "to_hotel" else at - timedelta(minutes=minutes)
    return Offer(
        offer_id=f"est_transfer_{direction}_{hashlib.sha256(hotel.offer_id.encode()).hexdigest()[:10]}_{start:%Y%m%d%H%M}",
        kind="transfer", item_id=f"estimate:transfer:{query.dest_city}:{direction}", title=title,
        variant={"title": title, "mode": "taxi", "estimate": True, "direction": direction, "distance_km": km,
                 "duration_min": minutes, "provider": "Оценка агента",
                 "note": "Только предложение: ориентировочная цена такси, не бронируется и не входит в оформление."},
        quantity=cars, unit_price_rub=price, price_unit="per_vehicle", units=1, total_rub=price * cars,
        times=OfferTimes(start=start, end=start + timedelta(minutes=minutes)),
        synthetic=True, provider="estimate", data_mode="estimated", source_url=None,
        source_refs=[f"estimate:transfer:{hub_id or query.dest_city}"],
    )


def _party_for_avia(query: TripQuery) -> tuple[int, int, int]:
    adults = query.party.adults + sum(age >= 12 for age in query.party.children_ages)
    children = sum(2 <= age <= 11 for age in query.party.children_ages)
    infants = sum(age < 2 for age in query.party.children_ages)
    return adults, children, infants


def _rows(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    result = data.get(key)
    return [x for x in result if isinstance(x, dict)] if isinstance(result, list) else []


def _local_arrival(offer, timezone: str):
    return offer.times.end.astimezone(ZoneInfo(timezone)).date() if offer.times else None


def _transport_limit_issue(offer, query: TripQuery) -> str | None:
    legs = offer.variant.get("legs") or []
    stop_counts, durations = [], []
    for leg in legs:
        segments = leg.get("segments") or []
        stop_counts.append(max(0, len(segments) - 1) if segments else None)
        durations.append(leg.get("duration_min"))
    issues = []
    if query.max_stops is not None and (not stop_counts or any(x is None or x > query.max_stops for x in stop_counts)):
        issues.append(f"число пересадок больше {query.max_stops} или не подтверждено")
    if query.max_travel_min is not None and (not durations or any(not isinstance(x, (int, float)) or x > query.max_travel_min for x in durations)):
        issues.append(f"время в пути превышает {query.max_travel_min // 60} ч или не подтверждено")
    return "; ".join(issues) or None


def _hard_lodging_issues(item, query: TripQuery, state: Any) -> list[str]:
    kinds = {r.key: r.kind for r in getattr(state, "requirements", [])}
    terms = item.variant.get("terms", {})
    issues = []
    star_key = next((k for k in ("lodging_stars", "stars", "hotel_stars") if k in kinds), None)
    if query.min_stars and (kinds.get(star_key) == "hard" or (star_key is None and query.lodging_hard)):
        stars = item.variant.get("stars")
        if not isinstance(stars, (int, float)) or stars < query.min_stars:
            issues.append(f"звёздность не подтверждает минимум {query.min_stars}")
    meal_key = next((k for k in ("meal_preference", "meal", "meals") if k in kinds), None)
    if query.meal and (kinds.get(meal_key) == "hard" or (meal_key is None and query.lodging_hard)):
        meal = parse_meal(terms.get("meal_name"))
        if meal is None or MEAL_RANK.get(meal, -1) < MEAL_RANK[query.meal]:
            issues.append(f"питание не подтверждает требование {query.meal}")
    return issues


def _has_24h_reception(hotel_offer) -> bool:
    terms = hotel_offer.variant.get("terms", {})
    chunks = []
    for group in terms.get("amenities") or []:
        chunks.extend(str(x) for x in group.get("amenities") or [])
    text = " ".join(chunks).lower().replace("ё", "е")
    return any(phrase in text for phrase in ("круглосуточная стойка регистрации", "24-hour front desk", "24h reception"))


def _wire(bundle: Bundle) -> dict[str, Any]:
    return bundle.model_dump(mode="json")


async def _search_tutu_core(requirements: dict[str, Any], state: Any = None) -> list[dict[str, Any]]:
    """Search only the requested date window via Tutu MCP; all quantities come from provider results."""
    query = _parse_query(requirements, state)
    catalog = get_catalog()
    destination_name = catalog.cities[query.dest_city].name
    city_tz = catalog.cities[query.dest_city].timezone
    origin_name = _origin_for_provider(requirements)
    adults, children, infants = _party_for_avia(query)
    hotel_party = {"adults": query.party.adults, "children_ages": list(query.party.children_ages)}
    source_errors: list[str] = []
    place_errors: list[TutuPlaceResolutionError] = []
    hard_lodging_rejections: list[str] = []
    flight_rows: list[dict[str, Any]] = []
    flight_out_rows: list[dict[str, Any]] = []
    flight_back_rows: list[dict[str, Any]] = []
    outbound_rows: list[dict[str, Any]] = []
    return_rows: list[dict[str, Any]] = []
    search_facts: dict[str, dict[str, Any]] = {}
    async with TutuMCPClient() as client:
        tasks: dict[str, Any] = {}
        if "flight" in query.kinds:
            class_out, class_back = _service_classes(requirements)
            base_avia = {"origin": origin_name, "destination": destination_name, "adults": adults, "children": children,
                         "infants": infants, "page": 1, "page_size": 5, "sort": "price_asc", "view": "compact"}
            if query.limits_hard and query.max_stops == 0:
                base_avia["direct_only"] = True
            if class_out == class_back:
                avia_args = {**base_avia, "departure_date": query.depart_date.isoformat(),
                             "return_date": query.return_date.isoformat()}
                if class_out:
                    avia_args["service_class"] = class_out
                tasks["avia"] = _mcp_call(client, "search_avia", avia_args)
            else:
                # Different classes there and back (e.g. business out, economy back): two one-way searches.
                tasks["avia_out"] = _mcp_call(client, "search_avia", {
                    **base_avia, "departure_date": query.depart_date.isoformat(), "service_class": class_out or "Y"})
                tasks["avia_back"] = _mcp_call(client, "search_avia", {
                    **base_avia, "origin": destination_name, "destination": origin_name,
                    "departure_date": query.return_date.isoformat(), "service_class": class_back or "Y"})
        if "train" in query.kinds:
            out_args = {"origin": origin_name, "destination": destination_name, "departure_date": query.depart_date.isoformat(),
                        "passengers": query.party.people, "page": 1, "page_size": 5, "sort": "price_asc", "view": "compact"}
            back_args = {"origin": destination_name, "destination": origin_name, "departure_date": query.return_date.isoformat(),
                         "passengers": query.party.people, "page": 1, "page_size": 5, "sort": "price_asc", "view": "compact"}
            if query.limits_hard and query.max_stops == 0:
                out_args["direct_only"] = True
                back_args["direct_only"] = True
            tasks["rail_out"], tasks["rail_back"] = _mcp_call(client, "search_rail", out_args), _mcp_call(client, "search_rail", back_args)
        keys = list(tasks)
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for key, result in zip(keys, results):
            if isinstance(result, Exception):
                place_error = _origin_resolution_error(result, origin_name)
                if place_error:
                    place_errors.append(place_error)
                source_errors.append(f"{key}: {getattr(result, 'upstream_message', None) or 'техническая ошибка поиска'}")
                continue
            meta = result.data.get("meta") or {}
            if key.endswith("_back"):
                pass  # the return search starts at the destination, which may be abroad
            elif is_worldwide_enabled():
                if _explicitly_non_russian(meta.get("from")):
                    raise TutuOriginOutsideRussia("Туту определил город отправления за пределами России. Укажите российский город отправления.")
            else:
                if any(_explicitly_non_russian(meta.get(side)) for side in ("from", "to")):
                    raise TutuOriginOutsideRussia("Туту определил один из городов маршрута за пределами России. Укажите российский город отправления.")
            search_facts[key] = {"args": result.args, "fetched_at": result.fetched_at,
                                 "resolved_from": meta.get("from"),
                                 "resolved_to": meta.get("to")}
            if key == "avia":
                flight_rows = _rows(result.data, "offers")
            elif key == "avia_out":
                flight_out_rows = _rows(result.data, "offers")
            elif key == "avia_back":
                flight_back_rows = _rows(result.data, "offers")
            elif key == "rail_out":
                outbound_rows = _rows(result.data, "offers")
            elif key == "rail_back":
                return_rows = _rows(result.data, "offers")

        flights = [
            item for row in flight_rows if not row.get("is_multi_pnr")
            if (item := transport_offer(row, kind="flight", direction="round_trip",
                                         search_args=search_facts["avia"]["args"],
                                         fetched_at=search_facts["avia"]["fetched_at"],
                                         resolved_places={"from": search_facts["avia"].get("resolved_from"),
                                                          "to": search_facts["avia"].get("resolved_to")})) is not None
        ]
        def one_way_flights(rows: list[dict[str, Any]], key: str, direction: str) -> list[Any]:
            return [
                item for row in rows if not row.get("is_multi_pnr")
                if (item := transport_offer(row, kind="flight", direction=direction,
                                             search_args=search_facts[key]["args"],
                                             fetched_at=search_facts[key]["fetched_at"],
                                             resolved_places={"from": search_facts[key].get("resolved_from"),
                                                              "to": search_facts[key].get("resolved_to")})) is not None
            ]
        flights_out = one_way_flights(flight_out_rows, "avia_out", "outbound") if flight_out_rows else []
        flights_back = one_way_flights(flight_back_rows, "avia_back", "return") if flight_back_rows else []
        outbound = [
            item for row in outbound_rows
            if (item := transport_offer(row, kind="train", direction="outbound",
                                         search_args=search_facts["rail_out"]["args"],
                                         fetched_at=search_facts["rail_out"]["fetched_at"],
                                         resolved_places={"from": search_facts["rail_out"].get("resolved_from"),
                                                          "to": search_facts["rail_out"].get("resolved_to")})) is not None
        ]
        inbound = [
            item for row in return_rows
            if (item := transport_offer(row, kind="train", direction="return",
                                         search_args=search_facts["rail_back"]["args"],
                                         fetched_at=search_facts["rail_back"]["fetched_at"],
                                         resolved_places={"from": search_facts["rail_back"].get("resolved_from"),
                                                          "to": search_facts["rail_back"].get("resolved_to")})) is not None
        ]
        if query.limits_hard:
            flights = [item for item in flights if _transport_limit_issue(item, query) is None]
            flights_out = [item for item in flights_out if _transport_limit_issue(item, query) is None]
            flights_back = [item for item in flights_back if _transport_limit_issue(item, query) is None]
            outbound = [item for item in outbound if _transport_limit_issue(item, query) is None]
            inbound = [item for item in inbound if _transport_limit_issue(item, query) is None]
        transport_choices: list[tuple[str, list[Any], int]] = []
        for item in flights[:5]:
            duration = int(item.variant.get("duration_min") or 0)
            transport_choices.append(("avia", [item], duration))
        for out in flights_out[:3]:
            for back in flights_back[:3]:
                duration = int(out.variant.get("duration_min") or 0) + int(back.variant.get("duration_min") or 0)
                transport_choices.append(("avia", [out, back], duration))
        for out in outbound[:3]:
            for back in inbound[:3]:
                duration = int(out.variant.get("duration_min") or 0) + int(back.variant.get("duration_min") or 0)
                transport_choices.append(("rail", [out, back], duration))
        if not transport_choices:
            if is_worldwide_enabled():
                log.info("Tutu returned no transport for %s -> %s, falling back to worldwide catalog", origin_name, destination_name)
                from app.planning.build import build_trip_bundles
                from app.planning.models import bundle_to_wire
                res = build_trip_bundles(catalog, query)
                if res.bundles:
                    return [bundle_to_wire(b) for b in res.bundles]
            if place_errors:
                raise place_errors[0]
            failed = "; ".join(source_errors)
            if failed:
                raise TutuMCPError("Не удалось выполнить live-поиск: " + failed)
            if query.limits_hard:
                raise TutuSearchEmpty("Туту не вернул транспорт, подтверждённо соответствующий жёстким ограничениям времени или пересадок.")
            raise TutuSearchEmpty("Туту не вернул предложений на указанные даты и состав. Это означает отсутствие результата поиска на Туту, а не отсутствие поездок вообще.")

        arrival_dates = sorted({d for _, items, _ in transport_choices[:10]
                                if (d := _local_arrival(items[0], city_tz)) is not None and d < query.return_date})
        hotel_by_checkin: dict[str, list[Any]] = {}
        hotel_details_errors: dict[str, str] = {}
        hotel_details_attempted = 0
        cat = get_catalog()
        for checkin in arrival_dates[:3]:
            if True:
                hotel_args: dict[str, Any] = {
                    "city_name": destination_name, "check_in": checkin.isoformat(), "check_out": query.return_date.isoformat(),
                    **hotel_party, "page": 1, "page_size": 5, "view": "compact",
                }
                if query.party.pets:
                    hotel_args["hotel_amenities"] = ["pets"]
                try:
                    response = await _mcp_call(client, "search_hotels", hotel_args)
                    search_facts[f"hotel:{checkin.isoformat()}"] = {
                        "args": response.args, "fetched_at": response.fetched_at,
                        "resolved_destination": (response.data.get("meta") or {}).get("resolved_geo"),
                    }
                    hotel_rows = _rows(response.data, "hotels")
                    normalized = []
                    for row in hotel_rows[:3]:
                        details = None
                        hotel_id = row.get("hotel_id") or row.get("hotel_geo_id")
                        detail_args = {"product_type": "hotels", "hotel_id": hotel_id, "check_in": checkin.isoformat(),
                                       "check_out": query.return_date.isoformat(), **hotel_party, "view": "compact"}
                        hotel_details_attempted += 1
                        try:
                            detail_response = await _mcp_call(client, "get_offer_details", detail_args)
                            details = detail_response.data
                        except Exception:
                            hotel_details_errors[f"{checkin.isoformat()}:{hotel_id}"] = "детали условий недоступны"
                        item = hotel_offer(row, search_args=hotel_args, fetched_at=response.fetched_at,
                                           details=details, city_timezone=city_tz,
                                           resolved_destination=search_facts[f"hotel:{checkin.isoformat()}"]["resolved_destination"])
                        if item is not None:
                            lodging_issues = _hard_lodging_issues(item, query, state)
                            if lodging_issues:
                                hard_lodging_rejections.extend(lodging_issues)
                                continue
                            pet_status = item.variant["terms"].get("pet_allowed")
                            if query.party.pets and pet_status is False:
                                continue
                            ostrovok_url = _ostrovok_hotel_url(row, query.dest_city, checkin, query.return_date,
                                                               query.party.adults)
                            if ostrovok_url:
                                item.source_url = ostrovok_url
                            normalized.append(item)
                    hotel_by_checkin[checkin.isoformat()] = normalized
                except Exception:
                    log.warning("Tutu search_hotels failed for %s on %s", destination_name, checkin, exc_info=True)
            if not hotel_by_checkin.get(checkin.isoformat()):
                # Tutu has no hotels for this date: fall back to the Ostrovok catalog list.
                ostrovok_list = search_ostrovok_hotels(cat, query.dest_city, checkin, query.return_date, query.party)
                if ostrovok_list:
                    hotel_by_checkin[checkin.isoformat()] = ostrovok_list[:4]

        candidates: list[tuple[int, int, int, str, list[Any]]] = []
        for mode, transports, duration in transport_choices:
            checkin = _local_arrival(transports[0], city_tz)
            if checkin is None or checkin >= query.return_date:
                continue
            for hotel in hotel_by_checkin.get(checkin.isoformat(), []):
                dest_city_info = cat.cities.get(query.dest_city)
                if mode == "rail":
                    t_text = f"{transports[0].title} {transports[0].variant.get('arrival_station', '')}".lower()
                    if "адлер" in t_text:
                        arr_hub = "st_aer_adler"
                    else:
                        arr_hub = "st_aer_sochi" if query.dest_city == "AER" else (
                            next((h for h in (dest_city_info.hub_ids if dest_city_info else []) if cat.hubs[h].kind == "station"), "st_aer_sochi")
                        )
                    ret_t = transports[1] if len(transports) > 1 else transports[0]
                    ret_text = f"{ret_t.title} {ret_t.variant.get('departure_station', '')}".lower()
                    if "адлер" in ret_text:
                        dep_hub = "st_aer_adler"
                    else:
                        dep_hub = "st_aer_sochi" if query.dest_city == "AER" else (
                            next((h for h in (dest_city_info.hub_ids if dest_city_info else []) if cat.hubs[h].kind == "station"), "st_aer_sochi")
                        )
                else:
                    arr_hub = next((h for h in (dest_city_info.hub_ids if dest_city_info else []) if cat.hubs[h].kind == "airport"), "AER")
                    dep_hub = arr_hub

                if arr_hub not in cat.hubs:
                    arr_hub = "st_aer_sochi" if mode == "rail" else "AER"
                if dep_hub not in cat.hubs:
                    dep_hub = "st_aer_sochi" if mode == "rail" else "AER"

                pickup = transports[0].times.end + timedelta(minutes=30)
                to_hotel = _best_transfer(cat, arr_hub, hotel.item_id, query, pickup, "to_hotel", prefer_mode="taxi")
                return_start = _return_departure(transports)
                be_at_hub = return_start - timedelta(minutes=90) if return_start else None
                to_hub = (_best_transfer(cat, dep_hub, hotel.item_id, query, be_at_hub, "to_hub", prefer_mode="taxi")
                          if be_at_hub else None)
                # Live hotels are not in the catalog, so there is no bookable transfer: show an estimate.
                if to_hotel is None:
                    to_hotel = _estimated_transfer(cat, arr_hub, query, pickup, "to_hotel", hotel, mode)
                if to_hub is None and be_at_hub is not None:
                    to_hub = _estimated_transfer(cat, dep_hub, query, be_at_hub, "to_hub", hotel, mode)

                # Timeline order: there, transfer, hotel, transfer, back.
                outbound_items = transports[:1]
                back_items = transports[1:]
                items = [*outbound_items, *([to_hotel] if to_hotel else []), hotel,
                         *([to_hub] if to_hub else []), *back_items]
                subtotal = sum(item.total_rub for item in items)
                refundable = sum(
                    item.variant.get("refundable") is True or item.variant.get("terms", {}).get("free_cancellation") is True if item.kind == "hotel"
                    else item.variant.get("cancellation_status") is True for item in transports
                )
                candidates.append((subtotal, duration, -refundable, mode, items))
        if not candidates:
            if hotel_details_attempted and len(hotel_details_errors) == hotel_details_attempted:
                raise TutuMCPError("Техническая ошибка: не удалось получить детали тарифов размещения у Туту.")
            if hard_lodging_rejections:
                raise TutuSearchEmpty("Не найден отель, подтверждающий жёсткое условие: " + "; ".join(dict.fromkeys(hard_lodging_rejections)) + ".")
            if query.party.pets:
                raise TutuSearchEmpty("На даты не найдено отелей, которые Туту подтвердил как принимающие животных. Проверьте другой состав или условия; другие данные не подставляю.")
            if source_errors and len(source_errors) >= len(query.kinds):
                raise TutuMCPError("Все запрошенные поиски Туту завершились технической ошибкой")
            raise TutuSearchEmpty("Реальный поиск Туту не вернул комплект из транспорта и отеля на выбранные даты.")

        # Produce at most three real, distinct combinations; never copy one result under fake labels.
        sorted_price = sorted(candidates, key=lambda x: (x[0], x[1], tuple(i.offer_id for i in x[4])))
        sorted_time = sorted(candidates, key=lambda x: (x[1], x[0], tuple(i.offer_id for i in x[4])))
        sorted_flex = sorted(candidates, key=lambda x: (x[2], x[0], x[1], tuple(i.offer_id for i in x[4])))
        selected: list[tuple[str, tuple[int, int, int, str, list[Any]]]] = []
        for label, pool in (("Дешевле из найденных", sorted_price), ("Быстрее из найденных", sorted_time),
                            ("Больше подтверждённых условий отмены", sorted_flex)):
            if label.startswith("Больше"):
                top = pool[0]
                if top[2] == 0:  # no positive confirmed flexibility in any result
                    continue
            else:
                top = pool[0]
            signature = tuple(item.offer_id for item in top[4])
            if any(tuple(item.offer_id for item in old[1][4]) == signature for old in selected):
                continue
            selected.append((label, top))
            if len(selected) == 3:
                break

        # Same transport often wins every pool: offer other real hotels so there is a choice.
        def hotel_ids(candidate) -> set[str]:
            return {i.item_id for i in candidate[4] if i.kind == "hotel"}

        def hotel_stars(candidate) -> int:
            return max((int(i.variant.get("stars") or 0) for i in candidate[4] if i.kind == "hotel"), default=0)

        used_hotels = set().union(*(hotel_ids(c) for _, c in selected)) if selected else set()
        for label, key in (("Отель выше классом", lambda x: (-hotel_stars(x), x[0])), ("Другой отель", lambda x: x[0])):
            pool = [c for c in candidates if not (hotel_ids(c) & used_hotels)]
            if not pool or len(selected) >= 4:
                break
            top = sorted(pool, key=key)[0]
            selected.append((label, top))
            used_hotels |= hotel_ids(top)

        bundles: list[dict[str, Any]] = []
        fetched_at = datetime.now(timezone.utc).isoformat()
        for label, candidate in selected:
            subtotal, duration, _flex, _mode, items = candidate
            refs = [f"offer:{item.offer_id}" for item in items]
            checks = [Check(kind="availability", status="ok", refs=refs,
                            text="Транспорт подтверждён поиском Tutu MCP; отель подобран на Островке.").model_dump(mode="json")]
            transfers_in_bundle = [i for i in items if i.kind == "transfer"]
            estimated_transfer = any(t.variant.get("estimate") for t in transfers_in_bundle)
            if transfers_in_bundle and estimated_transfer:
                checks.append(Check(kind="transfer_fit", status="warn", refs=[f"offer:{t.offer_id}" for t in transfers_in_bundle],
                                    text="Трансфер — только предложение агента: ориентировочная цена такси по расстоянию, "
                                         "не бронируется и оплачивается на месте.").model_dump(mode="json"))
            elif transfers_in_bundle:
                checks.append(Check(kind="transfer_fit", status="ok", refs=[f"offer:{t.offer_id}" for t in transfers_in_bundle],
                                    text=f"Трансфер ({transfers_in_bundle[0].variant.get('tariff_name', 'Комфорт')}) включён в сумму поездки (в отель и обратно).").model_dump(mode="json"))
            else:
                checks.append(Check(kind="transfer_fit", status="warn", refs=refs,
                                    text="Трансфер не подобран для данного маршрута.").model_dump(mode="json"))
            hotel = next(item for item in items if item.kind == "hotel")
            if hotel.variant.get("terms", {}).get("mandatory_charges") is None:
                checks.append(Check(kind="price_completeness", status="warn", refs=[f"offer:{hotel.offer_id}"],
                                    text="Туту не указал отдельно возможные обязательные доплаты отеля; сумма карточки может быть неполной.").model_dump(mode="json"))
            arrival = items[0].times.end.astimezone(ZoneInfo(city_tz))
            room_start = hotel.times.start.astimezone(ZoneInfo(city_tz)) if hotel.times else None
            hotel_ref = f"offer:{hotel.offer_id}"
            if room_start and arrival.date() == room_start.date() and arrival < room_start:
                checks.append(Check(kind="early_arrival", status="warn", refs=[hotel_ref],
                                    text=f"Прибытие {arrival:%d.%m %H:%M}, стандартное время заезда {room_start:%H:%M}. Ранний заезд можно запросить в отеле или оставить багаж на ресепшн.",
                                    options=["Уточнить ранний заезд у отеля", "Изменить время прибытия"]).model_dump(mode="json"))
            elif room_start and arrival.date() == room_start.date():
                if _has_24h_reception(hotel):
                    checks.append(Check(kind="late_arrival", status="ok", refs=[hotel_ref],
                                        text=f"Прибытие {arrival:%d.%m %H:%M}; в деталях отеля указана круглосуточная стойка регистрации.").model_dump(mode="json"))
                else:
                    checks.append(Check(kind="late_arrival", status="warn", refs=[hotel_ref],
                                        text=f"Прибытие {arrival:%d.%m %H:%M}; время заезда отеля {room_start:%H:%M}. Часы работы стойки Туту не сообщил.").model_dump(mode="json"))
            else:
                checks.append(Check(kind="checkin_date", status="warn", refs=[hotel_ref],
                                    text="Время прибытия или время заселения не подтверждено для проверки покрытия проживания.").model_dump(mode="json"))
            if any(item.data_mode == "estimated" for item in items):
                checks.append(Check(kind="availability", status="warn", refs=refs,
                                    text="ЖД-сумма — цена за место, умноженная на состав для одного выбранного тарифа; отдельная детская цена и вместимость конкретного тарифа не подтверждены.").model_dump(mode="json"))
            if not query.limits_hard:
                limit_issues = [issue for item in items if item.kind in ("flight", "train")
                                if (issue := _transport_limit_issue(item, query)) is not None]
                if limit_issues:
                    checks.append(Check(kind="transport_preference", status="warn", refs=refs,
                                        text="Пожелание к дороге не подтверждено: " + "; ".join(dict.fromkeys(limit_issues)) + ".").model_dump(mode="json"))
            if query.party.pets:
                checks.append(Check(kind="pets", status="block", refs=refs,
                                    text="Туту не подтвердил правила перевозки питомца на выбранном предложении и допустимый вес в отеле. Отельные результаты фильтровались по pet-friendly, но весовой лимит не вернулся.",
                                    options=["Проверить правила выбранного перевозчика и отеля перед продолжением"]).model_dump(mode="json"))
            kinds = {r.key: r.kind for r in getattr(state, "requirements", [])}
            star_key = next((k for k in ("lodging_stars", "stars", "hotel_stars") if k in kinds), None)
            if query.min_stars and kinds.get(star_key) != "hard":
                stars = hotel.variant.get("stars")
                if not isinstance(stars, (int, float)) or stars < query.min_stars:
                    checks.append(Check(kind="lodging_preference", status="warn", refs=[hotel_ref],
                                        text=f"Отель не подтвердил мягкое пожелание по категории: минимум {query.min_stars} звёзд.").model_dump(mode="json"))
            meal_key = next((k for k in ("meal_preference", "meal", "meals") if k in kinds), None)
            if query.meal and kinds.get(meal_key) != "hard":
                actual_meal = parse_meal(hotel.variant.get("terms", {}).get("meal_name"))
                if actual_meal is None or MEAL_RANK.get(actual_meal, -1) < MEAL_RANK[query.meal]:
                    checks.append(Check(kind="lodging_preference", status="warn", refs=[hotel_ref],
                                        text=f"Отель не подтвердил мягкое пожелание по питанию: {query.meal}.").model_dump(mode="json"))
            if query.budget_rub is not None:
                if subtotal > query.budget_rub:
                    status = "block" if query.budget_hard else "warn"
                    text = f"Подтверждённая сумма билетов и отеля {subtotal:,} ₽ выше бюджета {query.budget_rub:,} ₽; трансфер в неё не входит.".replace(",", " ")
                elif query.budget_hard:
                    status = "block"
                    text = f"Билеты и отель составляют {subtotal:,} ₽, но проверить жёсткий бюджет {query.budget_rub:,} ₽ целиком нельзя: стоимость трансфера отсутствует.".replace(",", " ")
                else:
                    status = "warn"
                    text = f"Билеты и отель составляют {subtotal:,} ₽; общий итог с трансфером неизвестен.".replace(",", " ")
                checks.append(Check(kind="budget", status=status, refs=refs, text=text).model_dump(mode="json"))
            relevant_errors = [
                err for err in source_errors
                if not (mode == "rail" and err.startswith("avia:"))
                and not (mode == "avia" and err.startswith("rail_"))
            ]
            if relevant_errors:
                checks.append(Check(kind="availability", status="warn", refs=refs,
                                    text="Не все включённые в поиск виды транспорта ответили: " + "; ".join(relevant_errors) + ".").model_dump(mode="json"))
            bundle = Bundle(
                id=f"tutu-{hashlib.sha256('|'.join(i.offer_id for i in items).encode()).hexdigest()[:12]}",
                label=label, items=items, total_rub=subtotal,
                checks=checks,
                advantages=[Claim(text=f"Фактический маршрут и цена получены от Туту; сравнение основано на найденных вариантах.", refs=refs)],
                tradeoffs=[Claim(text=("Билеты и отель — реальные цены Туту; трансфер — ориентировочная оценка агента."
                                       if estimated_transfer else "В стоимость входят только позиции Туту в карточке."), refs=refs)],
                risks=[Claim(text=check["text"], refs=check["refs"] or refs) for check in checks if check["status"] in {"warn", "block"}],
                price_note=("Билеты и отель — актуальные цены за указанный состав и даты; трансфер — ориентировочная оценка."
                            if estimated_transfer else
                            "Сумма live-предложений за указанный состав и даты; трансфер не включён. Это не окончательная стоимость поездки."),
            )
            bundles.append(_wire(bundle))
        recorder = _trace_recorder.get()
        if recorder:
            recorder({"step_type": "tool", "name": "validate_tutu_bundle",
                      "input": {"bundle_ids": [bundle["id"] for bundle in bundles]},
                      "output": {"checks": [{"bundle_id": bundle["id"],
                                              "statuses": [check["status"] for check in bundle["checks"]]}
                                             for bundle in bundles]},
                      "status": "ok"})
        return bundles


async def build_tutu_bundles(requirements: dict[str, Any], state: Any = None) -> list[dict[str, Any]]:
    """Search with Tutu live MCP; seamlessly falls back to worldwide catalog when live search is unavailable or unsupported."""
    query = _parse_query(requirements, state)
    catalog = get_catalog()
    try:
        try:
            return await _search_tutu_core(requirements, state)
        except (TutuOriginOutsideRussia, TutuPlaceResolutionError, TutuSearchEmpty):
            raise
        except (Exception, asyncio.CancelledError) as exc:
            # The MCP handshake sometimes drops (ExceptionGroup or a CancelledError from the transport's
            # cancel scope): retry once with a fresh session. A real cancellation of this task still propagates.
            task = asyncio.current_task()
            if isinstance(exc, asyncio.CancelledError) and task is not None and task.cancelling():
                raise
            log.warning("Tutu live search failed (%s), retrying once", type(exc).__name__)
            return await _search_tutu_core(requirements, state)
    except TutuOriginOutsideRussia:
        raise
    except BaseException as exc:
        if is_worldwide_enabled():
            log.warning("Tutu live search failed or unsupported for route (%s: %s). Falling back to worldwide catalog.", type(exc).__name__, exc)
            from app.planning.build import build_trip_bundles
            from app.planning.models import bundle_to_wire
            res = build_trip_bundles(catalog, query)
            if res.bundles:
                return [bundle_to_wire(b) for b in res.bundles]
        raise


async def replan_tutu(state: Any, changes: dict[str, Any]) -> dict[str, Any]:
    """Re-search the current trip with the full active requirements and accumulated change diff."""
    requirements = {r.key: r.value for r in state.requirements if r.kind != "unknown"}
    old = next((b for b in state.bundles if b.id == state.selected_bundle_id), None)
    if old is None:
        return {"bundle": None, "bundles": [], "kept": [], "replaced": [], "notes": ["Нет текущего выбранного комплекта для пересборки."],
                "price_delta_rub": None, "input_changes": changes}
    try:
        results = await build_tutu_bundles(requirements, state)
    except TutuSearchEmpty as exc:
        return {"bundle": None, "bundles": [], "selected_bundle_id": None,
                "kept": [], "replaced": [], "price_delta_rub": None,
                "notes": [str(exc)], "input_changes": changes}
    matching = next((b for b in results if b.get("label") == old.label), results[0] if results else None)
    if matching is None:
        return {"bundle": None, "bundles": [], "kept": [], "replaced": [],
                "notes": ["После нового live-поиска подходящего комплекта не найдено."],
                "price_delta_rub": None, "input_changes": changes}
    # The user selected this conceptual bundle; keep its identity across a trip
    # version while each concrete supplier Offer receives its own new identity.
    matching["id"] = old.id
    old_by_obj = {(item.kind, item.item_id): item for item in old.items}
    new_items = [type(old.items[0]).model_validate(x) for x in matching.get("items", [])] if old.items else []
    kept, replaced = [], []
    for item in new_items:
        prior = old_by_obj.get((item.kind, item.item_id))
        if prior and prior.offer_id == item.offer_id:
            kept.append({"offer_id": item.offer_id, "why": "тот же объект и то же предложение повторно найдены"})
        elif prior:
            replaced.append({"ref": f"offer:{prior.offer_id}→offer:{item.offer_id}",
                             "from": {"offer_id": prior.offer_id}, "to": {"offer_id": item.offer_id},
                             "why": "объект тот же, но supplier Offer после пересборки другой"})
        else:
            replaced.append({"ref": f"offer:—→{item.offer_id}", "from": {}, "to": {"offer_id": item.offer_id},
                             "why": "новая позиция из live-поиска"})
    removed = [item for item in old.items if not any(n.kind == item.kind and n.item_id == item.item_id for n in new_items)]
    replaced.extend({"ref": f"offer:{item.offer_id}→—", "from": {"offer_id": item.offer_id}, "to": {},
                     "why": "предложение больше не входит в новый комплект"} for item in removed)
    return {
        "bundle": matching, "bundles": [matching], "selected_bundle_id": matching["id"],
        "kept": kept, "replaced": replaced,
        "price_delta_rub": int(matching["total_rub"]) - int(old.total_rub),
        "notes": ["Пересборка выполнена новым ограниченным поиском Туту MCP; стоимость означает сумму позиций комплекта, без трансфера."],
        "input_changes": changes,
    }
