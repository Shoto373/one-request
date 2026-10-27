"""Rule-based request parser: the agent's fallback when no LLM is configured or the provider fails.

Produces the same ParsedRequest delta as the LLM parser, with every hard/soft quote taken
verbatim from the message, so validate_extraction and the rest of the turn work unchanged.
It covers the catalog vocabulary (cities, dates, party, pets, budget, transport) in Russian
and English; anything it cannot read stays unknown and the agent asks for it.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from app.agent.requirements import ParsedRequest
from app.catalog.store import get_catalog
from app.planning.query import resolve_city
from app.product_scope import fixed_destination_id
from app.state.models import Requirement, TripState

# canonical city name -> spellings (lowercase stems/inflections, Russian and English)
CITIES: dict[str, tuple[str, ...]] = {
    "Москва": ("москв", "moscow", "мск"),
    "Санкт-Петербург": ("санкт-петербург", "петербург", "питер", "спб", "saint petersburg", "st. petersburg", "st petersburg", "petersburg"),
    "Екатеринбург": ("екатеринбург", "екб", "yekaterinburg", "ekaterinburg"),
    "Сочи": ("сочи", "адлер", "sochi"),
    "Калининград": ("калининград", "kaliningrad"),
    "Казань": ("казан", "kazan"),
    "Стамбул": ("стамбул", "istanbul"),
    "Анталья": ("анталь", "antalya"),
    "Шарм-эль-Шейх": ("шарм", "sharm"),
    "Дубай": ("дубай", "дубае", "дубая", "dubai"),
    "Ереван": ("ереван", "yerevan"),
    "Париж": ("париж", "paris"),
    "Прага": ("праг", "prague", "praha"),
    "Рим": ("рим", "rome", "roma"),
    "Инсбрук": ("инсбрук", "innsbruck"),
    "Рязань": ("рязан", "ryazan"),
}
# places the user may name that are not in the catalog: kept as destination so the agent answers honestly
FOREIGN_HINT = re.compile(r"(?:\bв|\bto)\s+([A-ZА-ЯЁ][\w\-ёЁ]+)")

MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7, "август": 8,
    "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
MONTH_WORD = (r"(январ[ья]|феврал[ья]|марта?|апрел[ья]|ма[йя]|июн[ья]|июл[ья]|августа?|сентябр[ья]|октябр[ья]|ноябр[ья]|декабр[ья]"
              r"|january|february|march|april|may|june|july|august|september|october|november|december"
              r"|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)")
MONTH_WORD_NC = re.sub(r"\((?!\?)", "(?:", MONTH_WORD)
MONTH_IN = (r"(?:\bв\s+(январе|феврале|марте|апреле|мае|июне|июле|августе|сентябре|октябре|ноябре|декабре)"
            r"|\bin\s+(january|february|march|april|may|june|july|august|september|october|november|december))")

NUMBER_WORDS = {
    "один": 1, "одна": 1, "одного": 1, "двое": 2, "двоих": 2, "два": 2, "две": 2, "трое": 3, "троих": 3, "три": 3,
    "четверо": 4, "четверых": 4, "четыре": 4, "пятеро": 5, "пять": 5,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
}


def _month(word: str) -> int | None:
    w = word.lower()
    if w.startswith("ма"):  # май/мая/мае vs март/марта
        return 3 if w.startswith("мар") else 5
    for stem, n in MONTHS.items():
        if w.startswith(stem):
            return n
    return None


def _req(key: str, value, kind: str, quote: str | None, label: str | None = None) -> Requirement:
    source = "agent" if kind == "assumption" else "user_text"
    return Requirement(key=key, value=value, kind=kind, source=source,
                       quote=None if kind == "assumption" else quote, label=label)


NON_CITY_WORDS = re.compile(
    r"^(?:\d|центр|ноч|дн|сут|недел|выходн|weekend|человек|взросл|ребен|дет|двоих|троих|руб|тыс|бюджет|январ|феврал|март|апрел|ма[йея]|июн|июл|август|сентябр|октябр|ноябр|декабр|поездк|отпуск|тур|билет|отел)",
    re.I,
)


COMMAND_WORDS = re.compile(
    r"\b(предложи|покажи|найди|выбери|давай|ищу|нужен|нужно|можно|вариант\w*|отел\w*|комнаты?|номер\w*|разны\w*|лучш\w*|дешев\w*|дорог\w*)\b",
    re.I,
)


def _find_city(fragment: str) -> str | None:
    f = fragment.lower().strip()
    if not f or NON_CITY_WORDS.search(f) or COMMAND_WORDS.search(f) or "," in fragment:
        return None
    if len(f.split()) > 2:
        return None
    from app.planning.landmarks import resolve_landmark
    lm = resolve_landmark(f)
    if lm:
        return lm["city"]
    for name, spellings in CITIES.items():
        if any(s in f for s in spellings):
            return name
    # Keep destination parsing aligned with catalog/config additions; the MCP
    # integration should not need a parser rewrite for a newly covered city.
    city_id = resolve_city(get_catalog(), fragment)
    if city_id in get_catalog().cities:
        return get_catalog().cities[city_id].name
    return None


def _money(raw: str, suffix: str | None) -> int | None:
    digits = re.sub(r"[\s ]", "", raw).replace(",", ".")
    try:
        value = float(digits)
    except ValueError:
        return None
    s = (suffix or "").lower()
    if s.startswith(("тыс", "k", "к")):
        value *= 1000
    elif s.startswith(("млн", "m")):
        value *= 1_000_000
    return int(value) if value >= 1000 else None


def _year_for(month: int, day: int, now: datetime) -> int:
    return now.year + 1 if (month, day) < (now.month, now.day) else now.year


def rule_parse(text: str, state: TripState, now: datetime) -> ParsedRequest:
    reqs: dict[str, Requirement] = {}
    low = text.lower()
    open_keys = [q.key for q in state.open_questions]

    def put(r: Requirement) -> None:
        reqs.setdefault(r.key, r)

    # --- origin / destination ---
    # Capture a user's city even when it is outside the demo catalog. The live
    # Tutu search resolves the name and returns its geo id; this parser must not
    # silently constrain origins to the few cities in the demo fixtures.
    m = re.search(
        r"(?:\bиз|\bот|\bfrom)\s+(.+?)(?=\s+(?:в|во|на|к|с|со|to|for|with)\b|[;.!?]|$)",
        text, re.I,
    )
    origin_text = m.group(1).strip(" ,;") if m else None
    if origin_text is None and "origin" in open_keys:
        answer = text.strip().strip(".!?;,: ")
        if re.fullmatch(r"(?:город\s+)?[\wЁёА-Яа-я.,\-]+(?:\s+[\wЁёА-Яа-я.,\-]+){0,5}", answer) and answer.lower() not in {"да", "нет", "yes", "no"}:
            origin_text = re.sub(r"^город\s+", "", answer, flags=re.I)
    if origin_text:
        origin_text = re.sub(r"^город\s+", "", origin_text, flags=re.I).strip()
        city = _find_city(origin_text) or origin_text
        put(_req("origin", city, "hard", text[m.start():m.end()] if m else text.strip()))
    if "destination" in open_keys and re.fullmatch(r"\s*(да|согласен|согласна|ок|okay|yes|верно)[.!]?\s*", text, re.I):
        destination_id = fixed_destination_id()
        put(Requirement(key="destination", value=destination_id, kind="hard", source="user_edit", quote=None,
                        label="Пользователь согласился на доступное направление"))
    # Landmark and attraction detection (e.g. "увидеть триумфальную арку" -> Париж)
    from app.planning.landmarks import resolve_landmark
    lm = resolve_landmark(text)
    if lm:
        put(_req("destination", lm["city"], "hard", lm["quote"], label=f"{lm['city']} ({lm['landmark']})"))

    neg_prefix_pat = re.compile(
        r"(?:\bне\s+(?:хочу|буду|поеду|полечу|еду|надо|хотим|собираюсь|планирую)\s*|\bне\s+|\bвместо\s+)$",
        re.I,
    )
    pos_dest = None
    neg_dests = set()
    PREP_VERBS = {"в", "во", "на", "до", "из", "от", "с", "со", "к", "to", "хочу", "еду", "лечу", "буду"}
    # Check "в центре <города>" (e.g. "в центре Парижа", "в центре Рима", "в центре Сочи")
    m_centre_city = re.search(r"в\s+центр\w*\s+([A-Za-zА-Яа-яЁё\-]+)", text, re.I)
    if m_centre_city:
        c_city = _find_city(m_centre_city.group(1))
        if c_city and c_city != (reqs.get("origin").value if "origin" in reqs else None):
            pos_dest = (c_city, m_centre_city.group(0))

    # 1. First check 2-word compound cities (e.g. Нижний Новгород, Великий Новгород, Красная Поляна)
    for m in re.finditer(r"(?:\bв|\bво|\bна|\bдо|\bto)\s+([A-Za-zА-Яа-яЁё\-]+)\s+([A-Za-zА-Яа-яЁё\-]+)", text, re.I):
        if m.group(2).lower() not in PREP_VERBS:
            compound = f"{m.group(1)} {m.group(2)}"
            city = _find_city(compound)
            if city and city != (reqs.get("origin").value if "origin" in reqs else None):
                prefix = text[:m.start()].strip()
                if bool(neg_prefix_pat.search(prefix)):
                    neg_dests.add(city)
                else:
                    pos_dest = (city, m.group(0))

    # 2. Check 1-word cities (never swallowing the next preposition or verb)
    for m in re.finditer(r"(?:\bв|\bво|\bна|\bдо|\bto)\s+([A-Za-zА-Яа-яЁё\-]+)", text, re.I):
        city = _find_city(m.group(1))
        if city and city != (reqs.get("origin").value if "origin" in reqs else None):
            prefix = text[:m.start()].strip()
            if bool(neg_prefix_pat.search(prefix)):
                neg_dests.add(city)
            else:
                pos_dest = (city, m.group(0))

    if pos_dest:
        reqs["destination"] = _req("destination", pos_dest[0], "hard", pos_dest[1])
    elif "destination" in reqs and reqs["destination"].value in neg_dests:
        del reqs["destination"]

    if "destination" not in reqs and not re.search(r"куда\s+(?:угодно|нибудь)|anywhere|somewhere", low):
        # a capitalized place after «в/to» that the catalog does not know: keep it, the agent will say there is no data
        for m in FOREIGN_HINT.finditer(text):
            word = m.group(1)
            prefix = text[:m.start()].strip()
            is_negated = bool(neg_prefix_pat.search(prefix))
            if not is_negated and not _find_city(word) and not re.match(r"(ноябр|декабр|январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр)", word.lower()):
                reqs["destination"] = _req("destination", word, "hard", m.group(0))
                break

    if "destination" not in reqs and not re.search(r"куда\s+(?:угодно|нибудь)|anywhere|somewhere", low):
        # User answering the destination question directly (e.g. clicking "Париж", "Дубай", "Сочи")
        dest_ans = text.strip().strip(".!?;,: ")
        if len(dest_ans.split()) <= 4 and dest_ans.lower() not in {"да", "нет", "yes", "no"}:
            city = _find_city(dest_ans)
            if city and city != (reqs.get("origin").value if "origin" in reqs else None):
                put(_req("destination", city, "hard", text.strip()))

    # --- dates ---
    # Cross-month ranges: e.g. "30 октября – 1 ноября", "30 декабря – 6 января", "с 28 ноября по 5 декабря"
    m_cross = re.search(
        rf"(?:с\s*)?(?P<d1>\d{{1,2}})\s+(?P<m1>{MONTH_WORD_NC})\s*(?:[-–—]|по|to|until)\s*(?P<d2>\d{{1,2}})\s+(?P<m2>{MONTH_WORD_NC})(?:\s+(?P<year>20\d\d))?",
        low,
    )
    if m_cross:
        d1, mon1 = int(m_cross.group("d1")), _month(m_cross.group("m1"))
        d2, mon2 = int(m_cross.group("d2")), _month(m_cross.group("m2"))
        year = m_cross.group("year")
        if mon1 and mon2:
            y1 = int(year) if year else _year_for(mon1, d1, now)
            y2 = y1 + 1 if mon2 < mon1 else y1
            try:
                start, end = date(y1, mon1, d1), date(y2, mon2, d2)
                put(_req("dates", {"start": start.isoformat(), "end": end.isoformat()}, "hard", text[m_cross.start():m_cross.end()]))
            except ValueError:
                pass

    if "dates" not in reqs:
        m = re.search(rf"(?:с\s*)?(?P<d1>\d{{1,2}})\s*(?:[-–—]|по|to|until)\s*(?P<d2>\d{{1,2}})\s+(?P<month>{MONTH_WORD_NC})(?:\s+(?P<year>20\d\d))?", low)
        m_en = re.search(rf"(?P<month>{MONTH_WORD_NC})\s+(?P<d1>\d{{1,2}})\s*(?:[-–—]|to|until)\s*(?P<d2>\d{{1,2}})(?:,?\s+(?P<year>20\d\d))?", low)
        if m or m_en:
            if m:
                d1, d2, mon, year = int(m.group("d1")), int(m.group("d2")), _month(m.group("month")), m.group("year")
                span = m.span()
            else:
                mon, d1, d2, year = _month(m_en.group("month")), int(m_en.group("d1")), int(m_en.group("d2")), m_en.group("year")
                span = m_en.span()
            if mon:
                y = int(year) if year else _year_for(mon, d2, now)
                try:
                    start, end = date(y, mon, d1), date(y, mon, d2)
                    put(_req("dates", {"start": start.isoformat(), "end": end.isoformat()}, "hard", text[span[0]:span[1]]))
                except ValueError:
                    pass
    # A single calendar date is a known departure date, not a complete trip window.
    # Keep it separately so the agent can ask for the return date/duration instead
    # of repeating the generic date question or inventing a trip length.
    if "dates" not in reqs:
        single = re.search(rf"(?<!\d)(?P<day>\d{{1,2}})\s+(?P<month>{MONTH_WORD_NC})(?:\s+(?P<year>20\d{{2}}))?(?!\w)", low)
        if single:
            day, mon = int(single.group("day")), _month(single.group("month"))
            if mon:
                try:
                    year = int(single.group("year")) if single.group("year") else _year_for(mon, day, now)
                    start = date(year, mon, day)
                    put(_req("travel_start_date", start.isoformat(), "hard", text[single.start():single.end()]))
                except ValueError:
                    pass
    remove_keys: list[str] = []
    if re.search(r"разны\w*.*(?:отел|вари[а-я]*нт)|один\s+5.*один\s+4|разн\w+\s+по\s+звезд", low):
        remove_keys.extend(["lodging_stars", "room_preference"])

    if "dates" not in reqs:
        m_xmas = re.search(r"рождеств\w*|christmas", low)
        if m_xmas:
            q_xmas = text[m_xmas.start():m_xmas.end()]
            put(_req("dates", {"start": f"{now.year}-12-30", "end": f"{now.year + 1}-01-06"}, "soft", q_xmas, label="Рождественские праздники"))
        else:
            m = re.search(MONTH_IN, low)
            if m:
                word = m.group(1) or m.group(2)
                put(_req("travel_month", _month(word), "hard", text[m.start():m.end()]))
            elif re.search(r"нов(?:ый|ого)\s+год|new\s+year", low):
                q = re.search(r"нов(?:ый|ого)\s+год|new\s+year", text, re.I)
                put(_req("travel_month", 12, "soft", q.group(0)))

    # --- duration ---
    m = re.search(r"(\d{1,2})\s*(ноч|nights?|дн|days?|сут)", low)
    if m:
        n = int(m.group(1))
        nights = n if m.group(2).startswith(("ноч", "night", "сут")) else max(1, n - 1)
        put(_req("duration_nights", nights, "hard", text[m.start():m.end()]))
    elif (m := re.search(r"(на\s+)?недел[юяи]|a\s+week|one\s+week", low)):
        put(_req("duration_nights", 7, "hard", text[m.start():m.end()]))
    elif (m := re.search(r"выходны[ех]|weekend", low)):
        put(_req("duration_nights", 2, "soft", text[m.start():m.end()]))

    # --- party ---
    m = re.search(r"(\d+|" + "|".join(NUMBER_WORDS) + r")\s+(взросл\w*|adults?)", low)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else NUMBER_WORDS[m.group(1)]
        put(_req("adults", n, "hard", text[m.start():m.end()]))
    elif (m := re.search(r"\b(вдвоём|вдвоем|на\s+двоих|двое|вдвоем|for\s+two|two\s+of\s+us)\b", low)):
        put(_req("adults", 2, "hard", text[m.start():m.end()]))
    elif (m := re.search(r"\b(на\s+троих|втроём|втроем|трое|for\s+three)\b", low)):
        put(_req("adults", 3, "hard", text[m.start():m.end()]))
    elif (m := re.search(r"\b(я\s+один|я\s+одна|один\s+взрослый|одна\s+взрослая|solo|alone|just\s+me)\b", low)):
        put(_req("adults", 1, "hard", text[m.start():m.end()]))

    ages = [int(a) for a in re.findall(r"(\d{1,2})\s*(?:лет|год|years?\s*old|y\.?o\.?)", low)]
    child = re.search(r"реб[её]н\w*|дет(?:ьми|ей|и)\b|сын\w*|доч\w*|kids?|child(?:ren)?", low)
    if child:
        quote = text[child.start():child.end()]
        count = re.search(r"(\d|" + "|".join(NUMBER_WORDS) + r")\s+(?:реб[её]н\w*|дет\w*|kids?|child\w*)", low)
        if count:
            n = int(count.group(1)) if count.group(1).isdigit() else NUMBER_WORDS[count.group(1)]
            put(_req("children_count", n, "hard", text[count.start():count.end()]))
        elif re.search(r"реб[её]н|child\b|kid\b|сын|доч", quote.lower()):
            put(_req("children_count", 1, "hard", quote))
        if ages:
            age_quote = re.search(r"\d{1,2}\s*(?:лет|год|years?\s*old)", text, re.I)
            put(_req("children_ages", ages, "hard", age_quote.group(0) if age_quote else quote))

    # --- pets ---
    m = re.search(r"соба\w*|пс[оа]\w*|щен\w*|\bdog\b|puppy", low)
    c = re.search(r"\bкош\w*|\bкот\w*|\bcat\b|kitten", low)
    if m:
        put(_req("pets", {"kind": "dog"}, "hard", text[m.start():m.end()]))
    elif c:
        put(_req("pets", {"kind": "cat"}, "hard", text[c.start():c.end()]))
    w = re.search(r"(\d+(?:[.,]\d+)?)\s*(кг|kg)", low)
    if w and (m or c or "pet_weight_kg" in open_keys or "pets" in state.model_dump().get("requirements", [])):
        put(_req("pet_weight_kg", float(w.group(1).replace(",", ".")), "hard", text[w.start():w.end()]))

    # --- budget ---
    b = re.search(r"(строго\s+|не\s+дороже\s+|максимум\s+|до\s+|за\s+|бюджет\w*\s+(?:до\s+)?|budget\s+(?:strictly\s+)?(?:of\s+)?|under\s+|up\s+to\s+)"
                  r"(\d[\d\s .,]*\d|\d+)\s*(тыс\w*|k\b|к\b|млн|m\b)?\s*(?:₽|руб\w*|rub\w*|р\.?)?", low)
    if b:
        amount = _money(b.group(2), b.group(3))
        if amount:
            quote = text[b.start():b.end()].strip()
            per_person = re.search(r"на\s+(?:человека|одного|каждого)|per\s+person", low)
            key = "budget_per_person_rub" if per_person else "budget_total_rub"
            strict = re.search(r"строго|не\s+дороже|максимум|\bдо\b|\bза\b|strictly|up\s+to|under", quote.lower()) \
                and not re.search(r"примерно|около|желательно|about|around", low)
            put(_req(key, amount, "hard" if strict else "soft", quote))
            if re.search(r"на\s+(?:всю\s+)?поездку|на\s+всех|total|for\s+the\s+trip", low) and not per_person:
                q = re.search(r"на\s+(?:всю\s+)?поездку|на\s+всех|total|for\s+the\s+trip", text, re.I)
                put(_req("budget_scope", "trip", "hard", q.group(0)))

    if (m := re.search(r"версал\w*", low)):
        put(_req("room_preference", "Версаль", "soft", text[m.start():m.end()], label="Рядом с Версалем"))
    elif (m := re.search(r"в\s+центр\w*|центр\w*\s+париж|центр\w*\s+город", low)):
        put(_req("room_preference", "центр", "soft", text[m.start():m.end()], label="В центре города"))

    if re.search(r"дорог\w*|люкс|luxury|премиум|premium|элитн", low):
        q = re.search(r"дорог\w*|люкс|luxury|премиум|premium|элитн\w*", text, re.I)
        put(_req("budget_preference", "luxury", "soft", q.group(0)))
    elif (m := re.search(r"недорог\w*|дешев\w*|дёшев\w*|подешевле|бюджетн\w*|эконом\w*|cheap\w*|budget\s+trip|low[\s-]cost", low)):
        put(_req("budget_preference", "low_cost", "soft", text[m.start():m.end()]))

    # --- transport and route preferences ---
    if (m := re.search(r"поезд(?!к)\w*|\bжд\b|ж/д|by\s+train|\btrain\b", low)):
        put(_req("transport", "train", "hard", text[m.start():m.end()]))
    elif (m := re.search(r"самол[её]т\w*|перел[её]т\w*\s+только|by\s+plane|fly\b|flight", low)) and not re.search(r"без\s+(?:долгих\s+)?перел", low):
        put(_req("transport", "flight", "soft", text[m.start():m.end()]))
    if (m := re.search(r"без\s+пересад\w*|прям\w+\s+рейс\w*|только\s+прям\w*|direct|non[\s-]?stop", low)):
        put(_req("max_stops", 0, "hard" if "только" in m.group(0) else "soft", text[m.start():m.end()]))
    elif (m := re.search(r"с\s+пересадк\w*|пересадк\w*|layover|connecting", low)):
        put(_req("max_stops", 1, "soft", text[m.start():m.end()]))
    if (m := re.search(r"без\s+долг\w*\s+перел[её]т\w*|недолго\s+лететь|short\s+flight\w*|not\s+far", low)):
        put(_req("travel_time_preference", "short", "soft", text[m.start():m.end()]))

    # --- style, hotel ---
    if (m := re.search(r"т[её]пл\w*\s+мор\w*|\bмор[еяю]\w*|пляж\w*|warm\s+sea|beach|seaside|тепл\w*|warm", low)):
        put(_req("trip_style", "тёплое море" if re.search(r"мор|пляж|sea|beach", m.group(0)) else "тепло", "soft", text[m.start():m.end()]))
    if (m := re.search(r"(\d)\s*(?:зве[зд]{1,2}\w*|звё[зд]{1,2}\w*|\*|stars?)", low)):
        if not re.search(r"разны|один\s+\d.*один\s+\d", low):
            put(_req("lodging_stars", int(m.group(1)), "hard" if re.search(r"строго|только|обязательно|strictly|only", low) else "soft",
                     text[m.start():m.end()]))
    if (m := re.search(r"вс[её]\s+включено|all[\s-]inclusive|ультра\s*вс|\bai\b", low)):
        put(_req("meal_preference", "all_inclusive", "hard" if re.search(r"обязательно|только|строго", low) else "soft",
                 text[m.start():m.end()]))
    elif (m := re.search(r"полупансион|завтрак\s*\+\s*ужин|\bhb\b", low)):
        put(_req("meal_preference", "half_board", "soft", text[m.start():m.end()]))
    elif (m := re.search(r"полный\s+пансион|\bfb\b", low)):
        put(_req("meal_preference", "full_board", "soft", text[m.start():m.end()]))
    elif (m := re.search(r"с\s+завтрак\w*|завтрак|\bbb\b|breakfast", low)):
        put(_req("meal_preference", "breakfast", "soft", text[m.start():m.end()]))
    elif (m := re.search(r"без\s+питания|\bro\b|room\s*only", low)):
        put(_req("meal_preference", "none", "soft", text[m.start():m.end()]))

    # --- short answers to the open question ---
    stripped = text.strip()
    # возраст в ответе — это дети, какой бы вопрос ни был открыт («5 лет» на «сколько ночей?» — не 5 ночей)
    if "children_ages" not in reqs and ages and re.fullmatch(r"[\d\s,иand]+(?:лет|год\w*|years?(?:\s*old)?)\.?", low.strip()):
        put(_req("children_ages", ages, "hard", stripped))
    for key in open_keys[:1]:
        existing = next((r for r in state.requirements if r.key == key), None)
        if key in reqs and not (key == "origin" and existing and existing.source == "product_scope"):
            continue
        if key in ("origin", "destination") and (city := _find_city(stripped)):
            put(_req(key, city, "hard", stripped))
        elif key == "adults" and (m := re.search(r"\d+|" + "|".join(NUMBER_WORDS), low)):
            n = int(m.group(0)) if m.group(0).isdigit() else NUMBER_WORDS[m.group(0)]
            put(_req("adults", n, "hard", text[m.start():m.end()]))
        elif key in ("children_ages", "children_count") and (nums := re.findall(r"\d{1,2}", low)):
            put(_req("children_ages", [int(x) for x in nums], "hard", stripped))
        elif key in ("meal_preference", "meal"):
            if re.search(r"вс[её]\s+включено|all|\bai\b", low):
                put(_req("meal_preference", "all_inclusive", "soft", stripped))
            elif re.search(r"полупансион|ужин|\bhb\b", low):
                put(_req("meal_preference", "half_board", "soft", stripped))
            elif re.search(r"полный|\bfb\b", low):
                put(_req("meal_preference", "full_board", "soft", stripped))
            elif re.search(r"завтрак|\bbb\b", low):
                put(_req("meal_preference", "breakfast", "soft", stripped))
            elif re.search(r"без\s*питан|\bro\b", low):
                put(_req("meal_preference", "none", "soft", stripped))
        elif key == "pet_weight_kg" and (m := re.search(r"\d+(?:[.,]\d+)?", low)):
            put(_req("pet_weight_kg", float(m.group(0).replace(",", ".")), "hard", stripped if re.search(r"кг|kg", low) else text[m.start():m.end()] + ""))
        elif key == "duration_nights" and not ages and (m := re.search(r"\d{1,2}", low)):
            put(_req("duration_nights", int(m.group(0)), "hard", text[m.start():m.end()]))
        elif key == "budget_scope":
            if re.search(r"человек|person|одного", low):
                put(_req("budget_scope", "person", "hard", stripped))
            elif re.search(r"поездк|всех|trip|total", low):
                put(_req("budget_scope", "trip", "hard", stripped))

    return ParsedRequest(requirements=list(reqs.values()), remove_keys=remove_keys)
