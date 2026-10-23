"""Hotels: names and coordinates from OSM, every condition generated (marked synthetic)."""

from __future__ import annotations

import re

from pipeline import config
from pipeline.util import haversine_km, rng, round_to, snapshot

GEN_REF = f"gen:hotels@seed{config.RANDOM_SEED}"

# Month price factors, index 0 = January.
MONTH_PROFILE = {
    "AER": [0.75, 0.75, 0.75, 0.8, 0.9, 1.2, 1.6, 1.7, 1.3, 0.95, 0.7, 0.85],
    "KGD": [0.8, 0.75, 0.8, 0.85, 1.0, 1.25, 1.45, 1.45, 1.1, 0.9, 0.8, 0.95],
    "KZN": [1.05, 0.85, 0.85, 0.9, 1.05, 1.15, 1.2, 1.2, 1.05, 0.95, 0.9, 1.1],
    "IST": [0.85, 0.85, 0.95, 1.15, 1.2, 1.2, 1.15, 1.15, 1.15, 1.1, 0.95, 0.95],
    "AYT": [0.6, 0.6, 0.65, 0.8, 1.05, 1.35, 1.6, 1.65, 1.35, 1.05, 0.75, 0.65],
    "SSH": [1.15, 1.05, 1.15, 1.15, 0.95, 0.85, 0.95, 0.95, 0.95, 1.05, 1.1, 1.25],
    "DXB": [1.3, 1.3, 1.2, 1.0, 0.8, 0.65, 0.6, 0.6, 0.75, 1.0, 1.2, 1.4],
    "EVN": [0.9, 0.85, 0.9, 1.0, 1.1, 1.15, 1.2, 1.2, 1.15, 1.05, 0.9, 1.05],
}
STAR_PRICE = {1: 0.5, 2: 0.65, 3: 1.0, 4: 1.6, 5: 2.6}
KIND_PRICE = {"resort": 1.15, "guesthouse": 0.7, "boutique": 1.2, "hotel": 1.0, "apart": 0.9}
RESORT_CITIES = {"AYT", "SSH"}
CITY_TZ = {c["id"]: c["timezone"] for c in config.CITIES}

_GUEST = re.compile(r"гостев|мини|вилла|guest|pansiyon|pension|дом ", re.I)
_RESORT = re.compile(r"resort|санатор|spa|курорт|club|клуб", re.I)
_APART = re.compile(r"apart|апарт", re.I)
_FOREIGN_SCRIPT = re.compile(r"[^\u0000-ɏЀ-ӿ\s\"'«»№.,&()\-–—:+!/0-9]")


def _display_name(h: dict) -> str:
    name = h["name"]
    if _FOREIGN_SCRIPT.search(name) and h.get("name_en"):
        return h["name_en"]
    return name


def _stars(raw: str | None) -> int | None:
    if not raw:
        return None
    m = re.match(r"\s*(\d)", raw)
    return int(m.group(1)) if m and 1 <= int(m.group(1)) <= 5 else None


# Typical class of international chains: used only when OSM has no stars tag.
BRAND_STARS = [
    (re.compile(r"waldorf|raffles|jw marriott|grand hyatt|hyatt regency|ritz|four seasons", re.I), 5),
    (re.compile(r"hampton|holiday inn express|ibis|ибис|la quinta", re.I), 3),
    (re.compile(r"garden inn|doubletree|courtyard|mercure|radisson|рэдиссон|novotel|azimut|азимут", re.I), 4),
    (re.compile(r"hilton|хилтон|hyatt|marriott|sheraton|renaissance", re.I), 5),
]


def _brand(name: str) -> int | None:
    return next((stars for rx, stars in BRAND_STARS if rx.search(name)), None)


def _pick(city_id: str, candidates: list[dict], n: int) -> list[dict]:
    """Most notable OSM objects first (tags present), unique display names, stable order."""
    pool = sorted(candidates, key=lambda h: (-h["notable"], h["osm"]))
    seen, out = set(), []
    for h in pool:
        name = _display_name(h)
        if _FOREIGN_SCRIPT.search(name) or name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append(h)
        if len(out) == n:
            break
    if len(out) < n:
        raise RuntimeError(f"{city_id}: only {len(out)} usable OSM hotels")
    return out


def _kind(city_id: str, name: str, stars: int, beach_km: float | None, r) -> str:
    if _APART.search(name):
        return "apart"
    if _GUEST.search(name):
        return "guesthouse"
    if _RESORT.search(name) or (city_id in RESORT_CITIES and beach_km is not None and beach_km < 1.0 and stars >= 4):
        return "resort"
    if stars >= 4 and _brand(name) is None and r.random() < 0.25:
        return "boutique"
    return "hotel"


