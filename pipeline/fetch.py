"""Refresh real-data snapshots in ``pipeline/snapshots`` (network required).

    python -m pipeline.fetch            # all sources
    python -m pipeline.fetch osm        # one source: wikidata | airports | osm | climate

Snapshots are compact, committed extracts; ``pipeline.build`` reads only them.
"""

from __future__ import annotations

import csv
import io
import json
import statistics
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from pipeline import config, sources

SNAP_DIR = Path(__file__).parent / "snapshots"


def save(name: str, data: Any) -> None:
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    (SNAP_DIR / name).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"snapshot {name}")


def _claim_value(ent: dict, prop: str) -> Any:
    for claim in ent.get("claims", {}).get(prop, []):
        if claim.get("rank") == "deprecated":
            continue
        value = claim["mainsnak"].get("datavalue", {}).get("value")
        if value is not None:
            return value
    return None


def _population(ent: dict) -> int | None:
    best: tuple[str, int] | None = None
    for claim in ent.get("claims", {}).get("P1082", []):
        value = claim["mainsnak"].get("datavalue", {}).get("value")
        if not value:
            continue
        when = claim.get("qualifiers", {}).get("P585", [{}])[0].get("datavalue", {}).get("value", {}).get("time", "")
        amount = int(float(value["amount"]))
        if claim.get("rank") == "preferred":
            return amount
        if best is None or when > best[0]:
            best = (when, amount)
    return best[1] if best else None


def fetch_wikidata() -> None:
    ids = [c["wikidata"] for c in config.CITIES] + [s["wikidata"] for s in config.STATIONS.values()]
    res = sources.http_json(
        sources.WIKIDATA_API,
        {
            "action": "wbgetentities",
            "ids": "|".join(ids),
            "props": "labels|descriptions|claims",
            "languages": "ru|en",
            "format": "json",
        },
    )
    out = {}
    for qid, ent in res["entities"].items():
        coord = _claim_value(ent, "P625")
        out[qid] = {
            "label_ru": ent["labels"].get("ru", {}).get("value"),
            "label_en": ent["labels"].get("en", {}).get("value"),
            "description_ru": ent["descriptions"].get("ru", {}).get("value"),
            "lat": round(coord["latitude"], 6),
            "lon": round(coord["longitude"], 6),
            "population": _population(ent),
        }
    save("wikidata.json", out)


def fetch_airports() -> None:
    wanted = {iata for c in config.CITIES for iata in c["airports"]}
    raw = sources.http_get(sources.OURAIRPORTS_CSV).decode("utf-8")
    out = {}
    for row in csv.DictReader(io.StringIO(raw)):
        if row["iata_code"] in wanted and row["type"] in ("large_airport", "medium_airport"):
            out[row["iata_code"]] = {
                "ourairports_id": row["id"],
                "ident": row["ident"],
                "name": row["name"],
                "lat": round(float(row["latitude_deg"]), 6),
                "lon": round(float(row["longitude_deg"]), 6),
                "country": row["iso_country"],
                "municipality": row["municipality"],
            }
    missing = wanted - out.keys()
    if missing:
        raise RuntimeError(f"airports missing in OurAirports: {missing}")
    save("airports.json", dict(sorted(out.items())))


SNAPSHOT_HOTELS = 40
_JUNK_NAME = ("ресепшн", "reception", "квартир", "apartment", "комнат", "room ", "хостел", "hostel")


