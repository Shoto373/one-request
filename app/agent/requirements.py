"""Requirement extraction, provenance checks, merging and blocking questions."""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Iterable

from pydantic import BaseModel, Field

from app.state.models import OpenQuestion, Requirement, TripState


REQUIREMENT_KEYS = {
    "origin", "destination", "dates", "travel_month", "duration_nights", "date_flex_days",
    "adults", "children_count", "children_ages", "pets", "pet_weight_kg", "budget_total_rub",
    "budget_per_person_rub", "budget_scope", "budget_preference", "transport", "max_stops", "travel_time_preference",
    "max_duration_min", "trip_style", "lodging_stars", "meal_preference", "room_preference",
    "assumption_child_flight_time", "assumption_child_menu",
}

ALIASES = {
    "origin_city": "origin", "departure_city": "origin", "from": "origin",
    "destination_city": "destination", "dest": "destination", "to": "destination",
    "date_range": "dates", "trip_dates": "dates", "date_flexibility": "date_flex_days",
    "nights": "duration_nights", "duration": "duration_nights", "adults_count": "adults",
    "children": "children_ages", "kids": "children_ages", "child_age": "children_ages",
    "budget_rub": "budget_total_rub", "budget": "budget_total_rub", "max_budget": "budget_total_rub",
    "transport_mode": "transport", "max_travel_time": "max_duration_min", "style": "trip_style",
    "stars": "lodging_stars", "meal": "meal_preference", "meals": "meal_preference",
}
QUESTION_TEXT = {
    "origin": ("Откуда вы отправляетесь?", "Город отправления определяет доступный транспорт.", ["Москва", "Санкт-Петербург", "Екатеринбург"]),
    "destination": ("Куда хотите поехать?", "Направление нужно, чтобы собрать конкретный комплект.", []),
    "dates": ("На какие даты или в каком месяце планировать поездку?", "Окно поездки нужно для проверки доступности.", []),
    "duration_nights": ("Сколько ночей планировать?", "Длительность нужна для оценки полной стоимости.", ["3 ночи", "7 ночей", "10 ночей"]),
    "adults": ("Сколько взрослых едет?", "Число взрослых влияет на цену и размещение.", ["1", "2", "3"]),
    "children_ages": ("Сколько лет каждому ребёнку?", "Возраст влияет на тарифы и вместимость.", []),
    "children_count": ("Сколько детей едет и сколько лет каждому?", "Число и возраст детей нужны для тарифа и размещения.", []),
    "budget_scope": ("Бюджет указан на всю поездку или на одного человека?", "Основание бюджета меняет проверку стоимости.", ["На поездку", "На человека"]),
    "pet_weight_kg": ("Сколько весит животное?", "Вес влияет на правила перевозчика и вместимость трансфера.", []),
}


class ParsedRequest(BaseModel):
    """Only deltas from the latest user text; quotes must be exact message substrings."""
    requirements: list[Requirement] = Field(default_factory=list)
    remove_keys: list[str] = Field(default_factory=list)


def _canonical(req: Requirement) -> Requirement | None:
    key = ALIASES.get(req.key, req.key)
    if key not in REQUIREMENT_KEYS:
        return None
    return req.model_copy(update={"key": key})


