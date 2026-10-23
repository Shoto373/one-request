"""Synthetic dated trains on real stations (schedule in ``config.TRAINS``)."""

from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

from pipeline import config
from pipeline.util import TRANSLIT, days, demand_factor, local_dt, rng, round_to, ymd

GEN_REF = f"gen:trains@seed{config.RANDOM_SEED}"

# ₽ per km of rail distance; high-speed trains are priced separately.
RATE = {"sitting": 2.9, "platzkart": 1.7, "kupe": 3.1, "sv": 6.2}
CLASS_NAME = {"sitting": "Сидячий", "platzkart": "Плацкарт", "kupe": "Купе", "sv": "СВ"}


def _car(cls: str, name: str | None, km: int, day, key: str) -> dict:
    r = rng("car", key, cls)
    rate = RATE[cls] * (1.9 if name == "Сапсан" else 1.0) * (1.15 if name else 1.0)
    price = round_to(km * rate * demand_factor(day) * r.uniform(0.9, 1.15), 10)
    if name == "Ласточка":
        pets, fee = False, 0
    elif cls == "platzkart":
        pets, fee = True, 1100
    elif cls in ("kupe", "sv"):
        pets, fee = True, 1500
    else:
        pets, fee = True, 1000
    return {
        "car_class": cls,
        "name": CLASS_NAME[cls],
        "price_rub": price,
        "seats_left": r.choice([0, 2, 4, 8, 12, 20, 35]) if cls != "sv" else r.choice([0, 1, 2, 4, 6]),
        "pets_allowed": pets,
        "pet_fee_rub": fee,
        "bedding_included": cls in ("kupe", "sv") or name == "Премиум",
        "refund_fee_rub": 300,
    }


def build_trains(hubs: list[dict]) -> list[dict]:
    hub = {h["id"]: h for h in hubs}
    out = []
    for no, name, a, b, t, minutes, weekdays, classes, km in config.TRAINS:
        for day in days(config.WINDOW_START, config.WINDOW_END):
            if weekdays is not None and day.weekday() not in weekdays:
                continue
            dep = local_dt(day, t, hub[a]["timezone"])
            arr = (dep + timedelta(minutes=minutes)).astimezone(ZoneInfo(hub[b]["timezone"]))
            tid = f"tr_{no.translate(TRANSLIT)}_{ymd(day)}"
            out.append(
                {
                    "id": tid,
                    "train_no": no,
                    "name": name,
                    "origin_city": hub[a]["city_id"],
                    "dest_city": hub[b]["city_id"],
                    "from_hub": a,
                    "to_hub": b,
                    "depart": dep.isoformat(),
                    "arrive": arr.isoformat(),
                    "duration_min": minutes,
                    "cars": [_car(c, name, km, day, tid) for c in classes],
                    "child_free_age": 5,
                    "hand_luggage_kg": 36,
                    "synthetic": True,
                    "source_refs": [GEN_REF, *(f"wikidata:{config.STATIONS[s]['wikidata']}" for s in (a, b))],
                }
            )
    return sorted(out, key=lambda x: (x["depart"][:10], x["id"]))
