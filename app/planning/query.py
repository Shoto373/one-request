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


# Forms an LLM or a user may leave unnormalized; stems below cover most case endings.
CITY_ALIASES = {
    "мск": "MOW", "moscow": "MOW",
    "спб": "LED", "питер": "LED", "петербург": "LED", "санкт петербург": "LED", "ленинград": "LED",
    "st petersburg": "LED", "saint petersburg": "LED",
    "екб": "SVX", "екат": "SVX", "свердловск": "SVX",
    "сочи": "AER", "адлер": "AER", "sochi": "AER",
    "шарм": "SSH", "шарм эль шейх": "SSH", "шарм эш шейх": "SSH", "sharm": "SSH",
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
    if raw.upper() in cat.cities:
        return raw.upper()
    if raw.upper() in cat.hubs:
        return cat.hubs[raw.upper()].city_id
    text = _norm(raw)
    if text in CITY_ALIASES:
        return CITY_ALIASES[text]
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
            return cid
    return None


def _to_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        m = _ISO.search(value)
        return date.fromisoformat(m.group(0)) if m else None
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
    party = Party(adults=max(1, min(adults, 9)), children_ages=children, pets=pets)
    budget_key, budget_value = _pick(req, "budget_total_rub", "budget_rub", "budget", "max_budget", "budget_total")
    budget = parse_budget(budget_value)
    return party, budget, budget is not None and kinds_by_key.get(budget_key) == "hard"


def query_from_requirements(
    cat: Catalog, requirements: dict[str, Any], state: Any = None
) -> tuple[TripQuery | None, list[str]]:
    """Build TripQuery; returns (None, reasons) when a required field cannot be resolved."""
    req, kinds_by_key = merged_requirements(requirements, state)
    problems = []

    origin = resolve_city(cat, _pick(req, "origin", "origin_city", "from", "departure_city")[1])
    dest = resolve_city(cat, _pick(req, "destination", "dest", "destination_city", "to", "city")[1])
    if origin is None:
        problems.append(f"Город отправления не распознан: {req.get('origin')!r}.")
    elif "origin" not in cat.cities[origin].roles:
        problems.append(f"Из {cat.cities[origin].name} в каталоге нет отправлений: только Москва, Санкт-Петербург, Екатеринбург.")
    if dest is None:
        problems.append(f"Направление не распознано или его нет в каталоге: {req.get('destination')!r}.")
    elif "destination" not in cat.cities[dest].roles:
        problems.append(f"{cat.cities[dest].name} пока не направление каталога.")

    dates = parse_dates(_pick(req, "dates", "date_range", "trip_dates")[1])
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

    kinds = parse_kinds(_pick(req, "transport", "transport_mode", "transport_type", "by")[1]) or ("flight", "train")

    if problems:
        return None, problems
    try:
        party, budget, hard = party_and_budget(req, kinds_by_key)
    except ValueError as exc:
        return None, [f"Состав путешественников не распознан: {exc}"]
    return TripQuery(
        origin_city=origin, dest_city=dest, depart_date=dates[0], return_date=dates[1], party=party,
        budget_rub=budget, budget_hard=hard, kinds=kinds,
    ), []
