"""Cities, hubs and climate from real-data snapshots (Wikidata, OurAirports, Open-Meteo)."""

from __future__ import annotations

from pipeline import config
from pipeline.util import snapshot


def build_cities() -> list[dict]:
    wd = snapshot("wikidata.json")
    out = []
    for c in config.CITIES:
        ent = wd[c["wikidata"]]
        visa = config.VISA_KGD if c["id"] == "KGD" else config.VISA[c["country_code"]]
        out.append(
            {
                "id": c["id"],
                "name": c["name"],
                "name_en": c["name_en"],
                "country_code": c["country_code"],
                "country": c["country"],
                "roles": c["roles"],
                "timezone": c["timezone"],
                "coord": {"lat": ent["lat"], "lon": ent["lon"]},
                "population": ent["population"],
                "description": ent["description_ru"],
                "currency": c["currency"],
                "visa": visa,
                "hub_ids": c["airports"] + c["stations"],
                "tags": c["tags"],
                "wikidata": c["wikidata"],
                "source_refs": [f"wikidata:{c['wikidata']}", "ref:visa"],
            }
        )
    return out


def build_hubs() -> list[dict]:
    airports = snapshot("airports.json")
    wd = snapshot("wikidata.json")
    out = []
    for c in config.CITIES:
        for iata in c["airports"]:
            a = airports[iata]
            out.append(
                {
                    "id": iata,
                    "kind": "airport",
                    "city_id": c["id"],
                    "name": config.AIRPORT_NAMES_RU.get(iata, a["name"]),
                    "coord": {"lat": a["lat"], "lon": a["lon"]},
                    "timezone": c["timezone"],
                    "international": True,
                    "source_refs": [f"ourairports:{a['ident']}"],
                }
            )
        for sid in c["stations"]:
            s = config.STATIONS[sid]
            ent = wd[s["wikidata"]]
            out.append(
                {
                    "id": sid,
                    "kind": "station",
                    "city_id": c["id"],
                    "name": s["name"],
                    "coord": {"lat": ent["lat"], "lon": ent["lon"]},
                    "timezone": c["timezone"],
                    "international": False,
                    "source_refs": [f"wikidata:{s['wikidata']}"],
                }
            )
    return out


def build_climate() -> list[dict]:
    snap = snapshot("climate.json")
    out = []
    for city_id, c in snap.items():
        refs = ["open-meteo:archive@{:.2f},{:.2f}".format(*c["air_point"])]
        if c["sea_point"]:
            refs.append("open-meteo:marine@{:.2f},{:.2f}".format(*c["sea_point"]))
        out.append({"city_id": city_id, "period": c["period"], "months": c["months"], "source_refs": refs})
    return out
