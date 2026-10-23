"""Hand-curated reference for the catalog pipeline.

Real facts (Wikidata ids, IANA zones, visa regime, airports, stations) live here;
generated conditions are controlled by the knobs below and by ``RANDOM_SEED``.
"""

from __future__ import annotations

from datetime import date

RANDOM_SEED = 42
VISA_CHECKED_AT = date(2026, 10, 23)

# Dated transport is generated for these days (start inclusive, end exclusive).
WINDOW_START = date(2026, 10, 26)
WINDOW_END = date(2027, 1, 16)

CLIMATE_AIR_YEARS = (2021, 2025)
CLIMATE_SEA_YEARS = (2023, 2025)  # Open-Meteo Marine has no SST before mid-2022

HOTELS_PER_CITY = 7


def _visa(regime, documents, note, max_stay_days=None):
    return {
        "regime": regime,
        "max_stay_days": max_stay_days,
        "documents": documents,
        "note": note,
        "checked_at": VISA_CHECKED_AT.isoformat(),
    }


VISA_RU = _visa("domestic", ["паспорт РФ"], "Поездка по России, виза не нужна.")
VISA_KGD = _visa(
    "domestic",
    ["паспорт РФ"],
    "Прямой рейс — как внутренний. По суше через Литву нужен упрощённый проездной документ (УПД/ФТД).",
)
VISA = {
    "RU": VISA_RU,
    "TR": _visa(
        "visa_free",
        ["загранпаспорт"],
        "Безвиз до 60 дней в течение 180 дней. Срок действия паспорта уточняйте перед поездкой.",
        60,
    ),
    "EG": _visa(
        "visa_on_arrival",
        ["загранпаспорт (действует не менее 6 месяцев на дату въезда)"],
        "Только курорты Южного Синая до 15 дней — бесплатный штамп «Синай» в аэропорту; "
        "для дольше или за пределы региона — виза по прибытии (25 USD, до 30 дней).",
        30,
    ),
    "AE": _visa(
        "visa_free",
        ["загранпаспорт (действует не менее 6 месяцев на дату въезда)"],
        "Бесплатная отметка о въезде в аэропорту, до 90 дней.",
        90,
    ),
    "AM": _visa(
        "visa_free",
        ["паспорт РФ или загранпаспорт"],
        "Безвиз до 180 дней в году, въезд возможен по внутреннему паспорту РФ.",
        180,
    ),
}