def validate_extraction(parsed: ParsedRequest, text: str) -> ParsedRequest:
    """Reject unsupported keys and explicit claims without a literal quote."""
    valid: dict[str, Requirement] = {}
    for raw in parsed.requirements:
        req = _canonical(raw)
        if req is None:
            continue
        if req.kind in {"hard", "soft"}:
            if req.source != "user_text" or not req.quote or req.quote not in text:
                continue
            quote_l = req.quote.lower()
            if req.key in {"budget_total_rub", "budget_per_person_rub"} and not re.search(r"\d", quote_l):
                continue
            if req.key == "pet_weight_kg" and not re.search(r"\d+(?:[.,]\d+)?\s*кг|\d+(?:[.,]\d+)?\s*kg", quote_l):
                continue
            if req.key in {"budget_total_rub", "budget_per_person_rub"} and re.search(r"строго|не\s+больше|не\s+выше|не\s+дороже|максимум|лимит|\bдо\b", quote_l):
                req = req.model_copy(update={"kind": "hard"})
            if req.key in {"budget_total_rub", "budget_per_person_rub"} and re.search(r"примерно|около|желательно", quote_l):
                req = req.model_copy(update={"kind": "soft"})
            if req.key == "adults" and re.search(r"реб[её]н|дет", quote_l) and not re.search(r"взросл|\bя\b", quote_l):
                continue
            if req.key == "max_duration_min" and not re.search(r"\d", quote_l):
                continue
        elif req.kind == "assumption":
            # Inferences never carry a user quote and are labeled as agent-originated.
            req = req.model_copy(update={"source": "agent", "quote": None})
        elif req.kind == "unknown":
            req = req.model_copy(update={"value": None, "quote": req.quote if req.quote in text else None})
        valid[req.key] = req

    # Guard the common ambiguity rules even if the model returns an invalid parse.
    child_match = re.search(r"\bс\s+(?:реб[её]нком|детьми)\b|\bреб[её]н(?:ок|ком|ка|ку)\b|\bдет(?:и|ьми|ей)\b", text, re.I)
    if child_match:
        quote = child_match.group(0)
        singular = any(word in quote.lower() for word in ("ребёнком", "ребенком", "ребёнок", "ребенок"))
        if "children_count" not in valid:
            if singular:
                valid["children_count"] = Requirement(key="children_count", value=1, kind="hard", source="user_text", quote=quote)
            elif "children_ages" not in valid:
                valid["children_count"] = Requirement(key="children_count", value=None, kind="unknown", source="user_text", quote=quote)
        age_req = valid.get("children_ages")
        if age_req is None or age_req.kind == "unknown":
            valid["children_ages"] = Requirement(key="children_ages", value=None, kind="unknown", source="missing")

    # A vague budget is a preference, never a synthesized hard amount.
    if re.search(r"\b(недорого|подешевле|бюджетно|дешево|дёшево)\b", text, re.I):
        if "budget_preference" not in valid:
            valid["budget_preference"] = Requirement(  # deliberately not a planning filter key
                key="budget_preference", value="low_cost", kind="soft", source="user_text",
                quote=re.search(r"недорого|подешевле|бюджетно|дешево|дёшево", text, re.I).group(0),
            )
    budget_req = valid.get("budget_total_rub") or valid.get("budget_per_person_rub")
    if budget_req:
        quote = budget_req.quote or ""
        per_person = re.search(r"на\s+человек|с\s+человек|per\s+person", quote, re.I)
        whole_trip = re.search(r"на\s+(?:всю\s+)?поездк|на\s+всех|total\s+trip", quote, re.I)
        if per_person:
            if budget_req.key == "budget_total_rub":
                valid.pop("budget_total_rub", None)
                budget_req = budget_req.model_copy(update={"key": "budget_per_person_rub"})
                valid["budget_per_person_rub"] = budget_req
            valid["budget_scope"] = Requirement(key="budget_scope", value="person", kind="hard", source="user_text", quote=quote)
        elif whole_trip:
            if budget_req.key == "budget_per_person_rub":
                valid.pop("budget_per_person_rub", None)
                budget_req = budget_req.model_copy(update={"key": "budget_total_rub"})
                valid["budget_total_rub"] = budget_req
            valid["budget_scope"] = Requirement(key="budget_scope", value="trip", kind="hard", source="user_text", quote=quote)
        elif "budget_scope" not in valid or valid["budget_scope"].kind == "unknown":
            valid["budget_scope"] = Requirement(key="budget_scope", value=None, kind="unknown", source="missing")
    return ParsedRequest(requirements=list(valid.values()), remove_keys=[ALIASES.get(k, k) for k in parsed.remove_keys if k])


