"""TripQuery and its construction from TripState requirements (track B -> track A boundary).

The orchestrator passes ``{key: value}`` extracted by the LLM: city names as text
("Москва", "из Казани", "MOW"), dates as ``{"start", "end"}`` or ISO strings, people as
numbers or words. Parsing here is tolerant but never invents a city or a date:
unresolved fields make ``query_from_requirements`` return ``None`` with a reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

from app.catalog.models import Party, Pet
from app.catalog.store import Catalog

TransportKind = Literal["flight", "train"]


@dataclass
class TripQuery:
    origin_city: str
    dest_city: str
    depart_date: date
    return_date: date
    party: Party
    budget_rub: int | None = None
    budget_hard: bool = False
    kinds: tuple[TransportKind, ...] = ("flight", "train")
    max_stops: int | None = None  # "без пересадок" -> 0
    max_travel_min: int | None = None  # "без долгих перелётов" -> one-way limit
    limits_hard: bool = False  # soft limits fall back to any transport (with a note)
    dates_hard: bool = False  # alternatives never move hard dates without consent
    date_flex_days: int = 0  # user-stated flexibility: shifts within it need no consent
    min_stars: int | None = None  # "не ниже 4 звёзд", "5 звёзд"
    meal: str | None = None  # minimal meal plan: RO < BB < HB < FB < AI
    lodging_hard: bool = False  # hard stars/meal filter hotels; soft ones only rank the comfortable bundle
    dest_hard: bool = False


# Forms an LLM or a user may leave unnormalized; stems below cover most case endings.
CITY_ALIASES = {
    "мск": "MOW", "moscow": "MOW",
    "спб": "LED", "питер": "LED", "петербург": "LED", "санкт петербург": "LED", "ленинград": "LED",
    "st petersburg": "LED", "saint petersburg": "LED",
    "екб": "SVX", "екат": "SVX", "свердловск": "SVX",
    "сочи": "AER", "адлер": "AER", "sochi": "AER", "красная поляна": "AER",
    "шарм": "SSH", "шарм эль шейх": "SSH", "шарм эш шейх": "SSH", "sharm": "SSH",
    "альпы": "INN", "альпах": "INN", "alps": "INN", "инсбрук": "INN", "innsbruck": "INN",
    "женева": "GVA", "geneva": "GVA", "куршевель": "GVA", "шамони": "GVA",
    "банско": "SOF", "софия": "SOF", "bansko": "SOF",
    "пхукет": "HKT", "phuket": "HKT", "тайланд": "HKT", "таиланд": "HKT",
    "бали": "DPS", "bali": "DPS", "денпасар": "DPS",
    "париж": "CDG", "paris": "CDG",
    "рим": "FCO", "rome": "FCO",
    "токио": "NRT", "tokyo": "NRT",
    "мальдивы": "MLE", "мале": "MLE", "maldives": "MLE",
    "барселона": "BCN", "barcelona": "BCN",
}
WORD_NUMBERS = {
    "один": 1, "одна": 1, "одного": 1, "два": 2, "две": 2, "двое": 2, "двоих": 2, "трое": 3, "три": 3, "троих": 3,
    "четверо": 4, "четыре": 4, "пятеро": 5, "пять": 5, "шестеро": 6, "шесть": 6,
}
DEFAULT_CHILD_AGE = 7  # age unknown: counted as a paying child (assumption, visible in docs)
DEFAULT_PET_KG = 10.0

_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
_INT = re.compile(r"\d+")


def _norm(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^a-zа-я0-9]+", " ", text).strip()
    return re.sub(r"^(из|в|во|до|на|город|г)\s+", "", text)


def resolve_city(cat: Catalog, value: Any) -> str | None:
    """City id for free text ("Москва", "из Казани", "MOW", "SVO"); None if unknown."""
    if isinstance(value, dict):
        value = value.get("city") or value.get("name") or value.get("id") or value.get("value")
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    from app.planning.landmarks import resolve_landmark
    lm = resolve_landmark(raw)
    if lm:
        cid = lm["city_id"]
        if cid in cat.cities:
            return cid
        from app.catalog.world_resolver import resolve_world_city
        return resolve_world_city(cat, cid) or cid

    if raw.upper() in cat.cities:
        return raw.upper()
    if raw.upper() in cat.hubs:
        return cat.hubs[raw.upper()].city_id
    text = _norm(raw)
    if text in CITY_ALIASES:
        cid = CITY_ALIASES[text]
        if cid in cat.cities:
            return cid
        from app.catalog.world_resolver import resolve_world_city
        return resolve_world_city(cat, cid) or cid
    for city in cat.cities.values():
        for name in (_norm(city.name), _norm(city.name_en)):
            # Case endings: Москва/Москвы (-1), Анталья/Анталию (-2).
            stems = [name[:-1]] if len(name) >= 5 else []
            if len(name) >= 7:
                stems.append(name[:-2])
            if text == name or any(text.startswith(s) for s in stems):
                return city.id
    for alias, cid in CITY_ALIASES.items():
        if text.startswith(alias):
            if cid in cat.cities:
                return cid
            from app.catalog.world_resolver import resolve_world_city
            return resolve_world_city(cat, cid) or cid

    from app.catalog.world_resolver import resolve_world_city
    return resolve_world_city(cat, raw)



# Informal names and abbreviations of cities outside the catalog: the display name must not depend on
# the LLM (GigaChat often keeps «LA» or «нск» as written).
CITY_DISPLAY_ALIASES = {
    "la": "Лос-Анджелес", "l a": "Лос-Анджелес", "лос анджелес": "Лос-Анджелес", "los angeles": "Лос-Анджелес",
    "nyc": "Нью-Йорк", "ny": "Нью-Йорк", "нью йорк": "Нью-Йорк", "new york": "Нью-Йорк", "нй": "Нью-Йорк",
    "sf": "Сан-Франциско", "сан франциско": "Сан-Франциско", "san francisco": "Сан-Франциско",
    "vegas": "Лас-Вегас", "лас вегас": "Лас-Вегас", "las vegas": "Лас-Вегас",
    "london": "Лондон", "berlin": "Берлин", "vienna": "Вена", "madrid": "Мадрид", "lisbon": "Лиссабон",
    "нск": "Новосибирск", "новосиб": "Новосибирск", "нн": "Нижний Новгород", "нижний": "Нижний Новгород",
    "влад": "Владивосток", "краснодар": "Краснодар", "крд": "Краснодар", "ростов": "Ростов-на-Дону",
    "ростов на дону": "Ростов-на-Дону", "челяба": "Челябинск", "кёниг": "Калининград", "кениг": "Калининград",
    "кенигсберг": "Калининград", "минводы": "Минеральные Воды", "мин воды": "Минеральные Воды",
    "дубаи": "Дубай", "стамбуле": "Стамбул",
}


def _display_case(text: str) -> str:
    """«рязань» -> «Рязань», «нижний новгород» -> «Нижний Новгород», «ростов-на-дону» stays readable."""
    small = {"на", "де", "ла", "да", "дель", "on", "de", "la"}
    words = []
    for i, word in enumerate(text.split()):
        parts = [p if (j and p in small) else p[:1].upper() + p[1:] for j, p in enumerate(word.split("-"))]
        words.append("-".join(parts) if i == 0 or word not in small else word)
    return " ".join(words)


def display_city_name(cat: Catalog, value: Any) -> str | None:
    """Full, properly spelled city name for showing requirements: catalog name, known alias, or the
    user's text with capital letters. None only for empty input."""
    if not isinstance(value, str) or not value.strip():
        return None
    canonical = canonical_city_name(cat, value)
    if canonical:
        return canonical
    key = _norm(value)
    if key in CITY_DISPLAY_ALIASES:
        return CITY_DISPLAY_ALIASES[key]
    raw = value.strip()
    return _display_case(raw) if raw == raw.lower() else raw


