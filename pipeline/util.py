"""Deterministic helpers shared by the generators."""

from __future__ import annotations

import json
import math
import random
from collections.abc import Iterator
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline import config

SNAP_DIR = Path(__file__).parent / "snapshots"


def rng(*key: object) -> random.Random:
    """Independent stream per entity: adding a hotel never reshuffles flights."""
    return random.Random(f"{config.RANDOM_SEED}:" + ":".join(map(str, key)))


def snapshot(name: str):
    return json.loads((SNAP_DIR / name).read_text(encoding="utf-8"))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def local_dt(day: date, hhmm: str, tz: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime.combine(day, time(h, m), ZoneInfo(tz))


def days(start: date, end: date) -> Iterator[date]:
    d = start
    while d < end:
        yield d
        d += timedelta(days=1)


def round_to(x: float, step: int) -> int:
    return int(round(x / step) * step)


def round_minutes(x: float, step: int = 5) -> int:
    return max(step, round_to(x, step))


def demand_factor(day: date) -> float:
    """Transport price seasonality: November is low, New Year holidays are peak."""
    if (day.month == 12 and day.day >= 27) or (day.month == 1 and day.day <= 8):
        return 1.55
    base = {10: 0.95, 11: 0.88, 12: 1.0, 1: 1.05}.get(day.month, 1.0)
    if day.weekday() in (4, 6):
        base *= 1.08
    return base


def ymd(day: date) -> str:
    return day.strftime("%Y%m%d")


TRANSLIT = str.maketrans({"А": "A", "В": "V", "Г": "G", "Е": "E", "Й": "J", "М": "M", "С": "S", "Ж": "Z", "Ч": "C"})