def merge_requirement_changes(
    existing: Iterable[Requirement],
    incoming: Iterable[Requirement],
    *,
    remove_keys: Iterable[str] = (),
    suppressed_assumptions: Iterable[str] = (),
) -> tuple[list[Requirement], list[str]]:
    """One merge path for chat and UI edits; explicit updates replace by key."""
    values = {r.key: r for r in existing}
    suppressed = set(suppressed_assumptions)
    for key in remove_keys:
        old = values.pop(key, None)
        if old and old.kind == "assumption":
            suppressed.add(key)
    for requirement in incoming:
        if requirement.kind == "assumption" and requirement.key in suppressed:
            continue
        previous = values.get(requirement.key)
        if requirement.kind == "unknown" and previous is not None and previous.kind != "unknown":
            continue
        values[requirement.key] = requirement
        if requirement.source in {"user_text", "user_edit"}:
            suppressed.discard(requirement.key)
    return list(values.values()), sorted(suppressed)


def normalize_contextual_changes(parsed: ParsedRequest, text: str, state: TripState) -> tuple[ParsedRequest, OpenQuestion | None]:
    """Normalize relative chat edits against the current trip without guessing."""
    normalized = {r.key: r for r in parsed.requirements}
    lower = text.lower().replace("ё", "е")
    if re.search(r"бюджет\w*\s+(?:меньше|ниже|снизить|уменьшить)|(?:меньше|ниже)\s+бюджет", lower) and not re.search(r"\d", text):
        normalized.pop("budget_total_rub", None)
        normalized.pop("budget_per_person_rub", None)
        return ParsedRequest(requirements=list(normalized.values()), remove_keys=parsed.remove_keys), OpenQuestion(
            key="budget_total_rub", question="До какой суммы снизить бюджет?",
            why="Без суммы нельзя проверить, укладывается ли комплект в новый лимит.",
        )

    shift = re.search(r"на\s+(?P<n>\d+|один|одну|два|две|три|четыре)\s+д(?:ень|ня|ней)\s+(?P<dir>позже|раньше)", lower)
    if shift:
        word_nums = {"один": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4}
        days = int(shift.group("n")) if shift.group("n").isdigit() else word_nums[shift.group("n")]
        offset = days if shift.group("dir") == "позже" else -days
        current = next((r for r in state.requirements if r.key == "dates" and r.value is not None), None)
        if current and isinstance(current.value, dict):
            try:
                start = date.fromisoformat(str(current.value["start"])) + timedelta(days=offset)
                end = date.fromisoformat(str(current.value["end"])) + timedelta(days=offset)
                normalized["dates"] = Requirement(
                    key="dates", value={"start": start.isoformat(), "end": end.isoformat()},
                    kind=current.kind if current.kind in {"hard", "soft"} else "hard",
                    source="user_text", quote=text[shift.start():shift.end()],
                )
            except (KeyError, TypeError, ValueError):
                return ParsedRequest(requirements=list(normalized.values()), remove_keys=parsed.remove_keys), OpenQuestion(
                    key="dates", question="Какие даты поставить?", why="Не удалось однозначно сдвинуть текущий диапазон дат.",
                )
        else:
            return ParsedRequest(requirements=list(normalized.values()), remove_keys=parsed.remove_keys), OpenQuestion(
                key="dates", question="От каких дат отсчитать сдвиг?", why="В текущем плане нет точного диапазона дат.",
            )

    pet_match = re.search(r"с\s+(?:нами\s+)?(?:собак\w*|п[её]с\w*|кош\w*|кот\w*)", lower)
    existing_pets = next((r for r in state.requirements if r.key == "pets" and r.value not in (None, False, "", [])), None)
    weight = next((r for r in state.requirements if r.key == "pet_weight_kg" and r.value not in (None, "")), None)
    if pet_match and existing_pets is None:
        kind = "cat" if re.search(r"кош|кот", pet_match.group(0)) else "dog"
        if "pets" not in normalized:
            normalized["pets"] = Requirement(key="pets", value={"kind": kind}, kind="hard", source="user_text", quote=text[pet_match.start():pet_match.end()])
        if "pet_weight_kg" not in normalized and not weight:
            return ParsedRequest(requirements=list(normalized.values()), remove_keys=parsed.remove_keys), OpenQuestion(
                key="pet_weight_kg", question="Сколько весит животное?",
                why="Вес влияет на правила перевозчика и вместимость трансфера.",
            )
    return ParsedRequest(requirements=list(normalized.values()), remove_keys=parsed.remove_keys), None