def canonical_city_name(cat: Catalog, value: Any) -> str | None:
    """Full city name for informal input («Питер» -> «Санкт-Петербург», «мск», «SVO», «Анталию»).

    Uses only the catalog and fixed aliases: unlike ``resolve_city`` it never registers a new city
    and never geocodes, so it is safe to call on every extracted requirement.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    from app.catalog.world_resolver import init_world_catalog

    init_world_catalog(cat)
    if raw.upper() in cat.cities:
        return cat.cities[raw.upper()].name
    if raw.upper() in cat.hubs:
        return cat.cities[cat.hubs[raw.upper()].city_id].name
    text = _norm(raw)
    cid = CITY_ALIASES.get(text)
    if cid is None:
        for city in cat.cities.values():
            if city.id.startswith("W_"):
                continue
            for name in (_norm(city.name), _norm(city.name_en)):
                stems = [name[:-1]] if len(name) >= 5 else []
                if len(name) >= 7:
                    stems.append(name[:-2])
                if text == name or any(text.startswith(s) for s in stems):
                    cid = city.id
                    break
            if cid:
                break
    city = cat.cities.get(cid) if cid else None
    return city.name if city else None


def _to_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        m = _ISO.search(value)
        if m:
            return date.fromisoformat(m.group(0))
        m_dmy = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", value)
        if m_dmy:
            d, m_num, y = m_dmy.groups()
            return date(int(y), int(m_num), int(d))
    return None


def parse_dates(value: Any) -> tuple[date, date] | None:
    if isinstance(value, dict):
        for a, b in (("start", "end"), ("from", "to"), ("depart", "return"), ("check_in", "check_out")):
            if a in value and b in value:
                start, end = _to_date(value[a]), _to_date(value[b])
                return (start, end) if start and end else None
        return None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        start, end = _to_date(value[0]), _to_date(value[1])
        return (start, end) if start and end else None
    if isinstance(value, str):
        found = _ISO.findall(value)
        if len(found) >= 2:
            return date.fromisoformat(found[0]), date.fromisoformat(found[1])
        found_dmy = re.findall(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", value)
        if len(found_dmy) >= 2:
            d1, m1, y1 = found_dmy[0]
            d2, m2, y2 = found_dmy[1]
            return date(int(y1), int(m1), int(d1)), date(int(y2), int(m2), int(d2))
    return None


def parse_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        text = _norm(value)
        for word, n in WORD_NUMBERS.items():
            if re.search(rf"\b{word}\b", text):
                return n
        m = _INT.search(text)
        return int(m.group(0)) if m else None
    return None


def parse_children(value: Any) -> list[int]:
    """Ages from [5, 9], [{"age": 5}], "ребёнок 5 лет"; a bare count -> default ages."""
    if value is None or value is False:
        return []
    if isinstance(value, (list, tuple)):
        ages = []
        for x in value:
            age = parse_int(x.get("age") if isinstance(x, dict) else x)
            ages.append(age if age is not None and 0 <= age <= 17 else DEFAULT_CHILD_AGE)
        return ages
    if isinstance(value, str) and re.search(r"год|лет|мес", value):
        ages = [int(x) for x in _INT.findall(value) if int(x) <= 17]
        if "мес" in value:
            ages = [0] * len(ages)
        return ages or [DEFAULT_CHILD_AGE]
    n = parse_int(value)
    return [DEFAULT_CHILD_AGE] * n if n and n > 0 else []


def parse_pets(value: Any) -> list[Pet]:
    if value in (None, False, 0, "", [], "нет", "no", "false"):
        return []
    if isinstance(value, (list, tuple)):
        return [p for x in value for p in parse_pets(x)]
    if isinstance(value, dict):
        kind = "cat" if re.search(r"cat|кош|кот", str(value.get("kind", ""))) else "dog"
        weight = parse_int(value.get("weight_kg")) or DEFAULT_PET_KG
        return [Pet(kind=kind, weight_kg=weight)]
    text = str(value).lower()
    kind = "cat" if re.search(r"cat|кош|кот", text) else "dog"
    m = re.search(r"(\d+)\s*кг", text)
    return [Pet(kind=kind, weight_kg=float(m.group(1)) if m else DEFAULT_PET_KG)]


def parse_budget(value: Any) -> int | None:
    if isinstance(value, dict):
        value = value.get("value") or value.get("total_rub") or value.get("amount")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, str):
        text = value.lower().replace(" ", "").replace(" ", "")
        m = re.search(r"\d+(?:[.,]\d+)?", text)
        if not m:
            return None
        amount = float(m.group(0).replace(",", "."))
        if re.search(r"тыс|k|к\b", text) or amount < 1000:
            amount *= 1000
        return int(amount)
    return None


def parse_kinds(value: Any) -> tuple[TransportKind, ...] | None:
    text = str(value).lower()
    if re.search(r"поезд|жд|ж/д|train|rail", text):
        return ("train",)
    if re.search(r"самол|перел|авиа|flight|plane", text):
        return ("flight",)
    return None


def parse_transport(req: dict[str, Any], kinds_by_key: dict[str, str]) -> tuple[TransportKind, ...]:
    """Soft/assumed transport preferences rank results; only a hard transport requirement filters."""
    transport_key, transport_value = _pick(req, "transport", "transport_mode", "transport_type", "by")
    if transport_key and kinds_by_key.get(transport_key) == "hard":
        return parse_kinds(transport_value) or ("flight", "train")
    return ("flight", "train")


MEAL_RANK = {"RO": 0, "BB": 1, "HB": 2, "FB": 3, "AI": 4}


def parse_meal(value: Any) -> str | None:
    text = str(value or "").lower().replace("ё", "е")
    if re.search(r"all|вс[е] включ|ультра|\bai\b|uai", text):
        return "AI"
    if re.search(r"полн\w* пансион|full|\bfb\b", text):
        return "FB"
    if re.search(r"полупансион|half|\bhb\b|ужин|dinner", text):
        return "HB"
    if re.search(r"завтрак|breakfast|\bbb\b", text):
        return "BB"
    return None


def parse_lodging(req: dict[str, Any], kinds_by_key: dict[str, str]) -> tuple[int | None, str | None, bool]:
    """(min_stars, meal code, hard) from lodging_stars / meal_preference."""
    stars_key, stars_value = _pick(req, "lodging_stars", "stars", "hotel_stars")
    stars = parse_int(stars_value) if stars_value is not None else None
    stars = stars if stars and 1 <= stars <= 5 else None
    meal_key, meal_value = _pick(req, "meal_preference", "meal", "meals")
    meal = parse_meal(meal_value)
    hard = any(k and kinds_by_key.get(k) == "hard" for k, v in ((stars_key, stars), (meal_key, meal)) if v)
    return stars, meal, hard


def requirement_text(req: dict[str, Any]) -> str:
    """All keys and values as one lowercase string, for keyword preferences."""
    return " ".join(f"{k} {v}" for k, v in req.items()).lower().replace("ё", "е")


LONG_TRAVEL_DEFAULT_MIN = 300  # "без долгих перелётов" without a number: up to 5 hours one way


def parse_travel_limits(req: dict[str, Any], kinds_by_key: dict[str, str]) -> tuple[int | None, int | None, bool]:
    """(max_stops, max_travel_min, hard) from explicit numbers or phrases."""
    text = requirement_text(req)
    key, value = _pick(req, "max_duration_min")
    max_min = parse_int(value) if value is not None else None
    if max_min is None:
        key, value = _pick(req, "max_flight_hours", "max_travel_hours", "flight_hours", "max_flight_duration",
                           "travel_time", "flight_duration")
        hours = parse_int(value) if value is not None else None
        max_min = hours * 60 if hours else None
    hard = bool(key and kinds_by_key.get(key) == "hard")
    if max_min is None and str(req.get("travel_time_preference", "")).lower() in ("short", "короткий", "короткие"):
        max_min = LONG_TRAVEL_DEFAULT_MIN
    if max_min is None and re.search(r"без долг\w* (перел|дорог|поезд)|короткий перел|недолг\w* перел|short flight", text):
        max_min = LONG_TRAVEL_DEFAULT_MIN
    stops_key, stops_value = _pick(req, "max_stops", "stops", "direct", "direct_flight", "no_transfers")
    max_stops = None
    if stops_key in ("max_stops", "stops"):
        max_stops = parse_int(stops_value)
    elif stops_key or re.search(r"без пересад|прям\w* рейс|direct", text):
        max_stops = 0
    hard = hard or bool(stops_key and kinds_by_key.get(stops_key) == "hard")
    return max_stops, max_min, hard


def _items(state: Any) -> list[tuple[str, Any, str]]:
    """(key, value, kind) from a TripState model or its JSON dict."""
    reqs = getattr(state, "requirements", None)
    if reqs is None and isinstance(state, dict):
        reqs = state.get("requirements")
    out = []
    for r in reqs or []:
        get = r.get if isinstance(r, dict) else lambda k, r=r: getattr(r, k, None)
        out.append((get("key"), get("value"), get("kind") or "unknown"))
    return out


def _pick(req: dict[str, Any], *keys: str) -> tuple[str | None, Any]:
    for k in keys:
        if req.get(k) not in (None, ""):
            return k, req[k]
    return None, None


def merged_requirements(requirements: dict[str, Any] | None, state: Any = None) -> tuple[dict[str, Any], dict[str, str]]:
    """Explicit ``requirements`` win over TripState ones; also returns key -> kind."""
    kinds_by_key = {k: kind for k, _, kind in _items(state)}
    req = dict(requirements or {})
    for k, v, kind in _items(state):
        if kind != "unknown" and k not in req:
            req[k] = v
    return req, kinds_by_key


def party_and_budget(req: dict[str, Any], kinds_by_key: dict[str, str]) -> tuple[Party, int | None, bool]:
    adults = parse_int(_pick(req, "adults", "adults_count", "travelers", "people")[1]) or 1
    children = parse_children(_pick(req, "children_ages", "children", "kids", "child_age", "children_count")[1])
    pets = parse_pets(_pick(req, "pets", "pet", "animals", "with_pet", "dog")[1])
    weight = parse_int(req.get("pet_weight_kg"))
    if pets and weight:
        pets = [p.model_copy(update={"weight_kg": float(weight)}) for p in pets]
    party = Party(adults=max(1, min(adults, 9)), children_ages=children, pets=pets)
    budget_key, budget_value = _pick(req, "budget_total_rub", "budget_per_person_rub", "budget_rub", "budget", "max_budget", "budget_total")
    budget = parse_budget(budget_value)
    if budget is not None and budget_key == "budget_per_person_rub":
        # The user-facing scope is per traveller; A converts it to a total trip ceiling.
        budget *= party.people
    return party, budget, budget is not None and kinds_by_key.get(budget_key) == "hard"


def query_from_requirements(
    cat: Catalog, requirements: dict[str, Any], state: Any = None, *, allow_external_origin: bool = False,
) -> tuple[TripQuery | None, list[str]]:
    """Build TripQuery; returns (None, reasons) when a required field cannot be resolved."""
    from app.product_scope import is_worldwide_enabled
    req, kinds_by_key = merged_requirements(requirements, state)
    problems = []

    _origin_key, origin_value = _pick(req, "origin", "origin_city", "from", "departure_city")
    origin = resolve_city(cat, origin_value)
    if origin is None and allow_external_origin and isinstance(origin_value, str) and origin_value.strip():
        # Tutu resolves city names with its own location index during the
        # bounded search call. Keep that city name as the route key until then.
        origin = origin_value.strip()
    dest_key, dest_value = _pick(req, "destination", "dest", "destination_city", "to", "city")
    dest = resolve_city(cat, dest_value)
    if origin is None:
        problems.append(f"Город отправления не распознан: {req.get('origin')!r}.")
    elif origin in cat.cities and "origin" not in cat.cities[origin].roles and not allow_external_origin:
        if not is_worldwide_enabled():
            problems.append(f"Из {cat.cities[origin].name} в демо-каталоге нет отправлений.")
    if dest is None:
        problems.append(f"Направление не распознано или его нет в каталоге: {req.get('destination')!r}.")
    elif "destination" not in cat.cities[dest].roles:
        if not is_worldwide_enabled():
            problems.append(f"{cat.cities[dest].name} пока не направление каталога.")

    dates_key, dates_value = _pick(req, "dates", "date_range", "trip_dates")
    dates = parse_dates(dates_value)
    if dates is None:
        start = _to_date(_pick(req, "depart_date", "date_from", "start_date", "checkin")[1])
        end = _to_date(_pick(req, "return_date", "date_to", "end_date", "checkout")[1])
        nights = parse_int(_pick(req, "nights", "duration_nights")[1])
        if start and not end and nights:
            end = start + timedelta(days=nights)
        dates = (start, end) if start and end else None
    if dates is None:
        problems.append(f"Даты не распознаны: {req.get('dates')!r}.")
    elif dates[1] <= dates[0]:
        problems.append("Дата обратной дороги раньше даты отправления.")

    kinds = parse_transport(req, kinds_by_key)
    max_stops, max_travel_min, limits_hard = parse_travel_limits(req, kinds_by_key)
    min_stars, meal, lodging_hard = parse_lodging(req, kinds_by_key)

    if problems:
        return None, problems
    try:
        party, budget, hard = party_and_budget(req, kinds_by_key)
    except ValueError as exc:
        return None, [f"Состав путешественников не распознан: {exc}"]
    return TripQuery(
        origin_city=origin, dest_city=dest, depart_date=dates[0], return_date=dates[1], party=party,
        budget_rub=budget, budget_hard=hard, kinds=kinds,
        max_stops=max_stops, max_travel_min=max_travel_min, limits_hard=limits_hard,
        dates_hard=kinds_by_key.get(dates_key) == "hard", dest_hard=kinds_by_key.get(dest_key) == "hard",
        date_flex_days=parse_int(req.get("date_flex_days")) or 0,
        min_stars=min_stars, meal=meal, lodging_hard=lodging_hard,
    ), []
