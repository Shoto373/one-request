"""Requirement extraction, provenance checks, merging and blocking questions."""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Iterable

from pydantic import BaseModel, Field

from app.state.models import OpenQuestion, Requirement, TripState


REQUIREMENT_KEYS = {
    "origin", "destination", "dates", "travel_start_date", "travel_month", "duration_nights", "date_flex_days",
    "adults", "children_count", "children_ages", "pets", "pet_weight_kg", "budget_total_rub",
    "budget_per_person_rub", "budget_scope", "budget_preference", "transport", "max_stops", "travel_time_preference",
    "max_duration_min", "trip_style", "lodging_stars", "meal_preference", "room_preference",
    "assumption_child_flight_time", "assumption_child_menu",
}

ALIASES = {
    "origin_city": "origin", "departure_city": "origin", "from": "origin",
    "destination_city": "destination", "dest": "destination", "to": "destination",
    "date_range": "dates", "trip_dates": "dates", "date_flexibility": "date_flex_days",
    "departure_date": "travel_start_date", "start_date": "travel_start_date",
    "nights": "duration_nights", "duration": "duration_nights", "adults_count": "adults",
    "children": "children_ages", "kids": "children_ages", "child_age": "children_ages",
    "budget_rub": "budget_total_rub", "budget": "budget_total_rub", "max_budget": "budget_total_rub",
    "transport_mode": "transport", "max_travel_time": "max_duration_min", "style": "trip_style",
    "stars": "lodging_stars", "meal": "meal_preference", "meals": "meal_preference",
}
MONTHS_RU_GEN = [
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def generate_date_options(ref_date: date | None = None) -> list[str]:
    """Generate realistic, convenient, and complete date ranges for quick selection chips."""
    d = ref_date or date.today()
    options: list[str] = []

    # 1. Ближайшие выходные (пт–вс)
    days_to_fri = (4 - d.weekday()) % 7
    if days_to_fri < 3:
        days_to_fri += 7
    fri = d + timedelta(days=days_to_fri)
    sun = fri + timedelta(days=2)
    if fri.month == sun.month:
        options.append(f"{fri.day}–{sun.day} {MONTHS_RU_GEN[fri.month]} (выходные)")
    else:
        options.append(f"{fri.day} {MONTHS_RU_GEN[fri.month]} – {sun.day} {MONTHS_RU_GEN[sun.month]} (выходные)")

    # 2. Недельная поездка (7 ночей)
    week_start = fri + timedelta(days=7)
    week_end = week_start + timedelta(days=7)
    if week_start.month == week_end.month:
        options.append(f"{week_start.day}–{week_end.day} {MONTHS_RU_GEN[week_start.month]} (неделя)")
    else:
        options.append(f"{week_start.day} {MONTHS_RU_GEN[week_start.month]} – {week_end.day} {MONTHS_RU_GEN[week_end.month]} (неделя)")

    # 3. Начало следующего месяца (или через месяц, если конец текущего)
    next_m = 1 if d.month == 12 else (d.month + 2 if d.day > 20 and d.month <= 10 else d.month + 1)
    if next_m > 12:
        next_m -= 12
    options.append(f"1–8 {MONTHS_RU_GEN[next_m]} (неделя)")

    # 4. Сезонные праздники или отпуск на 2 недели
    if d.month in (10, 11, 12):
        options.append("30 декабря – 6 января")
    elif d.month in (3, 4, 5):
        options.append("1–9 мая (праздники)")
    else:
        two_w_start = week_start + timedelta(days=14)
        two_w_end = two_w_start + timedelta(days=14)
        if two_w_start.month == two_w_end.month:
            options.append(f"{two_w_start.day}–{two_w_end.day} {MONTHS_RU_GEN[two_w_start.month]} (2 недели)")
        else:
            options.append(f"{two_w_start.day} {MONTHS_RU_GEN[two_w_start.month]} – {two_w_end.day} {MONTHS_RU_GEN[two_w_end.month]} (2 недели)")

    return options


QUESTION_TEXT = {
    "origin": ("Откуда планируете выезжать?", "Город выезда нужен для точного подбора билетов Туту.", ["Москва", "Санкт-Петербург", "Казань", "Екатеринбург"]),
    "destination": ("В какой город или курорт хотите отправиться?", "Подберём лучшие отели и маршруты.", ["Сочи", "Санкт-Петербург", "Калининград", "Париж", "Дубай"]),
    "dates": ("На какие даты или в каком месяце планируете поездку?", "Проверим реальное наличие мест на нужный период.", ["14–16 ноября (выходные)", "15–22 ноября (неделя)", "1–8 декабря (неделя)", "30 декабря – 6 января"]),
    "duration_nights": ("На сколько ночей планируете отпуск?", "Рассчитаем проживание в отеле и обратные билеты.", ["На 3 ночи (выходные)", "На 7 ночей (неделя)", "На 10 ночей", "На 14 ночей (2 недели)"]),
    "adults": ("Сколько взрослых отправляется в поездку?", "Подберём подходящий номер и билеты на всех.", ["1 взрослый", "2 взрослых", "3 взрослых"]),
    "children_ages": ("Подскажите возраст ребёнка?", "Для детей действуют льготные тарифы и скидки.", ["3 года", "7 лет", "10 лет", "14 лет"]),
    "children_count": ("Сколько детей едет и сколько им лет?", "Учтём детские тарифы и правила размещения.", ["1 ребёнок", "2 детей", "без детей"]),
    "budget_scope": ("Бюджет указан на всю поездку или на одного человека?", "Поможет точнее отфильтровать предложения.", ["На поездку", "На человека"]),
    "pet_weight_kg": ("Сколько весит питомец?", "Вес важен для правил перевозки и трансфера.", ["до 5 кг", "до 8 кг", "12 кг"]),
}


class ParsedRequest(BaseModel):
    """Only deltas from the latest user text; quotes must be exact message substrings."""
    requirements: list[Requirement] = Field(default_factory=list)
    remove_keys: list[str] = Field(default_factory=list)


def _canonical(req: Requirement) -> Requirement | None:
    key = ALIASES.get(req.key, req.key)
    if key not in REQUIREMENT_KEYS:
        return None
    val = req.value
    if key in ("duration_nights", "adults", "children_count", "lodging_stars", "max_stops"):
        if isinstance(val, str):
            m = re.search(r"\d+", val)
            if m:
                val = int(m.group(0))
    elif key in ("budget_total_rub", "budget_per_person_rub"):
        if isinstance(val, str):
            clean = re.sub(r"[^\d]", "", val)
            if clean:
                val = int(clean)
    elif key == "destination" and isinstance(val, str) and val.strip():
        from app.planning.landmarks import resolve_landmark
        lm = resolve_landmark(val)
        if lm:
            return req.model_copy(update={
                "key": key,
                "value": lm["city"],
                "label": f"{lm['city']} ({lm['landmark']})",
            })
    return req.model_copy(update={"key": key, "value": val})


def validate_extraction(parsed: ParsedRequest, text: str) -> ParsedRequest:
    """Reject unsupported keys and explicit claims without a literal quote."""
    valid: dict[str, Requirement] = {}
    for raw in parsed.requirements:
        req = _canonical(raw)
        if req is None:
            continue
        if req.kind in {"hard", "soft"}:
            if req.source in {"user", "client", "input", "message"}:
                req = req.model_copy(update={"source": "user_text"})
            if req.source == "user_text" and (not req.quote or req.quote not in text):
                if isinstance(req.value, str) and req.value in text:
                    req = req.model_copy(update={"quote": req.value})
                elif str(req.value) in text:
                    req = req.model_copy(update={"quote": str(req.value)})
                elif len(text.strip().split()) <= 4:
                    req = req.model_copy(update={"quote": text.strip()})
            if req.source != "user_text" or not req.quote or req.quote not in text:
                if len(text.strip().split()) <= 4:
                    req = req.model_copy(update={"source": "user_text", "quote": text.strip()})
                else:
                    continue
            quote_l = req.quote.lower()
            if req.key in {"budget_total_rub", "budget_per_person_rub"} and not re.search(r"\d", quote_l):
                continue
            if req.key == "pet_weight_kg" and not re.search(r"\d+(?:[.,]\d+)?\s*кг|\d+(?:[.,]\d+)?\s*kg", quote_l):
                continue
            if req.key in {"budget_total_rub", "budget_per_person_rub"} and re.search(r"строго|не\s+больше|не\s+выше|не\s+дороже|максимум|лимит|\bдо\b|\bза\b|\bunder\b|\bup\s+to\b|\bmax", quote_l):
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
            # модели присылают unknown без цитаты: None in str раньше ронял весь ход
            req = req.model_copy(update={"value": None, "quote": req.quote if req.quote and req.quote in text else None})
        valid[req.key] = req

    # Detect landmarks and sights if destination is not yet resolved
    if "destination" not in valid:
        from app.planning.landmarks import resolve_landmark
        lm = resolve_landmark(text)
        if lm:
            valid["destination"] = Requirement(
                key="destination",
                value=lm["city"],
                kind="hard",
                source="user_text",
                quote=lm["quote"],
                label=f"{lm['city']} ({lm['landmark']})",
            )

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
    if (re.search(r"бюджет\w*\s+(?:меньше|ниже|снизить|уменьшить)|(?:меньше|ниже)\s+бюджет", lower)
            or re.search(r"\b(?:lower|reduce|decrease)\s+(?:the\s+)?budget\b|\bbudget\s+(?:lower|smaller)\b", lower)) and not re.search(r"\d", text):
        normalized.pop("budget_total_rub", None)
        normalized.pop("budget_per_person_rub", None)
        return ParsedRequest(requirements=list(normalized.values()), remove_keys=parsed.remove_keys), OpenQuestion(
            key="budget_total_rub", question="До какой суммы снизить бюджет?",
            why="Без суммы нельзя проверить, укладывается ли комплект в новый лимит.",
            options=["до 50 000 ₽", "до 70 000 ₽", "до 100 000 ₽", "до 150 000 ₽"],
        )

    shift = re.search(r"на\s+(?P<n>\d+|один|одну|два|две|три|четыре)\s+д(?:ень|ня|ней)\s+(?P<dir>позже|раньше)", lower)
    english_shift = re.search(r"\b(?P<n>\d+|one|two|three|four)\s+days?\s+(?P<dir>later|earlier)\b", lower)
    if shift is None and english_shift:
        shift = english_shift
    if shift:
        word_nums = {"один": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
                     "one": 1, "two": 2, "three": 3, "four": 4}
        days = int(shift.group("n")) if shift.group("n").isdigit() else word_nums[shift.group("n")]
        offset = days if shift.group("dir") in {"позже", "later"} else -days
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
                    options=generate_date_options(),
                )
        else:
            return ParsedRequest(requirements=list(normalized.values()), remove_keys=parsed.remove_keys), OpenQuestion(
                key="dates", question="От каких дат отсчитать сдвиг?", why="В текущем плане нет точного диапазона дат.",
                options=generate_date_options(),
            )

    pet_match = re.search(r"с\s+(?:нами\s+)?(?:собак\w*|п[её]с\w*|кош\w*|кот\w*)", lower)
    english_pet = re.search(r"\bwith\s+(?:a\s+)?(?P<kind>dog|cat)\b", lower)
    if pet_match is None and english_pet:
        pet_match = english_pet
    existing_pets = next((r for r in state.requirements if r.key == "pets" and r.value not in (None, False, "", [])), None)
    weight = next((r for r in state.requirements if r.key == "pet_weight_kg" and r.value not in (None, "")), None)
    if pet_match and existing_pets is None:
        kind = "cat" if re.search(r"кош|кот|\bcat\b", pet_match.group(0)) else "dog"
        if "pets" not in normalized:
            normalized["pets"] = Requirement(key="pets", value={"kind": kind}, kind="hard", source="user_text", quote=text[pet_match.start():pet_match.end()])
        if "pet_weight_kg" not in normalized and not weight:
            return ParsedRequest(requirements=list(normalized.values()), remove_keys=parsed.remove_keys), OpenQuestion(
                key="pet_weight_kg", question="Сколько весит животное?",
                why="Вес влияет на правила перевозчика и вместимость трансфера.",
                options=QUESTION_TEXT["pet_weight_kg"][2],
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
    has_start_date = _present(reqs, "travel_start_date") is not None
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
        if key == "dates":
            try:
                dyn = generate_date_options()
                if dyn:
                    options = dyn
            except Exception:
                pass
        questions.append(OpenQuestion(key=key, question=text, why=why, options=options))

    for need in keys:
        if need == "travel_window":
            if not has_dates:
                if has_start_date and not has_duration:
                    add("duration_nights")
                elif not has_start_date and not has_month:
                    add("dates")
                elif not has_duration:
                    add("duration_nights")
        elif need == "dates" and not has_dates:
            if not has_start_date:
                add("dates")
            elif not has_duration:
                add("duration_nights")
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
            options=QUESTION_TEXT["children_count"][2],
        )]
    if any(r.key == "children_count" and r.value and _present(reqs, "children_ages") is None for r in reqs):
        if not any(x.key == "children_ages" for x in q):
            q.append(OpenQuestion(
                key="children_ages", question="Сколько лет каждому ребёнку?",
                why="Возраст влияет на цену билетов и размещение.",
                options=QUESTION_TEXT["children_ages"][2],
            ))
    return q[:2]