def _present(requirements: Iterable[Requirement], key: str) -> Requirement | None:
    return next((r for r in requirements if r.key == key and r.kind != "unknown" and r.value not in (None, "")), None)


def _has_exact_dates(requirements: list[Requirement]) -> bool:
    item = _present(requirements, "dates")
    if not item:
        return False
    value = item.value
    try:
        if isinstance(value, dict):
            start, end = date.fromisoformat(str(value.get("start", ""))), date.fromisoformat(str(value.get("end", "")))
            return end > start
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return date.fromisoformat(str(value[1])) > date.fromisoformat(str(value[0]))
        if isinstance(value, str):
            matches = re.findall(r"20\d{2}-\d{2}-\d{2}", value)
            return len(matches) >= 2 and date.fromisoformat(matches[1]) > date.fromisoformat(matches[0])
    except ValueError:
        return False
    return False


def next_questions(
    requirements: Iterable[Requirement], *, action: str, required_inputs: Iterable[str] | None = None,
) -> list[OpenQuestion]:
    """Ask only for missing inputs required by the next A tool, at most two."""
    reqs = list(requirements)
    present = {r.key for r in reqs if r.kind != "unknown" and r.value not in (None, "")}
    keys = list(required_inputs or ("origin", "travel_window", "adults"))
    has_dates = _has_exact_dates(reqs)
    has_month = "travel_month" in present
    has_duration = "duration_nights" in present
    children_count = _present(reqs, "children_count")
    children_count_unknown = any(r.key == "children_count" and r.kind == "unknown" for r in reqs)
    child_ages = _present(reqs, "children_ages")
    budget = _present(reqs, "budget_total_rub") or _present(reqs, "budget_per_person_rub")
    pets = _present(reqs, "pets")
    questions: list[OpenQuestion] = []

    def add(key: str) -> None:
        text, why, options = QUESTION_TEXT[key]
        questions.append(OpenQuestion(key=key, question=text, why=why, options=options))

    for need in keys:
        if need == "travel_window":
            if not has_dates:
                if not has_month:
                    add("dates")
                elif not has_duration:
                    add("duration_nights")
        elif need == "dates" and not has_dates:
            add("dates")
        elif need in QUESTION_TEXT and need not in present:
            add(need)
    if "children_ages" in present and child_ages is None and children_count:
        add("children_ages")
    if children_count and child_ages is None and "children_ages" not in present:
        add("children_ages")
    if children_count_unknown:
        add("children_count")
    if budget and budget.key == "budget_total_rub" and "budget_scope" not in present:
        # A phrase like "strictly under 100k for two" may still mean per-person or total.
        scope = str(budget.value).lower()
        if not any(token in scope for token in ("total", "поездк", "человек", "person", "на всех")):
            add("budget_scope")
    if pets and "pet_weight_kg" not in present:
        add("pet_weight_kg")
    # Keep stable order and do not emit the same blocker twice.
    unique: dict[str, OpenQuestion] = {}
    for q in questions:
        unique.setdefault(q.key, q)
    return list(unique.values())[:2]


def missing_concrete_inputs(requirements: Iterable[Requirement]) -> list[OpenQuestion]:
    """Trip 1 bundle builder needs exact dates, origin, destination and party."""
    reqs = list(requirements)
    keys = ["origin", "destination", "dates", "adults"]
    q = next_questions(reqs, action="build_bundles", required_inputs=keys)
    if any(r.key == "children_count" and r.kind == "unknown" for r in reqs):
        q = [item for item in q if item.key != "adults"] + [OpenQuestion(
            key="children_count", question="Сколько детей едет и сколько лет каждому?",
            why="Число и возраст детей влияют на тарифы и вместимость.",
        )]
    if any(r.key == "children_count" and r.value and _present(reqs, "children_ages") is None for r in reqs):
        if not any(x.key == "children_ages" for x in q):
            q.append(OpenQuestion(key="children_ages", question="Сколько лет каждому ребёнку?", why="Возраст влияет на цену билетов и размещение."))
    return q[:2]