# id = IATA city code. hotel_area = (lat, lon, radius_m) for the OSM hotel search,
# sea_point = water point for Open-Meteo Marine (None = no sea).
CITIES = [
    {
        "id": "MOW", "wikidata": "Q649", "name": "Москва", "name_en": "Moscow",
        "country_code": "RU", "country": "Россия", "timezone": "Europe/Moscow", "currency": "RUB",
        "roles": ["origin"], "airports": ["SVO", "DME", "VKO"], "stations": ["st_mow_kaz", "st_mow_len"],
        "tags": ["city"],
    },
    {
        "id": "LED", "wikidata": "Q656", "name": "Санкт-Петербург", "name_en": "Saint Petersburg",
        "country_code": "RU", "country": "Россия", "timezone": "Europe/Moscow", "currency": "RUB",
        "roles": ["origin"], "airports": ["LED"], "stations": ["st_led_msk"],
        "tags": ["city"],
    },
    {
        "id": "SVX", "wikidata": "Q887", "name": "Екатеринбург", "name_en": "Yekaterinburg",
        "country_code": "RU", "country": "Россия", "timezone": "Asia/Yekaterinburg", "currency": "RUB",
        "roles": ["origin"], "airports": ["SVX"], "stations": ["st_svx"],
        "tags": ["city"],
    },
    {
        "id": "AER", "wikidata": "Q39420", "name": "Сочи", "name_en": "Sochi",
        "country_code": "RU", "country": "Россия", "timezone": "Europe/Moscow", "currency": "RUB",
        "roles": ["destination"], "airports": ["AER"], "stations": ["st_aer_sochi", "st_aer_adler"],
        "tags": ["sea", "beach", "resort", "mountains", "domestic"],
        "hotel_area": (43.5853, 39.7203, 5000), "sea_point": (43.55, 39.65),
        "price_level_rub": 6500, "road_speed_kmh": 35,
    },
    {
        "id": "KGD", "wikidata": "Q1829", "name": "Калининград", "name_en": "Kaliningrad",
        "country_code": "RU", "country": "Россия", "timezone": "Europe/Kaliningrad", "currency": "RUB",
        "roles": ["destination"], "airports": ["KGD"], "stations": [],
        "tags": ["sea", "city", "culture", "domestic"],
        "hotel_area": (54.7104, 20.5101, 4000), "sea_point": (54.98, 20.40),
        "price_level_rub": 5000, "road_speed_kmh": 45,
    },
    {
        "id": "KZN", "wikidata": "Q900", "name": "Казань", "name_en": "Kazan",
        "country_code": "RU", "country": "Россия", "timezone": "Europe/Moscow", "currency": "RUB",
        "roles": ["destination"], "airports": ["KZN"], "stations": ["st_kzn"],
        "tags": ["city", "culture", "food", "domestic", "train"],
        "hotel_area": (55.7909, 49.1144, 3500), "sea_point": None,
        "price_level_rub": 5500, "road_speed_kmh": 38,
    },
    {
        "id": "IST", "wikidata": "Q406", "name": "Стамбул", "name_en": "Istanbul",
        "country_code": "TR", "country": "Турция", "timezone": "Europe/Istanbul", "currency": "TRY",
        "roles": ["destination"], "airports": ["IST", "SAW"], "stations": [],
        "tags": ["city", "culture", "food", "shopping", "sea"],
        "hotel_area": (41.0115, 28.9800, 3500), "sea_point": (40.98, 28.97),
        "price_level_rub": 7500, "road_speed_kmh": 32,
    },
    {
        "id": "AYT", "wikidata": "Q6487", "name": "Анталья", "name_en": "Antalya",
        "country_code": "TR", "country": "Турция", "timezone": "Europe/Istanbul", "currency": "TRY",
        "roles": ["destination"], "airports": ["AYT"], "stations": [],
        "tags": ["sea", "beach", "resort", "all_inclusive", "family"],
        "hotel_area": (36.8700, 30.7400, 13000), "sea_point": (36.82, 30.70),
        "price_level_rub": 6000, "road_speed_kmh": 50,
    },
    {
        "id": "SSH", "wikidata": "Q644", "name": "Шарм-эль-Шейх", "name_en": "Sharm El Sheikh",
        "country_code": "EG", "country": "Египет", "timezone": "Africa/Cairo", "currency": "EGP",
        "roles": ["destination"], "airports": ["SSH"], "stations": [],
        "tags": ["sea", "beach", "resort", "diving", "all_inclusive", "family", "warm_winter"],
        "hotel_area": (27.9140, 34.3290, 10000), "sea_point": (27.88, 34.40),
        "price_level_rub": 5500, "road_speed_kmh": 55,
    },
    {
        "id": "DXB", "wikidata": "Q612", "name": "Дубай", "name_en": "Dubai",
        "country_code": "AE", "country": "ОАЭ", "timezone": "Asia/Dubai", "currency": "AED",
        "roles": ["destination"], "airports": ["DXB"], "stations": [],
        "tags": ["sea", "beach", "city", "shopping", "warm_winter", "luxury"],
        "hotel_area": (25.2000, 55.2600, 8000), "sea_point": (25.22, 55.20),
        "price_level_rub": 11000, "road_speed_kmh": 55,
    },
    {
        "id": "EVN", "wikidata": "Q1953", "name": "Ереван", "name_en": "Yerevan",
        "country_code": "AM", "country": "Армения", "timezone": "Asia/Yerevan", "currency": "AMD",
        "roles": ["destination"], "airports": ["EVN"], "stations": [],
        "tags": ["city", "culture", "food", "wine", "mountains"],
        "hotel_area": (40.1811, 44.5136, 3000), "sea_point": None,
        "price_level_rub": 6000, "road_speed_kmh": 35,
    },
]
CITY = {c["id"]: c for c in CITIES}

# Station id -> Wikidata id (coordinates and names are fetched from Wikidata).
STATIONS = {
    "st_mow_kaz": {"wikidata": "Q1432585", "city_id": "MOW", "name": "Москва, Казанский вокзал"},
    "st_mow_len": {"wikidata": "Q1344454", "city_id": "MOW", "name": "Москва, Ленинградский вокзал"},
    "st_led_msk": {"wikidata": "Q139318", "city_id": "LED", "name": "Санкт-Петербург, Московский вокзал"},
    "st_svx": {"wikidata": "Q3180012", "city_id": "SVX", "name": "Екатеринбург-Пассажирский"},
    "st_kzn": {"wikidata": "Q34085", "city_id": "KZN", "name": "Казань-Пассажирская"},
    "st_aer_sochi": {"wikidata": "Q3180156", "city_id": "AER", "name": "Сочи, ж/д вокзал"},
    "st_aer_adler": {"wikidata": "Q4057610", "city_id": "AER", "name": "Адлер, ж/д вокзал"},
}

