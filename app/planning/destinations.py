"""suggest_destinations (sprint 2, A2): only destinations where a feasible bundle is built.

For every candidate destination the real ``build_trip_bundles`` runs on a few start dates of
the requested month; destinations without a feasible bundle are rejected with a reason.
The rest are scored on weather in that month, travel time, the bundle total, visa and how
well they match soft/assumed preferences. Every factor keeps its value, weight and a Claim
with data refs, so the agent can explain the ranking and the user can correct it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any

from app.catalog.models import City, ClimateMonth, Party
from app.catalog.store import Catalog
from app.planning.alternatives import minimal_changes
from app.planning.build import build_trip_bundles
from app.planning.models import Bundle, Claim, bundle_to_wire
from app.planning.models import rub as _rub
from app.planning.query import (
    TransportKind,
    TripQuery,
    _pick,
    merged_requirements,
    parse_dates,
    parse_int,
    parse_transport,
    parse_travel_limits,
    party_and_budget,
    requirement_text,
    resolve_city,
)
from app.tools.climate import climate_ref, get_climate

DEFAULT_NIGHTS = 7
START_DAYS = (10, 17, 3, 24)  # tried start days in the requested month
TOP_N = 3

MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "мая": 5, "май": 5, "июн": 6, "июл": 7,
    "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9,
    "oct": 10, "nov": 11, "dec": 12,
}
MONTH_NAME = ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе", "сентябре",
              "октябре", "ноябре", "декабре"]
VISA_SCORE = {"domestic": 1.0, "visa_free": 0.9, "visa_on_arrival": 0.7, "evisa": 0.5, "visa_required": 0.2}
VISA_TEXT = {"domestic": "по России, виза не нужна", "visa_free": "безвиз", "visa_on_arrival": "виза по прибытии",
             "evisa": "электронная виза", "visa_required": "нужна виза"}
# keyword in requirements -> catalog city tags it asks for
PREF_TAGS = {
    r"мор|пляж|sea|beach|купа": {"sea", "beach"},
    r"культур|музе|истори|экскурс|culture": {"culture"},
    r"гастро|еда|кухн|food": {"food"},
    r"шопинг|shopping": {"shopping"},
    r"гор[ыа]|mountain": {"mountains"},
    r"дайв|снорк|diving": {"diving"},
    r"вс[её] включено|all.?inclusive": {"all_inclusive"},
    r"вино|wine": {"wine"},
}
WEIGHTS = {"weather": 0.30, "price": 0.25, "travel": 0.20, "visa": 0.10, "match": 0.15}


@dataclass
class Preferences:
    sea: bool = False
    sea_hard: bool = False
    warm: bool = False
    cheap: bool = False
    short_travel: bool = False
    tags: set[str] = field(default_factory=set)


@dataclass
class DestinationQuery:
    origin_city: str
    month: int
    year: int
    nights: int
    party: Party
    exact_dates: tuple[date, date] | None = None
    budget_rub: int | None = None
    budget_hard: bool = False
    kinds: tuple[TransportKind, ...] = ("flight", "train")
    max_stops: int | None = None
    max_travel_min: int | None = None
    limits_hard: bool = False
    prefs: Preferences = field(default_factory=Preferences)
    candidates: list[str] | None = None
    assumptions: list[str] = field(default_factory=list)


@dataclass
class DestinationResult:
    options: list[dict[str, Any]]
    rejected: list[dict[str, str]]
    assumptions: list[str]
    changes: list[dict[str, Any]] = field(default_factory=list)  # filled when no option is feasible
    problems: list[str] = field(default_factory=list)  # unresolved required fields

    def to_wire(self) -> dict[str, Any]:
        return {"options": self.options, "rejected": self.rejected, "assumptions": self.assumptions,
                "changes": self.changes, "problems": self.problems}


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _hours(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    return f"{h} ч {m:02d} мин" if h else f"{m} мин"


def _party_text(p: Party) -> str:
    text = f"{p.adults} взр."
    if p.children:
        text += f" и {p.children} дет."
    return text


# --------------------------------------------------------------------------- parsing


def parse_month(value: Any) -> int | None:
    if isinstance(value, int) and 1 <= value <= 12:
        return value
    if isinstance(value, dict):
        d = parse_dates(value)
        return d[0].month if d else None
    if isinstance(value, str):
        m = re.search(r"\d{4}-(\d{2})", value)
        if m:
            return int(m.group(1))
        text = value.lower()
        return next((n for stem, n in MONTHS.items() if stem in text), None)
    return None


def parse_nights(value: Any, text: str) -> int | None:
    n = parse_int(value) if value is not None and not isinstance(value, str) else None
    if isinstance(value, str):
        if re.search(r"недел", value):
            n = 7 * (parse_int(value) or 1)
        elif re.search(r"выходн", value):
            n = 2
        else:
            days = parse_int(value)
            n = days - 1 if days and re.search(r"дн|day", value) else days
    if n is None and re.search(r"на недел", text):
        n = 7
    if n is None and re.search(r"на выходн", text):
        n = 2
    return n if n and n > 0 else None


def _year_in_window(cat: Catalog, month: int) -> int | None:
    window = cat.meta.window if cat.meta else None
    if window is None:
        return date.today().year
    for year in (window.start.year, window.start.year + 1):
        first = date(year, month, 1)
        last = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
        if first < window.end and last > window.start:
            return year
    return None


def parse_preferences(req: dict[str, Any], kinds_by_key: dict[str, str], party: Party) -> Preferences:
    text = requirement_text(req)
    prefs = Preferences()
    prefs.sea = bool(re.search(r"мор|пляж|sea|beach|купа", text))
    sea_keys = [k for k, v in req.items() if re.search(r"мор|пляж|sea|beach", f"{k} {v}".lower())]
    prefs.sea_hard = any(kinds_by_key.get(k) == "hard" for k in sea_keys)
    prefs.warm = bool(re.search(r"тепл|жар|солн|warm|sun", text)) or prefs.sea
    prefs.cheap = bool(re.search(r"недорог|дешев|бюджетн|эконом|cheap|low_cost", text))
    for rx, tags in PREF_TAGS.items():
        if re.search(rx, text):
            prefs.tags |= tags
    if party.children:
        prefs.tags.add("family")
    return prefs


def destination_query_from_requirements(
    cat: Catalog, requirements: dict[str, Any], state: Any = None
) -> tuple[DestinationQuery | None, list[str]]:
    req, kinds_by_key = merged_requirements(requirements, state)
    text = requirement_text(req)
    problems, assumptions = [], []

    origin = resolve_city(cat, _pick(req, "origin", "origin_city", "from", "departure_city")[1])
    if origin is None or "origin" not in cat.cities[origin].roles:
        problems.append("origin: нужен город отправления (Москва, Санкт-Петербург или Екатеринбург).")

    exact = parse_dates(_pick(req, "dates", "date_range", "trip_dates")[1])
    month = exact[0].month if exact else parse_month(_pick(req, "travel_month", "month", "when", "period", "dates")[1])
    if month is None:
        month = parse_month(text)
    year = exact[0].year if exact else (_year_in_window(cat, month) if month else None)
    stated_year = parse_int(req.get("travel_year"))
    if not exact and month and stated_year and _year_in_window(cat, month) == stated_year:
        year = stated_year
    if month is None:
        problems.append("dates: нужен месяц или даты поездки.")
    elif year is None:
        problems.append("dates: этот месяц вне окна каталога (конец октября 2026 – середина января 2027).")

    nights = (exact[1] - exact[0]).days if exact else parse_nights(_pick(req, "duration_nights", "nights", "duration",
                                                                        "length", "trip_length")[1], text)
    if nights is None:
        nights = DEFAULT_NIGHTS
        assumptions.append(f"{DEFAULT_NIGHTS} ночей — длительность не указана")
    if problems:
        return None, problems

    party, budget, budget_hard = party_and_budget(req, kinds_by_key)
    if _pick(req, "adults", "adults_count", "travelers", "people")[0] is None:
        party = party.model_copy(update={"adults": 2})
        assumptions.append("2 взрослых — состав не указан")
    if party.children and any(a == 7 for a in party.children_ages) and not _pick(req, "children_ages")[0]:
        assumptions.append("возраст ребёнка не указан — считаем 7 лет")
    max_stops, max_travel_min, limits_hard = parse_travel_limits(req, kinds_by_key)
    prefs = parse_preferences(req, kinds_by_key, party)
    prefs.short_travel = max_travel_min is not None
    if max_travel_min is not None and not _pick(req, "max_duration_min", "max_flight_hours", "max_travel_hours")[0]:
        assumptions.append(f"«без долгих перелётов» = до {max_travel_min // 60} ч в одну сторону")
    explicit = resolve_city(cat, _pick(req, "destination", "dest", "destination_city", "to")[1])
    return DestinationQuery(
        origin_city=origin, month=month, year=year, nights=nights, party=party, exact_dates=exact,
        budget_rub=budget, budget_hard=budget_hard,
        kinds=parse_transport(req, kinds_by_key),
        max_stops=max_stops, max_travel_min=max_travel_min, limits_hard=limits_hard, prefs=prefs,
        candidates=[explicit] if explicit else None, assumptions=assumptions,
    ), []


# --------------------------------------------------------------------------- scoring


def _trip_queries(cat: Catalog, dq: DestinationQuery, dest: str) -> list[TripQuery]:
    base = TripQuery(origin_city=dq.origin_city, dest_city=dest, depart_date=date(dq.year, dq.month, 1),
                     return_date=date(dq.year, dq.month, 2), party=dq.party, budget_rub=dq.budget_rub,
                     budget_hard=dq.budget_hard, kinds=dq.kinds, max_stops=dq.max_stops,
                     max_travel_min=dq.max_travel_min, limits_hard=dq.limits_hard)
    if dq.exact_dates:
        return [replace(base, depart_date=dq.exact_dates[0], return_date=dq.exact_dates[1])]
    window = cat.meta.window if cat.meta else None
    out = []
    for day in START_DAYS:
        start = date(dq.year, dq.month, day)
        end = start + timedelta(days=dq.nights)
        if window and not (window.start <= start and end < window.end):
            continue
        out.append(replace(base, depart_date=start, return_date=end))
    return out


def _weather(city: City, c: ClimateMonth | None, prefs: Preferences) -> tuple[float, Claim, list[Claim]]:
    risks: list[Claim] = []
    if c is None:
        return 0.5, Claim(text="Нет данных о погоде", refs=[]), risks
    m = c.month
    air = Claim(text=f"Воздух днём {c.air_temp_day:.0f}°C, ночью {c.air_temp_night:.0f}°C в {MONTH_NAME[m - 1]}",
                refs=[climate_ref(city.id, m, "air_temp_day"), climate_ref(city.id, m, "air_temp_night")])
    if prefs.sea:
        sea = _clamp((c.sea_temp - 18) / 8) if c.sea_temp is not None and "beach" in city.tags else 0.0
        value = 0.6 * sea + 0.4 * _clamp((c.air_temp_day - 14) / 12)
        if c.sea_temp is not None and "beach" in city.tags:
            claim = Claim(text=f"Море {c.sea_temp:.0f}°C, воздух днём {c.air_temp_day:.0f}°C в {MONTH_NAME[m - 1]}",
                          refs=[climate_ref(city.id, m, "sea_temp"), climate_ref(city.id, m, "air_temp_day")])
            if c.sea_temp < 21:
                risks.append(Claim(text=f"Море прохладное для купания: {c.sea_temp:.0f}°C",
                                   refs=[climate_ref(city.id, m, "sea_temp")]))
        else:
            claim = Claim(text=f"Нет пляжного моря; {air.text[0].lower()}{air.text[1:]}", refs=air.refs)
            risks.append(Claim(text="Не пляжное направление", refs=[f"city:{city.id}:tags"]))
    elif prefs.warm:
        value, claim = _clamp((c.air_temp_day - 12) / 12), air
    else:
        value, claim = _clamp(1 - abs(c.air_temp_day - 22) / 15), air
    value -= min(0.15, c.rainy_days / 60)
    if c.rainy_days >= 8:
        risks.append(Claim(text=f"Часто дожди: {c.rainy_days:.0f} дней с осадками в месяц",
                           refs=[climate_ref(city.id, m, "rainy_days")]))
    return _clamp(value), claim, risks


def _score(cat: Catalog, dq: DestinationQuery, city: City, bundle: Bundle, cheapest: int) -> dict[str, Any]:
    prefs = dq.prefs
    weights = dict(WEIGHTS)
    if prefs.cheap or dq.budget_rub:
        weights["price"] += 0.10
    if prefs.short_travel:
        weights["travel"] += 0.10
    if prefs.sea or prefs.warm:
        weights["weather"] += 0.15
    total_w = sum(weights.values())
    weights = {k: v / total_w for k, v in weights.items()}

    climate = get_climate(cat, city.id, dq.month)
    weather, weather_claim, risks = _weather(city, climate, prefs)

    out_offer = bundle.items[0]
    minutes = int(out_offer.variant.get("duration_min", 0))
    travel = _clamp(1 - (minutes / 60 - 1.5) / 8)
    if dq.max_travel_min and minutes > dq.max_travel_min:
        travel *= 0.5
        risks.append(Claim(text=f"Дорога дольше желаемого: {_hours(minutes)}", refs=[f"offer:{out_offer.offer_id}"]))
    kind = "перелёт" if out_offer.kind == "flight" else "поезд"
    stops = out_offer.variant.get("stops", 0)
    travel_claim = Claim(text=f"{kind.capitalize()} {_hours(minutes)}" + (", с пересадкой" if stops else ", без пересадок"
                         if out_offer.kind == "flight" else ""), refs=[f"offer:{out_offer.offer_id}"])

    total = bundle.total_rub
    if dq.budget_rub:
        price = 1.0 if total <= dq.budget_rub else _clamp(1 - (total - dq.budget_rub) / dq.budget_rub)
    else:
        price = cheapest / total if total else 0.0
    nights = bundle.items[2].units if len(bundle.items) > 2 and bundle.items[2].kind == "hotel" else dq.nights
    price_claim = Claim(text=f"≈ {_rub(total)} на {_party_text(dq.party)} за {nights} ноч.: дорога, трансферы, отель",
                        refs=[f"bundle:{city.id}"] + [f"offer:{o.offer_id}" for o in bundle.items])

    visa = VISA_SCORE.get(city.visa.regime, 0.5)
    visa_claim = Claim(text=f"{city.country}: {VISA_TEXT.get(city.visa.regime, city.visa.regime)}",
                       refs=[f"city:{city.id}:visa"])

    wanted = prefs.tags
    matched = wanted & set(city.tags)
    match = len(matched) / len(wanted) if wanted else 0.5
    hotel = next((o for o in bundle.items if o.kind == "hotel"), None)
    if dq.party.children and hotel is not None:
        h = cat.hotels[hotel.item_id]
        if h.kids.kids_club or h.kids.kids_pool:
            match = _clamp(match + 0.2)
            matched.add("детский клуб/бассейн в отеле")
    match_claim = Claim(text="Подходит: " + ", ".join(sorted(matched)) if matched else "Пожелания по типу отдыха не указаны",
                        refs=[f"city:{city.id}:tags"])

    factors = [
        ("weather", weather, weather_claim), ("price", price, price_claim), ("travel", travel, travel_claim),
        ("visa", visa, visa_claim), ("match", match, match_claim),
    ]
    breakdown = [
        {"factor": name, "weight": round(weights[name], 3), "value": round(value, 3),
         "contribution": round(weights[name] * value, 3), "text": claim.text, "refs": claim.refs}
        for name, value, claim in factors
    ]
    risks += [Claim(text=r.text, refs=r.refs) for r in bundle.risks]
    if city.visa.regime != "domestic":
        risks.append(Claim(text=f"Документы: {', '.join(city.visa.documents)}", refs=[f"city:{city.id}:visa"]))
    return {
        "score": round(sum(b["contribution"] for b in breakdown), 3),
        "score_breakdown": breakdown,
        "why": [weather_claim, price_claim, travel_claim, visa_claim] + ([match_claim] if matched else []),
        "risks": risks,
        "travel": {"kind": out_offer.kind, "duration_min": minutes, "stops": stops, "title": out_offer.title},
        "climate": climate.model_dump(mode="json") if climate else None,
        "nights": nights,
    }


def _fits_core(prefs: Preferences, city: City, climate: dict[str, Any] | None) -> bool:
    if climate is None:
        return not (prefs.sea or prefs.warm)
    if prefs.sea:
        return "beach" in city.tags and (climate.get("sea_temp") or 0) >= 21
    if prefs.warm:
        return climate["air_temp_day"] >= 20
    return True


def suggest_destinations_for(cat: Catalog, dq: DestinationQuery, limit: int = TOP_N) -> DestinationResult:
    candidates = dq.candidates or sorted(c.id for c in cat.cities.values() if "destination" in c.roles)
    built: list[tuple[City, Bundle, TripQuery]] = []
    rejected: list[dict[str, str]] = []
    for dest in candidates:
        city = cat.cities[dest]
        climate = get_climate(cat, dest, dq.month)
        if dq.prefs.sea_hard and ("beach" not in city.tags or not climate or (climate.sea_temp or 0) < 20):
            rejected.append({"id": dest, "name": city.name, "reason": "нет тёплого пляжного моря в этот месяц"})
            continue
        best: tuple[Bundle, TripQuery] | None = None
        notes: list[str] = []
        for q in _trip_queries(cat, dq, dest):
            res = build_trip_bundles(cat, q)
            if res.bundles and (best is None or res.bundles[0].total_rub < best[0].total_rub):
                best = (res.bundles[0], q)
            notes = notes or res.notes
        if best is None:
            rejected.append({"id": dest, "name": city.name,
                             "reason": "; ".join(notes) or "нет выполнимого комплекта на эти даты"})
            continue
        if dq.budget_rub and dq.budget_hard and best[0].total_rub > dq.budget_rub:
            rejected.append({"id": dest, "name": city.name, "reason": f"дороже бюджета: от {_rub(best[0].total_rub)}"})
            continue
        built.append((city, best[0], best[1]))

    cheapest = min((b.total_rub for _, b, _ in built), default=0)
    options = []
    for city, bundle, q in built:
        scored = _score(cat, dq, city, bundle, cheapest)
        bundle_wire = bundle_to_wire(bundle)
        bundle_wire["id"] = f"b_{city.id.lower()}"
        options.append({
            "id": city.id, "name": city.name, "country": city.country, "feasible": True,
            "est_total_rub": bundle.total_rub,
            "dates": {"start": str(q.depart_date), "end": str(q.return_date)},
            "visa": {"regime": city.visa.regime, "note": city.visa.note, "documents": city.visa.documents},
            **scored,
            "why": [c.model_dump(mode="json") for c in scored["why"]],
            "risks": [c.model_dump(mode="json") for c in scored["risks"]],
            "bundle": bundle_wire,
            "assumptions": dq.assumptions,
        })
    # Destinations that meet the core wish (warm beach sea / warm weather) always rank first.
    options.sort(key=lambda o: (not _fits_core(dq.prefs, cat.cities[o["id"]], o["climate"]),
                                -o["score"], o["est_total_rub"], o["id"]))
    for i, o in enumerate(options, start=1):
        o["rank"] = i
    result = DestinationResult(options=options[:limit], rejected=rejected, assumptions=dq.assumptions)
    if not options:
        result.changes = _changes_when_empty(cat, dq, candidates)
    return result


def closest_trip_query(cat: Catalog, dq: DestinationQuery, candidates: list[str] | None = None) -> TripQuery | None:
    """Trip of the candidate that is cheapest when the budget is ignored: the base for minimal changes."""
    candidates = candidates or dq.candidates or sorted(c.id for c in cat.cities.values() if "destination" in c.roles)
    best: tuple[int, TripQuery] | None = None
    for dest in candidates:
        for q in _trip_queries(cat, dq, dest):
            res = build_trip_bundles(cat, replace(q, budget_rub=None, budget_hard=False))
            if res.bundles and (best is None or res.bundles[0].total_rub < best[0]):
                best = (res.bundles[0].total_rub, q)
    if best is not None:
        return best[1]
    queries = _trip_queries(cat, dq, candidates[0]) if candidates else []
    return queries[0] if queries else None


def _changes_when_empty(cat: Catalog, dq: DestinationQuery, candidates: list[str]) -> list[dict[str, Any]]:
    q = closest_trip_query(cat, dq, candidates)
    return [c.to_wire() for c in minimal_changes(cat, q)] if q else []


# --------------------------------------------------------------------------- orchestrator contract


def suggest_destinations(requirements: dict[str, Any], state: Any = None, limit: int = TOP_N) -> list[dict[str, Any]]:
    """Top feasible destinations for TripState.destinations ([] if none or the request is incomplete)."""
    return suggest_destinations_report(requirements, state, limit)["options"]


def suggest_destinations_report(requirements: dict[str, Any], state: Any = None, limit: int = TOP_N) -> dict[str, Any]:
    """Options + rejected with reasons + assumptions + minimal changes when nothing fits."""
    from app.catalog.store import get_catalog

    cat = get_catalog()
    dq, problems = destination_query_from_requirements(cat, requirements, state)
    if dq is None:
        return DestinationResult(options=[], rejected=[], assumptions=[], problems=problems).to_wire()
    return suggest_destinations_for(cat, dq, limit).to_wire()
