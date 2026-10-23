"""Transfer routes hub <-> hotel: real geometry, price and time by formula (synthetic)."""

from __future__ import annotations

from pipeline import config
from pipeline.util import haversine_km, rng, round_minutes, round_to

GEN_REF = f"gen:transfers@seed{config.RANDOM_SEED}"
FORMULA_REF = "formula:road_km=1.3*great_circle"

PROVIDER = {"taxi": "Такси «Комфорт»", "minivan": "Минивэн-трансфер", "shuttle": "Групповой трансфер",
            "public": "Общественный транспорт"}


def _hours(mode: str, city_id: str) -> dict:
    if mode in ("taxi", "minivan"):
        return {"round_the_clock": True, "opens": None, "closes": None}
    if mode == "shuttle":
        return {"round_the_clock": False, "opens": "07:00", "closes": "23:30"}
    closes = "00:30" if city_id in ("IST", "DXB") else "23:00"
    return {"round_the_clock": False, "opens": "06:00", "closes": closes}


def build_transfers(cities: list[dict], hubs: list[dict], hotels: list[dict]) -> list[dict]:
    country = {c["id"]: c["country_code"] for c in cities}
    speed = {c["id"]: c.get("road_speed_kmh", 40) for c in config.CITIES}
    hubs_by_city: dict[str, list[dict]] = {}
    for h in hubs:
        hubs_by_city.setdefault(h["city_id"], []).append(h)
    out = []
    for hotel in hotels:
        cid = hotel["city_id"]
        base, per_km = config.TAXI_TARIFF[country[cid]]
        pets_city = cid not in config.NO_PET_TRANSFER_CITIES
        for hub in hubs_by_city[cid]:
            km = round(max(0.5, 1.3 * haversine_km(hub["coord"]["lat"], hub["coord"]["lon"],
                                                  hotel["coord"]["lat"], hotel["coord"]["lon"])), 1)
            taxi_min = round_minutes(8 + km / speed[cid] * 60)
            r = rng("transfer", hub["id"], hotel["id"])
            modes = ["taxi", "minivan"]
            if hub["kind"] == "airport" and cid in config.SHUTTLE_RUB and hotel["kind"] != "guesthouse" and km <= 45:
                modes.append("shuttle")
            if cid in config.PUBLIC_RUB:
                modes.append("public")
            for mode in modes:
                if mode == "taxi":
                    price, unit, pax, bags = round_to(base + per_km * km, 50), "per_vehicle", 4, 3
                    minutes, pets, fee = taxi_min, pets_city and r.random() < 0.6, 400
                elif mode == "minivan":
                    price, unit, pax, bags = round_to(1.6 * (base + per_km * km), 50), "per_vehicle", 7, 7
                    minutes, pets, fee = taxi_min + 5, pets_city, 500
                elif mode == "shuttle":
                    price, unit, pax, bags = round_to(config.SHUTTLE_RUB[cid] * (1 + km / 100), 50), "per_person", 40, 40
                    minutes, pets, fee = round_minutes(taxi_min * 1.5 + 20), False, 0
                else:
                    price, unit, pax, bags = config.PUBLIC_RUB[cid], "per_person", 30, 20
                    minutes, pets, fee = round_minutes(taxi_min * 1.8 + 20), pets_city, 0
                out.append(
                    {
                        "id": f"trf_{hub['id']}_{hotel['id']}_{mode}",
                        "city_id": cid,
                        "hub_id": hub["id"],
                        "hotel_id": hotel["id"],
                        "mode": mode,
                        "provider": PROVIDER[mode],
                        "distance_km": km,
                        "duration_min": minutes,
                        "price_rub": price,
                        "price_unit": unit,
                        "capacity_pax": pax,
                        "capacity_bags": bags,
                        "pets_allowed": pets,
                        "pet_fee_rub": fee if pets else 0,
                        "hours": _hours(mode, cid),
                        "child_seat": mode == "minivan" or (mode == "taxi" and r.random() < 0.7),
                        "free_cancel_hours": {"taxi": 24, "minivan": 24, "shuttle": 48, "public": 0}[mode],
                        "child_free_age": 2 if mode in ("taxi", "minivan", "shuttle") else 7,
                        "synthetic": True,
                        "source_refs": [GEN_REF, FORMULA_REF, *hotel["source_refs"][:1], *hub["source_refs"][:1]],
                    }
                )
    return out