AIRPORT_NAMES_RU = {
    "SVO": "Шереметьево", "DME": "Домодедово", "VKO": "Внуково", "LED": "Пулково",
    "SVX": "Кольцово", "AER": "Сочи (Адлер)", "KGD": "Храброво", "KZN": "Казань",
    "IST": "Стамбул (новый)", "SAW": "Сабиха Гёкчен", "AYT": "Анталья", "SSH": "Шарм-эль-Шейх",
    "DXB": "Дубай", "EVN": "Звартноц",
}

# --------------------------------------------------------------------------- flights (synthetic)

CARRIERS = {
    "SU": {"name": "Аэрофлот", "price": 1.15, "fares": ("light", "standard", "flex"),
           "pets": {"cabin": True, "cabin_max_kg": 8, "cabin_fee_rub": 5000, "hold": True, "hold_fee_rub": 10000}},
    "DP": {"name": "Победа", "price": 0.72, "fares": ("light", "standard"),
           "pets": {"cabin": True, "cabin_max_kg": 10, "cabin_fee_rub": 2999, "hold": False, "hold_fee_rub": 0}},
    "S7": {"name": "S7 Airlines", "price": 1.0, "fares": ("light", "standard", "flex"),
           "pets": {"cabin": True, "cabin_max_kg": 10, "cabin_fee_rub": 4500, "hold": True, "hold_fee_rub": 9000}},
    "U6": {"name": "Уральские авиалинии", "price": 0.92, "fares": ("light", "standard", "flex"),
           "pets": {"cabin": True, "cabin_max_kg": 8, "cabin_fee_rub": 4000, "hold": True, "hold_fee_rub": 8000}},
    "FV": {"name": "Россия", "price": 1.0, "fares": ("light", "standard", "flex"),
           "pets": {"cabin": True, "cabin_max_kg": 8, "cabin_fee_rub": 5000, "hold": True, "hold_fee_rub": 10000}},
    "TK": {"name": "Turkish Airlines", "price": 1.2, "fares": ("light", "standard", "flex"),
           "pets": {"cabin": True, "cabin_max_kg": 8, "cabin_fee_rub": 7000, "hold": True, "hold_fee_rub": 14000}},
    "PC": {"name": "Pegasus", "price": 0.8, "fares": ("light", "standard"),
           "pets": {"cabin": True, "cabin_max_kg": 10, "cabin_fee_rub": 6000, "hold": False, "hold_fee_rub": 0}},
    "ZF": {"name": "Azur Air", "price": 0.82, "fares": ("standard",),
           "pets": {"cabin": True, "cabin_max_kg": 8, "cabin_fee_rub": 5000, "hold": False, "hold_fee_rub": 0}},
    "N4": {"name": "Северный ветер (Nordwind)", "price": 0.85, "fares": ("standard",),
           "pets": {"cabin": False, "cabin_max_kg": None, "cabin_fee_rub": 0, "hold": False, "hold_fee_rub": 0,
                    "note": "чартерная программа, перевозка животных не предусмотрена"}},
    "EK": {"name": "Emirates", "price": 1.5, "fares": ("light", "standard", "flex"),
           "pets": {"cabin": False, "cabin_max_kg": None, "cabin_fee_rub": 0, "hold": False, "hold_fee_rub": 0}},
    "FZ": {"name": "flydubai", "price": 0.95, "fares": ("light", "standard", "flex"),
           "pets": {"cabin": False, "cabin_max_kg": None, "cabin_fee_rub": 0, "hold": False, "hold_fee_rub": 0}},
}
# Pets to the UAE travel only as manifested cargo with an import permit: no passenger option.
PET_BLOCKED_COUNTRIES = {"AE": "ввоз животных в ОАЭ — только грузом с разрешением, пассажирская перевозка невозможна"}

