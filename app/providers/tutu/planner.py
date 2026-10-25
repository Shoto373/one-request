"""Bounded live planner for Russian origin cities to the fixed Sochi destination."""
from __future__ import annotations

import asyncio
import hashlib
from contextvars import ContextVar
from contextlib import contextmanager
import os
import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.catalog.store import get_catalog
from app.state.models import Bundle, Claim, Check
from app.planning.query import MEAL_RANK, TripQuery, parse_meal, query_from_requirements, resolve_city
from app.providers.base import ProviderError
from app.providers.tutu.adapter import hotel_offer, transport_offer
from app.providers.tutu.client import TutuMCPClient, TutuMCPError
from app.product_scope import destination_ids, fixed_destination_id

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
    if not any(req.get(k) for k in ("destination", "dest", "destination_city", "to", "city")):
        req["destination"] = fixed_destination_id()
    cat = get_catalog()
    destination_value = next((req.get(key) for key in ("destination", "dest", "destination_city", "to", "city")
                              if req.get(key) not in (None, "")), None)
    destination = resolve_city(cat, destination_value)
    if destination not in destination_ids():
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
        if city.country_code != "RU":
            raise TutuOriginOutsideRussia(
                f"Город отправления «{city.name}» находится вне России. В прототипе можно указать город отправления в России."
            )
        return city.name
    return raw


def _explicitly_non_russian(place: Any) -> bool:
    if not isinstance(place, dict):
        return False
    code = str(place.get("country_code") or place.get("country_iso2") or "").upper()
    if code:
        return code != "RU"
    country = str(place.get("country") or "").strip().lower()
    return bool(country and country not in {"россия", "russia", "российская федерация", "russian federation"})


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


