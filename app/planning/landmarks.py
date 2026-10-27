"""Landmark and attraction resolver: maps world and Russian sights to cities.

E.g.:
- Триумфальная арка / Эйфелева башня / Лувр -> Париж (PAR / CDG)
- Колизей / Ватикан / Пантеон -> Рим (FCO)
- Саграда Фамилия / Парк Гуэль -> Барселона (BCN)
- Биг Бен / Тауэрский мост -> Лондон (LON)
- Бурдж-Халифа / Дубай Молл -> Дубай (DXB)
- Айя-София / Голубая мечеть -> Стамбул (IST)
- Эрмитаж / Петергоф / Медный всадник -> Санкт-Петербург (LED)
- Красная площадь / Кремль / Третьяковка -> Москва (MOW)
- Кул Шариф / Казанский Кремль -> Казань (KZN)
- Красная Поляна / Роза Хутор -> Сочи (AER)
- Куршская коса / Остров Канта -> Калининград (KGD)
"""
from __future__ import annotations

import re
from typing import Any

# Normalized landmark patterns -> City details
LANDMARKS_CATALOG: list[dict[str, Any]] = [
    # --- Париж ---
    {
        "city": "Париж",
        "city_id": "PAR",
        "name_en": "Paris",
        "landmark": "Триумфальная арка",
        "patterns": [
            r"триу[м]?фальн\w*\s+арк\w*",
            r"арк\w*\s+на\s+елисейск\w*",
            r"arc\s+de\s+triomphe",
        ],
    },
    {
        "city": "Париж",
        "city_id": "PAR",
        "name_en": "Paris",
        "landmark": "Эйфелева башня",
        "patterns": [
            r"эйфелев\w*\s+башн\w*",
            r"башн\w*\s+эйфел\w*",
            r"eiffel\s+tower",
        ],
    },
    {
        "city": "Париж",
        "city_id": "PAR",
        "name_en": "Paris",
        "landmark": "Лувр",
        "patterns": [
            r"\bлувр\w*",
            r"\blouvre\b",
            r"музе\w*\s+лувр\w*",
        ],
    },
    {
        "city": "Париж",
        "city_id": "PAR",
        "name_en": "Paris",
        "landmark": "Нотр-Дам де Пари",
        "patterns": [
            r"нотр[\s\-]дам\w*",
            r"собор\w*\s+парижск\w*\s+богоматер\w*",
            r"notre[\s\-]dame",
        ],
    },
    {
        "city": "Париж",
        "city_id": "PAR",
        "name_en": "Paris",
        "landmark": "Елисейские поля",
        "patterns": [
            r"елисейск\w*\s+пол\w*",
            r"шамп[\s\-]элизе",
            r"champs[\s\-]elysees",
        ],
    },
    {
        "city": "Париж",
        "city_id": "PAR",
        "name_en": "Paris",
        "landmark": "Монмартр",
        "patterns": [
            r"монмартр\w*",
            r"мулен\s+руж",
            r"сакре[\s\-]к[её]р",
        ],
    },
    {
        "city": "Париж",
        "city_id": "PAR",
        "name_en": "Paris",
        "landmark": "Версаль",
        "patterns": [
            r"версал\w*",
            r"versailles",
        ],
    },
    {
        "city": "Париж",
        "city_id": "PAR",
        "name_en": "Paris",
        "landmark": "Диснейленд Париж",
        "patterns": [
            r"диснейленд\w*\s+париж\w*",
            r"парижск\w*\s+диснейленд\w*",
        ],
    },

    # --- Рим ---
    {
        "city": "Рим",
        "city_id": "FCO",
        "name_en": "Rome",
        "landmark": "Колизей",
        "patterns": [
            r"колизе\w*",
            r"colosseum",
            r"colosseo",
        ],
    },
    {
        "city": "Рим",
        "city_id": "FCO",
        "name_en": "Rome",
        "landmark": "Ватикан",
        "patterns": [
            r"ватикан\w*",
            r"vatican",
            r"собор\w*\s+свят\w*\s+петр\w*",
            r"сикстинск\w*\s+капелл\w*",
        ],
    },
    {
        "city": "Рим",
        "city_id": "FCO",
        "name_en": "Rome",
        "landmark": "Фонтан Треви",
        "patterns": [
            r"фонтан\w*\s+треви",
            r"треви",
            r"trevi",
        ],
    },
    {
        "city": "Рим",
        "city_id": "FCO",
        "name_en": "Rome",
        "landmark": "Пантеон",
        "patterns": [
            r"пантеон\w*",
            r"pantheon",
            r"римск\w*\s+форум\w*",
        ],
    },

    # --- Барселона ---
    {
        "city": "Барселона",
        "city_id": "BCN",
        "name_en": "Barcelona",
        "landmark": "Саграда Фамилия",
        "patterns": [
            r"саград\w*[\s\-]фамили\w*",
            r"храм\w*\s+свят\w*\s+семейств\w*",
            r"sagrada\s+familia",
        ],
    },
    {
        "city": "Барселона",
        "city_id": "BCN",
        "name_en": "Barcelona",
        "landmark": "Парк Гуэль",
        "patterns": [
            r"парк\w*\s+гуэл\w*",
            r"park\s+guell",
            r"дом\w*\s+бальо",
            r"каса\s+батльо",
            r"дом\w*\s+мила",
        ],
    },

    # --- Лондон ---
    {
        "city": "Лондон",
        "city_id": "LON",
        "name_en": "London",
        "landmark": "Биг-Бен",
        "patterns": [
            r"биг[\s\-]бен\w*",
            r"big\s+ben",
        ],
    },
    {
        "city": "Лондон",
        "city_id": "LON",
        "name_en": "London",
        "landmark": "Тауэрский мост",
        "patterns": [
            r"тауэрск\w*\s+мост\w*",
            r"tower\s+bridge",
            r"лондонск\w*\s+глаз\w*",
            r"london\s+eye",
            r"букингемск\w*\s+дворец\w*",
        ],
    },

    # --- Дубай ---
    {
        "city": "Дубай",
        "city_id": "DXB",
        "name_en": "Dubai",
        "landmark": "Бурдж-Халифа",
        "patterns": [
            r"бурдж[\s\-]халиф\w*",
            r"burj\s+khalifa",
            r"дуба[йея]\s+молл\w*",
            r"пальм\w*\s+джумейр\w*",
            r"бурдж\s+аль\s+араб",
            r"отел\w*\s+парус",
        ],
    },

    # --- Стамбул ---
    {
        "city": "Стамбул",
        "city_id": "IST",
        "name_en": "Istanbul",
        "landmark": "Айя-София",
        "patterns": [
            r"айя[\s\-]софи\w*",
            r"собор\w*\s+свят\w*\s+софи\w*",
            r"hagia\s+sophia",
            r"голуб\w*\s+мечет\w*",
            r"галатск\w*\s+башн\w*",
            r"дворец\w*\s+топкап\w*",
            r"босфор\w*",
        ],
    },

    # --- Санкт-Петербург ---
    {
        "city": "Санкт-Петербург",
        "city_id": "LED",
        "name_en": "Saint Petersburg",
        "landmark": "Эрмитаж",
        "patterns": [
            r"эрмитаж\w*",
            r"зимн\w*\s+дворец\w*",
            r"дворцов\w*\s+площад\w*",
            r"hermitage",
        ],
    },
    {
        "city": "Санкт-Петербург",
        "city_id": "LED",
        "name_en": "Saint Petersburg",
        "landmark": "Петергоф",
        "patterns": [
            r"петергоф\w*",
            r"фонтан\w*\s+петергоф\w*",
            r"peterhof",
        ],
    },
    {
        "city": "Санкт-Петербург",
        "city_id": "LED",
        "name_en": "Saint Petersburg",
        "landmark": "Исаакиевский собор",
        "patterns": [
            r"исааки\w*",
            r"исаакиевск\w*\s+собор\w*",
            r"спас\w*\s+на\s+кров\w*",
            r"медн\w*\s+всадник\w*",
            r"петропавловск\w*\s+крепост\w*",
        ],
    },

    # --- Москва ---
    {
        "city": "Москва",
        "city_id": "MOW",
        "name_en": "Moscow",
        "landmark": "Красная площадь",
        "patterns": [
            r"красн\w*\s+площад\w*",
            r"red\s+square",
            r"храм\w*\s+васили\w*\s+блаженн\w*",
        ],
    },
    {
        "city": "Москва",
        "city_id": "MOW",
        "name_en": "Moscow",
        "landmark": "Московский Кремль",
        "patterns": [
            r"московск\w*\s+кремл\w*",
            r"\bкремл\w*\b",
            r"kremlin",
            r"вднх",
            r"третьяковк\w*",
            r"третьяковск\w*\s+галере\w*",
            r"москва[\s\-]сити",
        ],
    },

    # --- Казань ---
    {
        "city": "Казань",
        "city_id": "KZN",
        "name_en": "Kazan",
        "landmark": "Кул Шариф",
        "patterns": [
            r"кул[\s\-]шариф\w*",
            r"мечет\w*\s+кул[\s\-]шариф\w*",
            r"казанск\w*\s+кремл\w*",
            r"башн\w*\s+сююмбик\w*",
        ],
    },

    # --- Сочи ---
    {
        "city": "Сочи",
        "city_id": "AER",
        "name_en": "Sochi",
        "landmark": "Красная Поляна / Роза Хутор",
        "patterns": [
            r"красн\w*\s+полян\w*",
            r"роз\w*\s+хутор\w*",
            r"олимпийск\w*\s+парк\w*",
            r"сочи[\s\-]парк\w*",
            r"гора\s+ахун",
        ],
    },

    # --- Калининград ---
    {
        "city": "Калининград",
        "city_id": "KGD",
        "name_en": "Kaliningrad",
        "landmark": "Куршская коса",
        "patterns": [
            r"куршск\w*\s+кос\w*",
            r"остров\w*\s+кант\w*",
            r"могил\w*\s+кант\w*",
            r"кафедральн\w*\s+собор\w*\s+в\s+калининград\w*",
            r"рыбн\w*\s+деревн\w*",
        ],
    },

    # --- Альпы / Инсбрук ---
    {
        "city": "Инсбрук",
        "city_id": "INN",
        "name_en": "Innsbruck",
        "landmark": "Золотая крыша",
        "patterns": [
            r"золот\w*\s+крыш\w*",
            r"нордкетт\w*",
            r"nordkette",
        ],
    },

    # --- Нью-Йорк ---
    {
        "city": "Нью-Йорк",
        "city_id": "NYC",
        "name_en": "New York",
        "landmark": "Статуя Свободы",
        "patterns": [
            r"стату\w*\s+свобод\w*",
            r"statue\s+of\s+liberty",
            r"таймс[\s\-]сквер\w*",
            r"times\s+square",
            r"эмпайр[\s\-]стейт\w*",
            r"центральн\w*\s+парк\w*\s+в\s+нью[\s\-]йорк\w*",
        ],
    },

    # --- Египет / Каир ---
    {
        "city": "Каир",
        "city_id": "CAI",
        "name_en": "Cairo",
        "landmark": "Пирамиды Гизы",
        "patterns": [
            r"пирамид\w*\s+гиз\w*",
            r"египетск\w*\s+пирамид\w*",
            r"велик\w*\s+сфинкс\w*",
            r"\bсфинкс\w*\b",
            r"пирамид\w*\s+хеопс\w*",
        ],
    },

    # --- Токио ---
    {
        "city": "Токио",
        "city_id": "NRT",
        "name_en": "Tokyo",
        "landmark": "Сибуя / Хатико",
        "patterns": [
            r"перекресток\w*\s+сибу\w*",
            r"\bсибу\w*",
            r"памятник\w*\s+хатик\w*",
            r"стату\w*\s+хатик\w*",
            r"токийск\w*\s+башн\w*",
            r"гор\w*\s+фудзи\w*",
            r"фудзиям\w*",
        ],
    },

    # --- Прага ---
    {
        "city": "Прага",
        "city_id": "PRG",
        "name_en": "Prague",
        "landmark": "Карлов мост",
        "patterns": [
            r"карлов\w*\s+мост\w*",
            r"пражск\w*\s+град\w*",
            r"астрономическ\w*\s+час\w*",
            r"староместск\w*\s+площад\w*",
        ],
    },

    # --- Венеция ---
    {
        "city": "Венеция",
        "city_id": "VCE",
        "name_en": "Venice",
        "landmark": "Гранд-канал / Сан-Марко",
        "patterns": [
            r"гранд[\s\-]канал\w*",
            r"площад\w*\s+сан[\s\-]марк\w*",
            r"мост\w*\s+риальт\w*",
            r"мост\w*\s+вздох\w*",
            r"гондол\w*",
        ],
    },
]


def resolve_landmark(text: str) -> dict[str, Any] | None:
    """Detect if text contains or references a known landmark or sight.

    Returns dict with city, city_id, name_en, landmark, and matched quote, or None.
    """
    if not text or not isinstance(text, str):
        return None

    cleaned = text.lower().replace("ё", "е").strip()

    for item in LANDMARKS_CATALOG:
        for pat in item["patterns"]:
            m = re.search(pat, cleaned, re.IGNORECASE)
            if m:
                # Extract actual substring from original text
                start, end = m.span()
                quote = text[start:end] if len(text) >= end else m.group(0)
                return {
                    "city": item["city"],
                    "city_id": item["city_id"],
                    "name_en": item["name_en"],
                    "landmark": item["landmark"],
                    "quote": quote,
                }

    return None