# Route: (carrier, from_hub, to_hub, local departure times, weekdays (0=Mon) or None = daily,
#         operating range (start, end) or None = whole window).
# Return flights are mirrored automatically. Times "23:55" etc. are local at from_hub.
ROUTES = [
    # Moscow
    ("SU", "SVO", "AER", ["07:40", "13:25", "23:55"], None, None),
    ("DP", "VKO", "AER", ["06:10", "11:45", "19:30"], None, None),
    ("S7", "DME", "AER", ["09:15", "16:50"], None, None),
    ("SU", "SVO", "KGD", ["08:30", "15:10", "20:45"], None, None),
    ("DP", "VKO", "KGD", ["07:05", "22:50"], None, None),
    ("S7", "DME", "KGD", ["23:40"], None, None),
    ("SU", "SVO", "KZN", ["09:50", "18:20"], None, None),
    ("DP", "VKO", "KZN", ["12:30"], None, None),
    ("S7", "DME", "KZN", ["21:15"], [0, 2, 4, 6], None),
    ("SU", "SVO", "IST", ["10:05", "16:40"], None, None),
    ("TK", "VKO", "IST", ["08:20", "18:55"], None, None),
    ("PC", "VKO", "SAW", ["05:50"], None, None),
    ("DP", "VKO", "SAW", ["14:15"], [1, 3, 5], None),
    ("SU", "SVO", "AYT", ["09:00", "15:35"], None, None),
    ("DP", "VKO", "AYT", ["07:25"], None, None),
    ("ZF", "VKO", "AYT", ["02:40"], [0, 2, 4, 6], None),
    ("SU", "SVO", "SSH", ["10:20"], None, None),
    ("ZF", "VKO", "SSH", ["04:10"], [1, 3, 5], None),
    ("N4", "SVO", "SSH", ["13:45"], [2, 6], None),
    ("SU", "SVO", "DXB", ["10:45", "21:30"], None, None),
    ("EK", "DME", "DXB", ["15:30", "23:55"], None, None),
    ("FZ", "VKO", "DXB", ["06:35", "17:20"], None, None),
    ("SU", "SVO", "EVN", ["08:10", "14:20", "22:35"], None, None),
    ("DP", "VKO", "EVN", ["06:55", "17:05"], None, None),
    # Saint Petersburg
    ("SU", "LED", "AER", ["08:15", "17:30"], None, None),
    ("DP", "LED", "AER", ["06:40", "21:10"], None, None),
    ("FV", "LED", "AER", ["12:20"], None, None),
    ("SU", "LED", "KGD", ["09:40", "19:05"], None, None),
    ("DP", "LED", "KGD", ["07:50", "16:25"], None, None),
    ("FV", "LED", "KZN", ["11:15"], None, None),
    ("DP", "LED", "KZN", ["20:30"], [0, 3, 5], None),
    ("TK", "LED", "IST", ["09:30"], None, None),
    ("PC", "LED", "SAW", ["16:05"], [0, 2, 4, 6], None),
    ("FV", "LED", "AYT", ["08:45"], None, None),
    ("ZF", "LED", "AYT", ["23:30"], [0, 4], None),
    ("FV", "LED", "SSH", ["10:30"], [5], None),
    ("FZ", "LED", "DXB", ["12:50"], None, None),
    ("SU", "LED", "EVN", ["10:55"], None, None),
    ("FV", "LED", "EVN", ["19:40"], [1, 3, 5, 6], None),
    # Yekaterinburg
    ("U6", "SVX", "AER", ["07:20", "15:45"], None, None),
    ("DP", "SVX", "AER", ["11:05"], None, None),
    ("U6", "SVX", "KGD", ["09:25"], [1, 4, 6], None),
    ("TK", "SVX", "IST", ["06:50"], None, None),
    ("PC", "SVX", "SAW", ["13:30"], [1, 5], None),
    # Night charter with 01:40 arrival next day; the program ends mid-November (out of schedule after).
    ("U6", "SVX", "AYT", ["22:30"], None, ("2026-10-26", "2026-11-16")),
    ("U6", "SVX", "AYT", ["22:30"], [2, 6], ("2026-11-16", "2027-01-16")),
    ("U6", "SVX", "SSH", ["08:40"], [1, 5], None),
    ("FZ", "SVX", "DXB", ["10:15"], None, None),
    ("U6", "SVX", "DXB", ["23:10"], [0, 3, 5], None),
    ("U6", "SVX", "EVN", ["12:35"], [0, 2, 4, 6], None),
]

# One-stop itineraries: (carrier, from_hub, via_hub, to_hub, local departure time, weekdays or None).
CONNECTIONS = [
    ("SU", "SVX", "SVO", "KGD", "06:15", None),
    ("SU", "SVX", "SVO", "KZN", "06:15", None),
    ("SU", "LED", "SVO", "SSH", "06:00", None),
    ("SU", "SVX", "SVO", "AYT", "06:15", None),
    ("SU", "SVX", "SVO", "SSH", "06:15", None),
    ("TK", "LED", "IST", "AYT", "09:30", None),
    ("TK", "SVX", "IST", "AYT", "06:50", None),
    ("SU", "SVX", "SVO", "EVN", "06:15", None),
]