async def build_tutu_bundles(requirements: dict[str, Any], state: Any = None) -> list[dict[str, Any]]:
    """Search only the requested date window; all quantities come from provider results."""
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
    outbound_rows: list[dict[str, Any]] = []
    return_rows: list[dict[str, Any]] = []
    search_facts: dict[str, dict[str, Any]] = {}
    async with TutuMCPClient() as client:
        tasks: dict[str, Any] = {}
        if "flight" in query.kinds:
            avia_args = {
                "origin": origin_name, "destination": destination_name, "departure_date": query.depart_date.isoformat(),
                "return_date": query.return_date.isoformat(), "adults": adults, "children": children,
                "infants": infants, "page": 1, "page_size": 5, "sort": "price_asc", "view": "compact",
            }
            if query.limits_hard and query.max_stops == 0:
                avia_args["direct_only"] = True
            tasks["avia"] = _mcp_call(client, "search_avia", avia_args)
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
            if any(_explicitly_non_russian(meta.get(side)) for side in ("from", "to")):
                raise TutuOriginOutsideRussia("Туту определил один из городов маршрута за пределами России. Укажите российский город отправления.")
            search_facts[key] = {"args": result.args, "fetched_at": result.fetched_at,
                                 "resolved_from": meta.get("from"),
                                 "resolved_to": meta.get("to")}
            if key == "avia":
                flight_rows = _rows(result.data, "offers")
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
            outbound = [item for item in outbound if _transport_limit_issue(item, query) is None]
            inbound = [item for item in inbound if _transport_limit_issue(item, query) is None]
        transport_choices: list[tuple[str, list[Any], int]] = []
        for item in flights[:5]:
            duration = int(item.variant.get("duration_min") or 0)
            transport_choices.append(("avia", [item], duration))
        for out in outbound[:3]:
            for back in inbound[:3]:
                duration = int(out.variant.get("duration_min") or 0) + int(back.variant.get("duration_min") or 0)
                transport_choices.append(("rail", [out, back], duration))
        if not transport_choices:
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
        for checkin in arrival_dates[:4]:
            hotel_args: dict[str, Any] = {
                "city_name": destination_name, "check_in": checkin.isoformat(), "check_out": query.return_date.isoformat(),
                **hotel_party, "page": 1, "page_size": 5, "view": "compact",
            }
            if query.party.pets:
                # Listing filter is provider-confirmed but pet weight limits still require room/rule details.
                hotel_args["hotel_amenities"] = ["pets"]
            response = await _mcp_call(client, "search_hotels", hotel_args)
            search_facts[f"hotel:{checkin.isoformat()}"] = {
                "args": response.args, "fetched_at": response.fetched_at,
                "resolved_destination": (response.data.get("meta") or {}).get("resolved_geo"),
            }
            hotel_rows = _rows(response.data, "hotels")
            normalized = []
            for row in hotel_rows[:2]:
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
                    normalized.append(item)
            hotel_by_checkin[checkin.isoformat()] = normalized

        candidates: list[tuple[int, int, int, str, list[Any]]] = []
        for mode, transports, duration in transport_choices:
            checkin = _local_arrival(transports[0], city_tz)
            if checkin is None or checkin >= query.return_date:
                continue
            for hotel in hotel_by_checkin.get(checkin.isoformat(), []):
                items = [*transports, hotel]
                subtotal = sum(item.total_rub for item in items)
                refundable = sum(
                    item.variant.get("terms", {}).get("free_cancellation") is True if item.kind == "hotel"
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

        bundles: list[dict[str, Any]] = []
        fetched_at = datetime.now(timezone.utc).isoformat()
        for label, candidate in selected:
            subtotal, duration, _flex, _mode, items = candidate
            refs = [f"offer:{item.offer_id}" for item in items]
            checks = [Check(kind="availability", status="ok", refs=refs,
                            text="Позиции получены из live-поиска Tutu MCP; поисковая выдача не резервирует наличие.").model_dump(mode="json")]
            checks.append(Check(kind="transfer_fit", status="warn", refs=refs,
                                text="В доступных инструментах Туту нет поиска трансферов. Сумма комплекта не включает дорогу от станции/аэропорта до отеля и обратно.").model_dump(mode="json"))
            hotel = next(item for item in items if item.kind == "hotel")
            if hotel.variant.get("terms", {}).get("mandatory_charges") is None:
                checks.append(Check(kind="price_completeness", status="warn", refs=[f"offer:{hotel.offer_id}"],
                                    text="Туту не указал отдельно возможные обязательные доплаты отеля; сумма карточки может быть неполной.").model_dump(mode="json"))
            arrival = items[0].times.end.astimezone(ZoneInfo(city_tz))
            room_start = hotel.times.start.astimezone(ZoneInfo(city_tz)) if hotel.times else None
            hotel_ref = f"offer:{hotel.offer_id}"
            if room_start and arrival.date() == room_start.date() and arrival < room_start:
                checks.append(Check(kind="early_arrival", status="block", refs=[hotel_ref],
                                    text=f"Прибытие {arrival:%d.%m %H:%M}, подтверждённое время заезда {room_start:%H:%M}; ранний заезд в цене не подтверждён.",
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
                limit_issues = [issue for item in transports
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
            if source_errors:
                checks.append(Check(kind="availability", status="warn", refs=refs,
                                    text="Не все включённые в поиск виды транспорта ответили: " + "; ".join(source_errors) + ".").model_dump(mode="json"))
            bundle = Bundle(
                id=f"tutu-{hashlib.sha256('|'.join(i.offer_id for i in items).encode()).hexdigest()[:12]}",
                label=label, items=items, total_rub=subtotal,
                checks=checks,
                advantages=[Claim(text=f"Фактический маршрут и цена получены от Туту; сравнение основано на найденных вариантах.", refs=refs)],
                tradeoffs=[Claim(text="В стоимость входят только позиции Туту в карточке; трансфер не найден и отдельно не посчитан.", refs=refs)],
                risks=[Claim(text=check["text"], refs=check["refs"] or refs) for check in checks if check["status"] in {"warn", "block"}],
                price_note="Сумма live-предложений за указанный состав и даты; трансфер не включён. Это не окончательная стоимость поездки.",
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
