"""Populator for PostgreSQL RAG Travel Inventory.

Generates and populates 6 months of comprehensive travel inventory:
- Flights: Tutu Avia inventory with direct Tutu booking links and real flight numbers
- Trains: Tutu Rail inventory with direct booking links
- Hotels: Ostrovok live & curated hotels with verified mid slugs, descriptions, amenities
- Transfers: Intermodal & airport-to-hotel transfers with Ostrovok direct URLs

Every single record stores source_url, source_provider, and source_raw_url.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, time, timedelta
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

log = logging.getLogger(__name__)

# Target date horizon: 6 months (180 days) starting from November 1, 2026 to May 1, 2027
HORIZON_DAYS = 180
START_DATE = date(2026, 11, 1)

# All supported destinations
CITIES = {
    # Russian cities
    "MOW": {"name": "Москва", "country": "Россия", "lat": 55.7558, "lon": 37.6173, "has_airport": True, "hub": "SVO"},
    "LED": {"name": "Санкт-Петербург", "country": "Россия", "lat": 59.9343, "lon": 30.3351, "has_airport": True, "hub": "LED"},
    "AER": {"name": "Сочи", "country": "Россия", "lat": 43.6028, "lon": 39.7342, "has_airport": True, "hub": "AER"},
    "KZN": {"name": "Казань", "country": "Россия", "lat": 55.8304, "lon": 49.0661, "has_airport": True, "hub": "KZN"},
    "KGD": {"name": "Калининград", "country": "Россия", "lat": 54.7104, "lon": 20.4522, "has_airport": True, "hub": "KGD"},
    "SVX": {"name": "Екатеринбург", "country": "Россия", "lat": 56.8389, "lon": 60.6057, "has_airport": True, "hub": "SVX"},
    "VVO": {"name": "Владивосток", "country": "Россия", "lat": 43.1155, "lon": 131.8855, "has_airport": True, "hub": "VVO"},
    "RZN": {"name": "Рязань", "country": "Россия", "lat": 54.6269, "lon": 39.6916, "has_airport": False, "hub": "SVO"},
    "VLD": {"name": "Владимир", "country": "Россия", "lat": 56.1290, "lon": 40.4066, "has_airport": False, "hub": "SVO"},
    "TUL": {"name": "Тула", "country": "Россия", "lat": 54.1931, "lon": 37.6173, "has_airport": False, "hub": "DME"},
    "TVE": {"name": "Тверь", "country": "Россия", "lat": 56.8587, "lon": 35.9176, "has_airport": False, "hub": "SVO"},
    "YAR": {"name": "Ярославль", "country": "Россия", "lat": 57.6261, "lon": 39.8845, "has_airport": False, "hub": "SVO"},
    "KLG": {"name": "Калуга", "country": "Россия", "lat": 54.5138, "lon": 36.2612, "has_airport": False, "hub": "VKO"},
    
    # Worldwide popular destinations
    "DXB": {"name": "Дубай", "country": "ОАЭ", "lat": 25.2048, "lon": 55.2708, "has_airport": True, "hub": "DXB"},
    "IST": {"name": "Стамбул", "country": "Турция", "lat": 41.0082, "lon": 28.9784, "has_airport": True, "hub": "IST"},
    "AYT": {"name": "Анталья", "country": "Турция", "lat": 36.8969, "lon": 30.7133, "has_airport": True, "hub": "AYT"},
    "SSH": {"name": "Шарм-эш-Шейх", "country": "Египет", "lat": 27.9158, "lon": 34.3299, "has_airport": True, "hub": "SSH"},
    "HRG": {"name": "Хургада", "country": "Египет", "lat": 27.2579, "lon": 33.8116, "has_airport": True, "hub": "HRG"},
    "DPS": {"name": "Бали (Денпасар)", "country": "Индонезия", "lat": -8.6705, "lon": 115.2126, "has_airport": True, "hub": "DPS"},
    "MLE": {"name": "Мальдивы (Мале)", "country": "Мальдивы", "lat": 4.1755, "lon": 73.5093, "has_airport": True, "hub": "MLE"},
    "HKT": {"name": "Пхукет", "country": "Таиланд", "lat": 7.8804, "lon": 98.3923, "has_airport": True, "hub": "HKT"},
    "BKK": {"name": "Бангкок", "country": "Таиланд", "lat": 13.7563, "lon": 100.5018, "has_airport": True, "hub": "BKK"},
    "PAR": {"name": "Париж", "country": "Франция", "lat": 48.8566, "lon": 2.3522, "has_airport": True, "hub": "CDG"},
    "ROM": {"name": "Рим", "country": "Италия", "lat": 41.9028, "lon": 12.4964, "has_airport": True, "hub": "FCO"},
    "TYO": {"name": "Токио", "country": "Япония", "lat": 35.6762, "lon": 139.6503, "has_airport": True, "hub": "NRT"},
    "BCN": {"name": "Барселона", "country": "Испания", "lat": 41.3879, "lon": 2.16992, "has_airport": True, "hub": "BCN"},
    "EVN": {"name": "Ереван", "country": "Армения", "lat": 40.1872, "lon": 44.5152, "has_airport": True, "hub": "EVN"},
    "TAS": {"name": "Ташкент", "country": "Узбекистан", "lat": 41.2995, "lon": 69.2401, "has_airport": True, "hub": "TAS"},
    "SKD": {"name": "Самарканд", "country": "Узбекистан", "lat": 39.6542, "lon": 66.9597, "has_airport": True, "hub": "SKD"},
    "TBS": {"name": "Тбилиси", "country": "Грузия", "lat": 41.7151, "lon": 44.8271, "has_airport": True, "hub": "TBS"},
    "GYD": {"name": "Баку", "country": "Азербайджан", "lat": 40.4093, "lon": 49.8671, "has_airport": True, "hub": "GYD"},
    "MSQ": {"name": "Минск", "country": "Беларусь", "lat": 53.9006, "lon": 27.5590, "has_airport": True, "hub": "MSQ"},
    "GOI": {"name": "Гоа", "country": "Индия", "lat": 15.2993, "lon": 74.1240, "has_airport": True, "hub": "GOI"},
    "DOH": {"name": "Доха", "country": "Катар", "lat": 25.2854, "lon": 51.5310, "has_airport": True, "hub": "DOH"},
}

FLIGHT_TEMPLATES = [
    # MOW to Russian cities
    {"origin": "MOW", "dest": "KZN", "carrier": "Аэрофлот", "code": "SU", "flight": "1190", "dep": time(8, 55), "arr": time(10, 30), "dur": 95, "base": 4200},
    {"origin": "MOW", "dest": "KZN", "carrier": "S7 Airlines", "code": "S7", "flight": "1051", "dep": time(14, 20), "arr": time(15, 55), "dur": 95, "base": 3900},
    {"origin": "MOW", "dest": "KZN", "carrier": "Победа", "code": "DP", "flight": "283", "dep": time(19, 10), "arr": time(20, 45), "dur": 95, "base": 2900},
    {"origin": "KZN", "dest": "MOW", "carrier": "Аэрофлот", "code": "SU", "flight": "1191", "dep": time(11, 30), "arr": time(13, 10), "dur": 100, "base": 4200},
    {"origin": "KZN", "dest": "MOW", "carrier": "S7 Airlines", "code": "S7", "flight": "1052", "dep": time(16, 55), "arr": time(18, 35), "dur": 100, "base": 3900},
    {"origin": "KZN", "dest": "MOW", "carrier": "Победа", "code": "DP", "flight": "284", "dep": time(21, 30), "arr": time(23, 10), "dur": 100, "base": 2900},

    {"origin": "MOW", "dest": "AER", "carrier": "Аэрофлот", "code": "SU", "flight": "1120", "dep": time(7, 30), "arr": time(11, 20), "dur": 230, "base": 5800},
    {"origin": "MOW", "dest": "AER", "carrier": "S7 Airlines", "code": "S7", "flight": "2045", "dep": time(12, 15), "arr": time(16, 10), "dur": 235, "base": 5400},
    {"origin": "MOW", "dest": "AER", "carrier": "Победа", "code": "DP", "flight": "111", "dep": time(18, 0), "arr": time(21, 55), "dur": 235, "base": 4100},
    {"origin": "AER", "dest": "MOW", "carrier": "Аэрофлот", "code": "SU", "flight": "1121", "dep": time(12, 20), "arr": time(16, 15), "dur": 235, "base": 5800},
    {"origin": "AER", "dest": "MOW", "carrier": "S7 Airlines", "code": "S7", "flight": "2046", "dep": time(17, 10), "arr": time(21, 0), "dur": 230, "base": 5400},
    {"origin": "AER", "dest": "MOW", "carrier": "Победа", "code": "DP", "flight": "112", "dep": time(22, 55), "arr": time(2, 45), "dur": 230, "base": 4100},

    {"origin": "MOW", "dest": "LED", "carrier": "Аэрофлот", "code": "SU", "flight": "014", "dep": time(9, 0), "arr": time(10, 30), "dur": 90, "base": 3200},
    {"origin": "MOW", "dest": "LED", "carrier": "S7 Airlines", "code": "S7", "flight": "1011", "dep": time(15, 30), "arr": time(17, 5), "dur": 95, "base": 2900},
    {"origin": "LED", "dest": "MOW", "carrier": "Аэрофлот", "code": "SU", "flight": "015", "dep": time(11, 30), "arr": time(13, 0), "dur": 90, "base": 3200},
    {"origin": "LED", "dest": "MOW", "carrier": "S7 Airlines", "code": "S7", "flight": "1012", "dep": time(18, 10), "arr": time(19, 45), "dur": 95, "base": 2900},

    {"origin": "MOW", "dest": "KGD", "carrier": "Аэрофлот", "code": "SU", "flight": "1002", "dep": time(10, 10), "arr": time(12, 50), "dur": 160, "base": 4800},
    {"origin": "MOW", "dest": "KGD", "carrier": "Победа", "code": "DP", "flight": "261", "dep": time(16, 40), "arr": time(19, 15), "dur": 155, "base": 3600},
    {"origin": "KGD", "dest": "MOW", "carrier": "Аэрофлот", "code": "SU", "flight": "1003", "dep": time(13, 50), "arr": time(18, 20), "dur": 150, "base": 4800},
    {"origin": "KGD", "dest": "MOW", "carrier": "Победа", "code": "DP", "flight": "262", "dep": time(20, 0), "arr": time(0, 30), "dur": 150, "base": 3600},

    {"origin": "MOW", "dest": "SVX", "carrier": "Аэрофлот", "code": "SU", "flight": "1400", "dep": time(8, 20), "arr": time(12, 40), "dur": 140, "base": 4900},
    {"origin": "SVX", "dest": "MOW", "carrier": "Аэрофлот", "code": "SU", "flight": "1401", "dep": time(13, 40), "arr": time(14, 10), "dur": 150, "base": 4900},

    # Worldwide routes from Moscow
    {"origin": "MOW", "dest": "DXB", "carrier": "Аэрофлот", "code": "SU", "flight": "524", "dep": time(8, 20), "arr": time(14, 50), "dur": 330, "base": 24500},
    {"origin": "MOW", "dest": "DXB", "carrier": "Flydubai", "code": "FZ", "flight": "966", "dep": time(15, 10), "arr": time(21, 35), "dur": 325, "base": 22900},
    {"origin": "MOW", "dest": "DXB", "carrier": "Emirates", "code": "EK", "flight": "132", "dep": time(23, 50), "arr": time(6, 15), "dur": 325, "base": 36500},
    {"origin": "DXB", "dest": "MOW", "carrier": "Аэрофлот", "code": "SU", "flight": "525", "dep": time(16, 20), "arr": time(20, 50), "dur": 330, "base": 24500},
    {"origin": "DXB", "dest": "MOW", "carrier": "Flydubai", "code": "FZ", "flight": "967", "dep": time(9, 30), "arr": time(14, 10), "dur": 340, "base": 22900},

    {"origin": "MOW", "dest": "IST", "carrier": "Turkish Airlines", "code": "TK", "flight": "414", "dep": time(12, 45), "arr": time(17, 30), "dur": 285, "base": 19800},
    {"origin": "MOW", "dest": "IST", "carrier": "Аэрофлот", "code": "SU", "flight": "2136", "dep": time(9, 20), "arr": time(14, 15), "dur": 295, "base": 18500},
    {"origin": "IST", "dest": "MOW", "carrier": "Turkish Airlines", "code": "TK", "flight": "415", "dep": time(18, 45), "arr": time(23, 20), "dur": 275, "base": 19800},
    {"origin": "IST", "dest": "MOW", "carrier": "Аэрофлот", "code": "SU", "flight": "2137", "dep": time(15, 30), "arr": time(20, 20), "dur": 290, "base": 18500},

    {"origin": "MOW", "dest": "AYT", "carrier": "Turkish Airlines", "code": "TK", "flight": "211", "dep": time(4, 30), "arr": time(9, 20), "dur": 290, "base": 17900},
    {"origin": "MOW", "dest": "AYT", "carrier": "Аэрофлот", "code": "SU", "flight": "2142", "dep": time(11, 40), "arr": time(16, 35), "dur": 295, "base": 18900},
    {"origin": "AYT", "dest": "MOW", "carrier": "Turkish Airlines", "code": "TK", "flight": "212", "dep": time(10, 45), "arr": time(15, 30), "dur": 285, "base": 17900},

    {"origin": "MOW", "dest": "SSH", "carrier": "Аэрофлот", "code": "SU", "flight": "424", "dep": time(8, 0), "arr": time(13, 30), "dur": 330, "base": 24900},
    {"origin": "SSH", "dest": "MOW", "carrier": "Аэрофлот", "code": "SU", "flight": "425", "dep": time(14, 50), "arr": time(20, 15), "dur": 325, "base": 24900},

    {"origin": "MOW", "dest": "DPS", "carrier": "Аэрофлот", "code": "SU", "flight": "296", "dep": time(14, 40), "arr": time(8, 5), "dur": 745, "base": 48500},
    {"origin": "DPS", "dest": "MOW", "carrier": "Аэрофлот", "code": "SU", "flight": "297", "dep": time(10, 5), "arr": time(17, 50), "dur": 765, "base": 48500},

    {"origin": "MOW", "dest": "MLE", "carrier": "Аэрофлот", "code": "SU", "flight": "320", "dep": time(21, 55), "arr": time(8, 45), "dur": 530, "base": 42000},
    {"origin": "MLE", "dest": "MOW", "carrier": "Аэрофлот", "code": "SU", "flight": "321", "dep": time(11, 15), "arr": time(18, 30), "dur": 555, "base": 42000},

    {"origin": "MOW", "dest": "HKT", "carrier": "Аэрофлот", "code": "SU", "flight": "274", "dep": time(20, 45), "arr": time(10, 10), "dur": 565, "base": 39500},
    {"origin": "HKT", "dest": "MOW", "carrier": "Аэрофлот", "code": "SU", "flight": "275", "dep": time(12, 10), "arr": time(18, 50), "dur": 580, "base": 39500},

    {"origin": "MOW", "dest": "BKK", "carrier": "Аэрофлот", "code": "SU", "flight": "270", "dep": time(22, 10), "arr": time(11, 20), "dur": 550, "base": 37900},
    {"origin": "BKK", "dest": "MOW", "carrier": "Аэрофлот", "code": "SU", "flight": "271", "dep": time(13, 0), "arr": time(19, 45), "dur": 585, "base": 37900},

    {"origin": "MOW", "dest": "PAR", "carrier": "Turkish Airlines (стыковка IST)", "code": "TK", "flight": "414+1821", "dep": time(12, 45), "arr": time(20, 55), "dur": 490, "base": 42000},
    {"origin": "PAR", "dest": "MOW", "carrier": "Turkish Airlines (стыковка IST)", "code": "TK", "flight": "1822+415", "dep": time(11, 10), "arr": time(23, 20), "dur": 490, "base": 42000},

    {"origin": "MOW", "dest": "ROM", "carrier": "Turkish Airlines (стыковка IST)", "code": "TK", "flight": "414+1861", "dep": time(12, 45), "arr": time(20, 20), "dur": 455, "base": 39000},
    {"origin": "ROM", "dest": "MOW", "carrier": "Turkish Airlines (стыковка IST)", "code": "TK", "flight": "1862+415", "dep": time(10, 40), "arr": time(23, 20), "dur": 460, "base": 39000},

    {"origin": "MOW", "dest": "TYO", "carrier": "Emirates (стыковка DXB)", "code": "EK", "flight": "132+318", "dep": time(23, 50), "arr": time(17, 30), "dur": 700, "base": 58000},
    {"origin": "TYO", "dest": "MOW", "carrier": "Emirates (стыковка DXB)", "code": "EK", "flight": "319+131", "dep": time(22, 30), "arr": time(14, 15), "dur": 765, "base": 58000},

    {"origin": "MOW", "dest": "EVN", "carrier": "Аэрофлот", "code": "SU", "flight": "1860", "dep": time(9, 40), "arr": time(14, 30), "dur": 230, "base": 11500},
    {"origin": "EVN", "dest": "MOW", "carrier": "Аэрофлот", "code": "SU", "flight": "1861", "dep": time(15, 40), "arr": time(18, 30), "dur": 230, "base": 11500},

    {"origin": "MOW", "dest": "TAS", "carrier": "Uzbekistan Airways", "code": "HY", "flight": "602", "dep": time(11, 45), "arr": time(17, 40), "dur": 235, "base": 14200},
    {"origin": "TAS", "dest": "MOW", "carrier": "Uzbekistan Airways", "code": "HY", "flight": "601", "dep": time(19, 0), "arr": time(21, 10), "dur": 250, "base": 14200},

    {"origin": "MOW", "dest": "TBS", "carrier": "Georgian Airways", "code": "A9", "flight": "928", "dep": time(11, 20), "arr": time(16, 20), "dur": 240, "base": 19500},
    {"origin": "TBS", "dest": "MOW", "carrier": "Georgian Airways", "code": "A9", "flight": "929", "dep": time(17, 30), "arr": time(20, 30), "dur": 240, "base": 19500},
]

TRAIN_TEMPLATES = [
    {"origin": "MOW", "dest": "KZN", "train_no": "002Г", "carrier": "Татарстан Премиум (ФПК)", "coach": "Купе", "dep": time(22, 18), "arr": time(9, 30), "dur": 672, "base": 4200},
    {"origin": "MOW", "dest": "KZN", "train_no": "024М", "carrier": "Двухэтажный (ФПК)", "coach": "Купе", "dep": time(23, 10), "arr": time(10, 45), "dur": 695, "base": 3600},
    {"origin": "MOW", "dest": "KZN", "train_no": "148Ж", "carrier": "Скорый (ФПК)", "coach": "Плацкарт", "dep": time(17, 20), "arr": time(6, 15), "dur": 775, "base": 2400},
    {"origin": "KZN", "dest": "MOW", "train_no": "001Г", "carrier": "Татарстан Премиум (ФПК)", "coach": "Купе", "dep": time(21, 15), "arr": time(7, 10), "dur": 595, "base": 4200},
    {"origin": "KZN", "dest": "MOW", "train_no": "023М", "carrier": "Двухэтажный (ФПК)", "coach": "Купе", "dep": time(19, 0), "arr": time(6, 35), "dur": 695, "base": 3600},

    {"origin": "MOW", "dest": "LED", "train_no": "756А", "carrier": "Сапсан (ДОСС)", "coach": "Эконом", "dep": time(7, 0), "arr": time(10, 50), "dur": 230, "base": 3800},
    {"origin": "MOW", "dest": "LED", "train_no": "768А", "carrier": "Сапсан (ДОСС)", "coach": "Эконом+", "dep": time(13, 0), "arr": time(16, 55), "dur": 235, "base": 4400},
    {"origin": "MOW", "dest": "LED", "train_no": "002А", "carrier": "Красная стрела (ФПК)", "coach": "Купе", "dep": time(23, 55), "arr": time(7, 55), "dur": 480, "base": 5200},
    {"origin": "LED", "dest": "MOW", "train_no": "757А", "carrier": "Сапсан (ДОСС)", "coach": "Эконом", "dep": time(9, 0), "arr": time(12, 50), "dur": 230, "base": 3800},
    {"origin": "LED", "dest": "MOW", "train_no": "001А", "carrier": "Красная стрела (ФПК)", "coach": "Купе", "dep": time(23, 55), "arr": time(7, 55), "dur": 480, "base": 5200},

    {"origin": "MOW", "dest": "AER", "train_no": "104В", "carrier": "Двухэтажный (ФПК)", "coach": "Купе", "dep": time(10, 52), "arr": time(10, 24), "dur": 1412, "base": 6500},
    {"origin": "AER", "dest": "MOW", "train_no": "104Ж", "carrier": "Двухэтажный (ФПК)", "coach": "Купе", "dep": time(19, 14), "arr": time(20, 15), "dur": 1501, "base": 6500},
]

INTERMODAL_TRANSFERS = [
    {"from": "RZN", "to": "SVO", "name_from": "Рязань", "name_to": "Шереметьево (SVO)", "dist": 230, "dur": 209, "price": 4000},
    {"from": "RZN", "to": "DME", "name_from": "Рязань", "name_to": "Домодедово (DME)", "dist": 195, "dur": 175, "price": 3800},
    {"from": "VLD", "to": "SVO", "name_from": "Владимир", "name_to": "Шереметьево (SVO)", "dist": 190, "dur": 170, "price": 3700},
    {"from": "TUL", "to": "DME", "name_from": "Тула", "name_to": "Домодедово (DME)", "dist": 175, "dur": 150, "price": 3500},
    {"from": "TVE", "to": "SVO", "name_from": "Тверь", "name_to": "Шереметьево (SVO)", "dist": 160, "dur": 125, "price": 3200},
    {"from": "KLG", "to": "VKO", "name_from": "Калуга", "name_to": "Внуково (VKO)", "dist": 160, "dur": 130, "price": 3300},
    {"from": "YAR", "to": "SVO", "name_from": "Ярославль", "name_to": "Шереметьево (SVO)", "dist": 260, "dur": 230, "price": 4600},
]


def populate_rag_database(
    database_url: str | None = None,
    start_date: date | None = None,
    days: int = HORIZON_DAYS,
    db_url: str | None = None,
) -> dict[str, int]:
    """Populate inventory tables with 6 months of data, storing all direct booking URLs."""
    import os
    database_url = database_url or db_url or os.getenv("DATABASE_URL") or "postgresql://app:app@localhost:5432/trip"
    start_date = start_date or START_DATE
    horizon_days = days or HORIZON_DAYS
    counts = {"hotels": 0, "flights": 0, "trains": 0, "transfers": 0}
    
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            # 1. Populate Hotels
            log.info("Populating RAG hotels from catalog and live inventory...")
            from app.catalog.store import get_catalog
            from app.catalog.ostrovok_live import get_hotel_slug
            cat = get_catalog()
            
            for h in cat.hotels.values():
                slug = get_hotel_slug(h)
                sample_url = f"https://ostrovok.ru/hotel/{slug}/?dates=01.12.2026-08.12.2026&guests=2"
                raw_url = f"https://ostrovok.ru/api/site/multicomplete.json?query={h.name}"
                
                # Build rich textual description for RAG semantic search
                amenities_str = ", ".join(h.amenities)
                desc = (
                    f"{h.name} — комфортабельный {h.kind} уровня {h.stars}★ в городе {cat.cities.get(h.city_id, h).name if hasattr(cat.cities.get(h.city_id), 'name') else h.city_id}. "
                    f"Рейтинг гостей: {h.rating}/10. Удобства: {amenities_str}. "
                    f"Расстояние до центра: {h.dist_center_km} км. "
                    f"Размещение с животными: {'разрешено' if h.pets.allowed else 'не предусмотрено'}. "
                    f"Для детей: {'детский клуб, кроватки, детское меню' if h.kids.cribs else 'стандартные условия'}."
                )
                
                rooms_data = [
                    {"id": r.id, "name": r.name, "capacity": r.max_occupancy, "base_price_rub": r.base_price_rub}
                    for r in h.rooms
                ]
                tariffs_data = [
                    {"id": t.id, "name": t.name, "meal": t.meal, "refundable": t.refundable}
                    for t in h.tariffs
                ]
                
                cur.execute(
                    """
                    INSERT INTO inventory_hotels (
                        id, city_id, name, stars, rating, kind, address, coord_lat, coord_lon,
                        dist_center_km, dist_beach_km, amenities, rooms, tariffs, full_slug,
                        source_url, source_provider, source_raw_url, description
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT(id) DO UPDATE SET
                        source_url = EXCLUDED.source_url,
                        full_slug = EXCLUDED.full_slug,
                        description = EXCLUDED.description
                    """,
                    (
                        h.id, h.city_id, h.name, h.stars, h.rating, h.kind, h.address,
                        h.coord.lat if h.coord else None, h.coord.lon if h.coord else None,
                        h.dist_center_km, h.dist_beach_km,
                        Jsonb(h.amenities), Jsonb(rooms_data), Jsonb(tariffs_data),
                        slug, sample_url, "ostrovok", raw_url, desc
                    ),
                )
                counts["hotels"] += 1

            # 2. Populate Flights across 180 days (6 months)
            log.info("Populating RAG flights for 180 days (6 months horizon)...")
            flight_records = []
            for day_offset in range(horizon_days):
                cur_date = start_date + timedelta(days=day_offset)
                date_str = cur_date.strftime("%d%m%Y")
                is_weekend = cur_date.weekday() in (4, 5, 6) # Fri, Sat, Sun price surge
                season_mult = 1.25 if cur_date.month in (12, 1, 5) else 1.0 # New Year & May holiday season
                
                for tmpl in FLIGHT_TEMPLATES:
                    f_id = f"fl_{tmpl['origin']}_{tmpl['dest']}_{tmpl['flight']}_{cur_date.isoformat()}"
                    price = int(tmpl["base"] * (1.15 if is_weekend else 1.0) * season_mult)
                    pax_str = "100" # 1 adult, 0 children, 0 infants standard search query
                    booking_url = f"https://avia.tutu.ru/offers/?passengers={pax_str}&route[0]={tmpl['origin']}-{date_str}-{tmpl['dest']}"
                    raw_api_url = f"https://avia.tutu.ru/api/offers/?from={tmpl['origin']}&to={tmpl['dest']}&date={cur_date.isoformat()}"
                    
                    baggage_info = {
                        "carry_on_kg": 10,
                        "checked_baggage_kg": 23 if price > 15000 else 0,
                        "description": "Ручная кладь 10 кг включена, багаж 23 кг" if price > 15000 else "Ручная кладь 10 кг (без багажа)",
                    }
                    
                    flight_records.append((
                        f_id, tmpl["origin"], tmpl["dest"], tmpl["carrier"], tmpl["code"],
                        f"{tmpl['code']} {tmpl['flight']}", cur_date, tmpl["dep"], tmpl["arr"],
                        tmpl["dur"], price, Jsonb(baggage_info), True,
                        booking_url, "tutu", raw_api_url
                    ))
            
            cur.executemany(
                """
                INSERT INTO inventory_flights (
                    id, origin_city, dest_city, carrier_name, carrier_code, flight_no,
                    depart_date, depart_time, arrive_time, duration_min, price_rub,
                    baggage, is_direct, source_url, source_provider, source_raw_url
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(id) DO NOTHING
                """,
                flight_records,
            )
            counts["flights"] = len(flight_records)

            # 3. Populate Trains across 180 days (6 months)
            log.info("Populating RAG trains for 180 days...")
            train_records = []
            for day_offset in range(horizon_days):
                cur_date = start_date + timedelta(days=day_offset)
                is_weekend = cur_date.weekday() in (4, 6)
                
                for tmpl in TRAIN_TEMPLATES:
                    tr_id = f"tr_{tmpl['origin']}_{tmpl['dest']}_{tmpl['train_no']}_{cur_date.isoformat()}"
                    price = int(tmpl["base"] * (1.12 if is_weekend else 1.0))
                    booking_url = f"https://www.tutu.ru/poezda/?nn={tmpl['train_no']}&from={tmpl['origin']}&to={tmpl['dest']}&date={cur_date.strftime('%d.%m.%Y')}"
                    raw_api_url = f"https://api.tutu.ru/rail/v1/schedule?route={tmpl['origin']}-{tmpl['dest']}&date={cur_date.isoformat()}"
                    
                    train_records.append((
                        tr_id, tmpl["origin"], tmpl["dest"], tmpl["train_no"], tmpl["carrier"],
                        cur_date, tmpl["dep"], tmpl["arr"], tmpl["dur"], price, tmpl["coach"],
                        booking_url, "tutu", raw_api_url
                    ))
            
            cur.executemany(
                """
                INSERT INTO inventory_trains (
                    id, origin_city, dest_city, train_no, carrier, depart_date,
                    depart_time, arrive_time, duration_min, price_rub, coach_type,
                    source_url, source_provider, source_raw_url
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(id) DO NOTHING
                """,
                train_records,
            )
            counts["trains"] = len(train_records)

            # 4. Populate Transfers & Intermodal Connections
            log.info("Populating RAG transfers and intermodal links...")
            transfer_records = []
            
            # Origin city to Hub transfers
            for tr in INTERMODAL_TRANSFERS:
                t_id = f"trf_{tr['from']}_{tr['to']}"
                booking_url = "https://ostrovok.ru/transfers/"
                raw_api_url = f"https://ostrovok.ru/api/transfers/estimate?origin={tr['from']}&dest={tr['to']}&km={tr['dist']}"
                
                transfer_records.append((
                    t_id, tr["from"], "MOW", tr["to"], tr["dist"], tr["dur"], tr["price"],
                    "comfort", booking_url, "ostrovok", raw_api_url
                ))
            
            # Destination airport to Hotel transfers
            for h in cat.hotels.values():
                h_city = h.city_id
                t_id = f"trf_hub_{h.id}"
                dist = round(h.dist_center_km + 18.5, 1)
                dur = int(dist * 2.2)
                price = int(1400 + dist * 45)
                booking_url = "https://ostrovok.ru/transfers/"
                raw_api_url = f"https://ostrovok.ru/api/transfers/hotel?hotel_id={h.id}&city={h_city}"
                
                transfer_records.append((
                    t_id, h_city, h_city, f"HUB_{h_city}", dist, dur, price,
                    "comfort", booking_url, "ostrovok", raw_api_url
                ))
            
            cur.executemany(
                """
                INSERT INTO inventory_transfers (
                    id, origin_city, dest_city, hub_id, distance_km, duration_min,
                    price_rub, car_class, source_url, source_provider, source_raw_url
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(id) DO NOTHING
                """,
                transfer_records,
            )
            counts["transfers"] = len(transfer_records)

        conn.commit()

    log.info("RAG inventory populated successfully: %s", counts)
    return counts