def fetch_osm() -> None:
    hotels: dict[str, list[dict]] = {}
    beaches: dict[str, list[dict]] = {}
    for city in config.CITIES:
        if "hotel_area" not in city:
            continue
        lat, lon, radius = city["hotel_area"]
        res = sources.overpass(
            f'[out:json][timeout:120];nwr["tourism"="hotel"]["name"](around:{radius},{lat},{lon});out center tags;'
        )
        items = []
        for el in res["elements"]:
            tags = el.get("tags", {})
            name = tags.get("name:ru") or tags.get("name")
            if not name or any(j in name.lower() for j in _JUNK_NAME):
                continue
            pt = el if "lat" in el else el.get("center")
            if not pt:
                continue
            addr = " ".join(x for x in (tags.get("addr:street"), tags.get("addr:housenumber")) if x) or None
            items.append(
                {
                    "osm": f"{el['type']}/{el['id']}",
                    "name": name,
                    "name_en": tags.get("name:en"),
                    "stars": tags.get("stars"),
                    "address": addr,
                    "lat": round(pt["lat"], 6),
                    "lon": round(pt["lon"], 6),
                    "notable": sum(
                        1 for k in ("stars", "website", "contact:website", "phone", "contact:phone", "addr:street",
                                    "name:en", "wikidata", "rooms", "operator", "brand")
                        if k in tags
                    ),
                }
            )
        # Keep the most notable candidates only: the snapshot stays small and reviewable.
        items.sort(key=lambda h: (-h["notable"], h["osm"]))
        hotels[city["id"]] = sorted(items[:SNAPSHOT_HOTELS], key=lambda h: h["osm"])
        print(f"osm hotels {city['id']}: {len(items)}")
        if "beach" in city["tags"]:
            res = sources.overpass(
                f'[out:json][timeout:120];nwr["natural"="beach"](around:{radius + 3000},{lat},{lon});out center;'
            )
            pts = []
            for el in res["elements"]:
                pt = el if "lat" in el else el.get("center")
                if pt:
                    pts.append({"osm": f"{el['type']}/{el['id']}", "lat": round(pt["lat"], 6), "lon": round(pt["lon"], 6)})
            pts.sort(key=lambda b: b["osm"])
            beaches[city["id"]] = pts
            print(f"osm beaches {city['id']}: {len(pts)}")
    save("osm_hotels.json", hotels)
    save("osm_beaches.json", beaches)


def _monthly(days: list[str], values: list[float | None]) -> dict[int, list[float]]:
    by_month: dict[int, list[float]] = defaultdict(list)
    for d, v in zip(days, values):
        if v is not None:
            by_month[int(d[5:7])].append(v)
    return by_month


def fetch_climate() -> None:
    out = {}
    a0, a1 = config.CLIMATE_AIR_YEARS
    s0, s1 = config.CLIMATE_SEA_YEARS
    years = a1 - a0 + 1
    for city in config.CITIES:
        if "destination" not in city["roles"]:
            continue
        lat, lon, _ = city["hotel_area"]
        air = sources.http_json(
            sources.OPEN_METEO_ARCHIVE,
            {
                "latitude": lat, "longitude": lon,
                "start_date": f"{a0}-01-01", "end_date": f"{a1}-12-31",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,sunshine_duration",
                "timezone": city["timezone"],
            },
        )["daily"]
        tmax = _monthly(air["time"], air["temperature_2m_max"])
        tmin = _monthly(air["time"], air["temperature_2m_min"])
        prec = _monthly(air["time"], air["precipitation_sum"])
        sun = _monthly(air["time"], air["sunshine_duration"])
        sea: dict[int, list[float]] = {}
        if city.get("sea_point"):
            slat, slon = city["sea_point"]
            marine = sources.http_json(
                sources.OPEN_METEO_MARINE,
                {
                    "latitude": slat, "longitude": slon,
                    "start_date": f"{s0}-01-01", "end_date": f"{s1}-12-31",
                    "hourly": "sea_surface_temperature", "timezone": city["timezone"],
                },
            )["hourly"]
            sea = _monthly([t[:10] for t in marine["time"]], marine["sea_surface_temperature"])
        months = []
        for m in range(1, 13):
            months.append(
                {
                    "month": m,
                    "air_temp_day": round(statistics.fmean(tmax[m]), 1),
                    "air_temp_night": round(statistics.fmean(tmin[m]), 1),
                    "sea_temp": round(statistics.fmean(sea[m]), 1) if sea.get(m) else None,
                    "precip_mm": round(sum(prec[m]) / years, 1),
                    "rainy_days": round(sum(1 for v in prec[m] if v >= 1.0) / years, 1),
                    "sun_hours": round(statistics.fmean(sun[m]) / 3600, 1),
                }
            )
        out[city["id"]] = {
            "air_point": [lat, lon],
            "sea_point": list(city["sea_point"]) if city.get("sea_point") else None,
            "period": f"{a0}-{a1}" + (f" (sea {s0}-{s1})" if city.get("sea_point") else ""),
            "months": months,
        }
        print(f"climate {city['id']}")
    save("climate.json", out)


STEPS = {"wikidata": fetch_wikidata, "airports": fetch_airports, "osm": fetch_osm, "climate": fetch_climate}

if __name__ == "__main__":
    names = sys.argv[1:] or list(STEPS)
    for n in names:
        STEPS[n]()
    print(f"done {date.today()}")