def _rooms(hid: str, kind: str, stars: int, base: int, r) -> list[dict]:
    rooms = [{"id": f"{hid}_r1", "name": "Стандарт", "max_adults": 2, "max_children": 1, "max_occupancy": 3,
              "beds": "двуспальная или две односпальные", "area_m2": r.choice([16, 18, 20, 22]),
              "base_price_rub": base, "inventory": r.randint(3, 15)}]
    if kind != "guesthouse":
        rooms.append({"id": f"{hid}_r2", "name": "Улучшенный", "max_adults": 2, "max_children": 1, "max_occupancy": 3,
                      "beds": "двуспальная", "area_m2": r.choice([24, 26, 28]),
                      "base_price_rub": round_to(base * 1.3, 100), "inventory": r.randint(2, 10)})
    if kind == "resort" or r.random() < 0.6:
        rooms.append({"id": f"{hid}_r3", "name": "Семейный", "max_adults": 2, "max_children": 2, "max_occupancy": 4,
                      "beds": "двуспальная + диван", "area_m2": r.choice([30, 34, 38]),
                      "base_price_rub": round_to(base * 1.55, 100), "inventory": r.randint(1, 5)})
    if kind == "resort" and r.random() < 0.6:
        rooms.append({"id": f"{hid}_r4", "name": "Семейный, две спальни", "max_adults": 4, "max_children": 3,
                      "max_occupancy": 5, "beds": "две спальни", "area_m2": r.choice([45, 52, 60]),
                      "base_price_rub": round_to(base * 2.1, 100), "inventory": r.randint(1, 3)})
    if stars >= 4:
        rooms.append({"id": f"{hid}_r5", "name": "Люкс", "max_adults": 3, "max_children": 1, "max_occupancy": 3,
                      "beds": "кровать king-size", "area_m2": r.choice([40, 48, 55]),
                      "base_price_rub": round_to(base * 2.4, 100), "inventory": r.randint(1, 3)})
    return rooms


def _tariffs(city_id: str, kind: str, room_ids: list[str], r) -> list[dict]:
    def t(tid, name, meal, refundable, factor, days=None, penalty=1):
        return {"id": tid, "name": name, "meal": meal, "refundable": refundable, "free_cancel_days": days,
                "penalty_nights": penalty, "price_factor": factor, "room_ids": room_ids}

    if city_id in RESORT_CITIES and kind in ("resort", "hotel"):
        out = [t("t1", "Всё включено, невозвратный", "AI", False, 1.45),
               t("t2", "Всё включено, бесплатная отмена", "AI", True, 1.62, r.choice([3, 5, 7]), r.choice([1, 2]))]
        if r.random() < 0.5:
            out.append(t("t3", "Завтрак, бесплатная отмена", "BB", True, 1.08, r.choice([1, 2, 3])))
        return out
    out = [t("t1", "Без питания, невозвратный", "RO", False, 0.9),
           t("t2", "Завтрак, бесплатная отмена", "BB", True, 1.12, r.choice([1, 2, 3]))]
    if r.random() < 0.6:
        out.append(t("t3", "Завтрак, невозвратный", "BB", False, 0.98))
    if kind == "resort" or (city_id in ("AER", "DXB") and r.random() < 0.4):
        out.append(t("t4", "Полупансион, бесплатная отмена", "HB", True, 1.35, r.choice([2, 3])))
    return out


def _hhmm(h: int, m: int = 0) -> str:
    return f"{h:02d}:{m:02d}"