# --------------------------------------------------------------------------- trains (synthetic)

# (train_no, name, from_station, to_station, depart "HH:MM" local, duration_min, weekdays or None,
#  car classes, rail_km). Reverse direction is listed explicitly: real trains have their own numbers.
TRAINS = [
    ("002Й", "Премиум", "st_mow_kaz", "st_kzn", "20:20", 683, None, ("kupe", "sv"), 820),
    ("054Г", None, "st_mow_kaz", "st_kzn", "22:45", 733, None, ("platzkart", "kupe", "sv"), 820),
    ("752Г", "Ласточка", "st_mow_kaz", "st_kzn", "07:05", 735, [4, 5, 6], ("sitting",), 820),
    ("001Й", "Премиум", "st_kzn", "st_mow_kaz", "19:45", 683, None, ("kupe", "sv"), 820),
    ("053Г", None, "st_kzn", "st_mow_kaz", "21:30", 730, None, ("platzkart", "kupe", "sv"), 820),
    ("751Г", "Ласточка", "st_kzn", "st_mow_kaz", "06:40", 735, [0, 5, 6], ("sitting",), 820),
    ("104В", None, "st_mow_kaz", "st_aer_adler", "00:40", 1624, None, ("platzkart", "kupe"), 1950),
    ("102М", None, "st_mow_kaz", "st_aer_sochi", "12:14", 1528, None, ("platzkart", "kupe", "sv"), 1920),
    ("028С", "Сочи", "st_mow_kaz", "st_aer_adler", "18:45", 1465, [4, 6], ("kupe", "sv"), 1950),
    ("103В", None, "st_aer_adler", "st_mow_kaz", "15:20", 1630, None, ("platzkart", "kupe"), 1950),
    ("101М", None, "st_aer_sochi", "st_mow_kaz", "09:50", 1535, None, ("platzkart", "kupe", "sv"), 1920),
    ("027С", "Сочи", "st_aer_adler", "st_mow_kaz", "20:10", 1465, [0, 5], ("kupe", "sv"), 1950),
    ("752А", "Сапсан", "st_led_msk", "st_mow_len", "06:00", 238, None, ("sitting",), 650),
    ("758А", "Сапсан", "st_led_msk", "st_mow_len", "19:00", 235, None, ("sitting",), 650),
    ("002А", "Красная стрела", "st_led_msk", "st_mow_len", "23:55", 480, None, ("kupe", "sv"), 650),
    ("751А", "Сапсан", "st_mow_len", "st_led_msk", "05:50", 238, None, ("sitting",), 650),
    ("757А", "Сапсан", "st_mow_len", "st_led_msk", "19:10", 235, None, ("sitting",), 650),
    ("001А", "Красная стрела", "st_mow_len", "st_led_msk", "23:55", 480, None, ("kupe", "sv"), 650),
    ("380Е", None, "st_svx", "st_kzn", "17:50", 845, None, ("platzkart", "kupe"), 1000),
    ("092Е", None, "st_svx", "st_kzn", "06:30", 835, [1, 3, 5], ("platzkart", "kupe", "sv"), 1000),
    ("379Е", None, "st_kzn", "st_svx", "19:10", 855, None, ("platzkart", "kupe"), 1000),
    ("091Е", None, "st_kzn", "st_svx", "08:15", 840, [2, 4, 6], ("platzkart", "kupe", "sv"), 1000),
]

# --------------------------------------------------------------------------- transfers (synthetic)

# Per-country taxi tariff: (base_rub, per_km_rub). Minivan = 1.6x, shuttle/public are per person.
TAXI_TARIFF = {"RU": (300, 35), "TR": (450, 60), "EG": (600, 35), "AE": (600, 90), "AM": (250, 25)}
SHUTTLE_RUB = {"AER": 650, "IST": 900, "AYT": 950, "SSH": 1100, "DXB": 1500}
PUBLIC_RUB = {"AER": 120, "KGD": 90, "KZN": 110, "IST": 180, "AYT": 160, "DXB": 190, "EVN": 100}
# Destinations where no transfer takes animals (edge case "no_pets_transfer").
NO_PET_TRANSFER_CITIES = {"SSH", "DXB"}