def build_hotels() -> list[dict]:
    osm = snapshot("osm_hotels.json")
    beaches = snapshot("osm_beaches.json")
    wd = snapshot("wikidata.json")
    out = []
    for city in config.CITIES:
        if "destination" not in city["roles"]:
            continue
        cid = city["id"]
        center = wd[city["wikidata"]]
        picked = _pick(cid, osm[cid], config.HOTELS_PER_CITY)
        for i, h in enumerate(picked, start=1):
            hid = f"h_{cid}_{i:02d}"
            r = rng("hotel", h["osm"])
            name = _display_name(h)
            beach_km = None
            if beaches.get(cid):
                beach_km = round(min(haversine_km(h["lat"], h["lon"], b["lat"], b["lon"]) for b in beaches[cid]), 2)
                if beach_km > 15:
                    beach_km = None
            osm_stars = _stars(h["stars"])
            stars = osm_stars or _brand(name) or r.choice([3, 3, 4, 4, 5] if cid in RESORT_CITIES | {"DXB"} else [2, 3, 3, 4, 4])
            kind = _kind(cid, name, stars, beach_km, r)
            base = round_to(city["price_level_rub"] * STAR_PRICE[stars] * KIND_PRICE[kind] * r.uniform(0.85, 1.15), 100)
            rooms = _rooms(hid, kind, stars, base, r)
            room_ids = [x["id"] for x in rooms]
            small = kind in ("guesthouse", "apart")
            if small:
                close = r.choice([21, 22, 23])
                reception = {"hours": {"round_the_clock": False, "opens": _hhmm(8), "closes": _hhmm(close)},
                             "late_arrival_possible": r.random() < 0.5, "note": None}
            else:
                reception = {"hours": {"round_the_clock": True, "opens": None, "closes": None},
                             "late_arrival_possible": True, "note": None}
            checkin = 15 if cid in ("DXB", "IST") or r.random() < 0.15 else 14
            checkout = 12 if r.random() < 0.7 else 11
            if r.random() < 0.65:
                guaranteed = r.random() < 0.5
                early = {"available": True, "from": _hhmm(r.choice([6, 8, 9, 10, 11])),
                         "price_rub": round_to(base * r.uniform(0.4, 0.6), 100) if guaranteed else 0,
                         "guaranteed": guaranteed}
            else:
                early = {"available": False, "from": None, "price_rub": None, "guaranteed": False}
            if r.random() < 0.6:
                late = {"available": True, "until": _hhmm(r.choice([16, 17, 18])),
                        "price_rub": round_to(base * r.uniform(0.3, 0.5), 100), "guaranteed": r.random() < 0.5}
            else:
                late = {"available": False, "until": None, "price_rub": None, "guaranteed": False}
            pets_ok = r.random() < (0.55 if small else 0.25 if kind == "resort" else 0.45)
            pets = {"allowed": pets_ok, "max_weight_kg": r.choice([5, 8, 10, 15, 25]) if pets_ok else None,
                    "fee_rub_per_night": r.choice([0, 500, 800, 1000, 1500]) if pets_ok else 0, "note": None}
            resort = kind == "resort"
            kids = {"kids_club": resort and r.random() < 0.8, "kids_pool": resort and r.random() < 0.8,
                    "cribs": r.random() < (0.9 if resort else 0.65), "kids_menu": resort or r.random() < 0.3}
            amenities = ["wifi"]
            if resort or (city.get("sea_point") and r.random() < 0.5):
                amenities.append("pool")
            if beach_km is not None and beach_km < 0.4:
                amenities.append("beach")
            if stars >= 4:
                amenities += ["spa", "gym"]
            if not small:
                amenities.append("restaurant")
            if r.random() < 0.6:
                amenities.append("parking")
            rating = round(min(9.8, max(5.6, 6.6 + 0.5 * stars + r.uniform(-0.9, 0.7))), 1)
            out.append(
                {
                    "id": hid,
                    "city_id": cid,
                    "name": name,
                    "kind": kind,
                    "coord": {"lat": h["lat"], "lon": h["lon"]},
                    "address": h["address"],
                    "stars": stars,
                    "stars_source": "osm" if osm_stars else "generated",
                    "rating": rating,
                    "reviews_count": r.randint(25, 3200),
                    "dist_center_km": round(haversine_km(h["lat"], h["lon"], center["lat"], center["lon"]), 2),
                    "dist_beach_km": beach_km,
                    "checkin_time": _hhmm(checkin),
                    "checkout_time": _hhmm(checkout),
                    "reception": reception,
                    "early_checkin": early,
                    "late_checkout": late,
                    "luggage_storage": not small or r.random() < 0.5,
                    "pets": pets,
                    "kids": kids,
                    "amenities": amenities,
                    "rooms": rooms,
                    "tariffs": _tariffs(cid, kind, room_ids, r),
                    "month_factors": [round(f * r.uniform(0.95, 1.05), 3) for f in MONTH_PROFILE[cid]],
                    "weekend_factor": 1.0 if cid in RESORT_CITIES else round(r.uniform(1.08, 1.18), 2),
                    "extra_guest_rub_per_night": round_to(base * r.uniform(0.15, 0.25), 100),
                    "child_free_age": 7 if resort else 6 if not small else 4,
                    "open_periods": [],
                    "sold_out": [],
                    "synthetic": True,
                    "source_refs": [f"osm:{h['osm']}", GEN_REF],
                }
            )
    return out
