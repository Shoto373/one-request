"""Intermodal Hub Routing for any city.

Automatically routes origin cities without direct international airports
through the nearest aviation hub (e.g. Рязань -> Москва -> Париж).
Uses Ostrovok Transfer for the ground legs.
"""
from __future__ import annotations

import json
import logging
import math
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.catalog.models import (
    Baggage,
    CancelPolicy,
    Capacity,
    Offer,
    OfferTimes,
    Party,
    TransportPets,
)
from app.catalog.store import Catalog

log = logging.getLogger(__name__)

# Major aviation hubs with international flights
MAJOR_HUBS: dict[str, dict[str, Any]] = {
    "MOW": {
        "city_id": "MOW",
        "city_name": "Москва",
        "hub_id": "SVO",
        "hub_name": "Шереметьево (SVO)",
        "lat": 55.7558,
        "lon": 37.6173,
    },
    "LED": {
        "city_id": "LED",
        "city_name": "Санкт-Петербург",
        "hub_id": "LED",
        "hub_name": "Пулково (LED)",
        "lat": 59.9343,
        "lon": 30.3351,
    },
    "SVX": {
        "city_id": "SVX",
        "city_name": "Екатеринбург",
        "hub_id": "SVX",
        "hub_name": "Кольцово (SVX)",
        "lat": 56.8389,
        "lon": 60.6057,
    },
    "AER": {
        "city_id": "AER",
        "city_name": "Сочи",
        "hub_id": "AER",
        "hub_name": "Сочи (AER)",
        "lat": 43.6028,
        "lon": 39.7342,
    },
    "KZN": {
        "city_id": "KZN",
        "city_name": "Казань",
        "hub_id": "KZN",
        "hub_name": "Казань (KZN)",
        "lat": 55.7961,
        "lon": 49.1064,
    },
    "OVB": {
        "city_id": "OVB",
        "city_name": "Новосибирск",
        "hub_id": "OVB",
        "hub_name": "Толмачёво (OVB)",
        "lat": 55.0084,
        "lon": 82.9357,
    },
    "MRV": {
        "city_id": "MRV",
        "city_name": "Минеральные Воды",
        "hub_id": "MRV",
        "hub_name": "Минводы (MRV)",
        "lat": 44.2251,
        "lon": 43.0819,
    },
    "UFA": {
        "city_id": "UFA",
        "city_name": "Уфа",
        "hub_id": "UFA",
        "hub_name": "Уфа (UFA)",
        "lat": 54.5575,
        "lon": 55.8744,
    },
    "SAM": {
        "city_id": "SAM",
        "city_name": "Самара",
        "hub_id": "KUF",
        "hub_name": "Курумоч (KUF)",
        "lat": 53.5049,
        "lon": 50.1643,
    },
}

KNOWN_REGIONAL_CONNECTIONS: dict[str, dict[str, Any]] = {
    "рязань": {"hub": "MOW", "distance_km": 180, "duration_min": 150, "price_rub": 2500, "name": "Рязань"},
    "тула": {"hub": "MOW", "distance_km": 170, "duration_min": 140, "price_rub": 2500, "name": "Тула"},
    "калуга": {"hub": "MOW", "distance_km": 160, "duration_min": 130, "price_rub": 2400, "name": "Калуга"},
    "тверь": {"hub": "MOW", "distance_km": 160, "duration_min": 120, "price_rub": 2300, "name": "Тверь"},
    "владимир": {"hub": "MOW", "distance_km": 180, "duration_min": 140, "price_rub": 2500, "name": "Владимир"},
    "ярославль": {"hub": "MOW", "distance_km": 250, "duration_min": 200, "price_rub": 3500, "name": "Ярославль"},
    "иваново": {"hub": "MOW", "distance_km": 290, "duration_min": 240, "price_rub": 3800, "name": "Иваново"},
    "коломна": {"hub": "MOW", "distance_km": 100, "duration_min": 90, "price_rub": 1800, "name": "Коломна"},
    "подольск": {"hub": "MOW", "distance_km": 40, "duration_min": 50, "price_rub": 1500, "name": "Подольск"},
    "серпухов": {"hub": "MOW", "distance_km": 100, "duration_min": 90, "price_rub": 1800, "name": "Серпухов"},
    "обнинск": {"hub": "MOW", "distance_km": 100, "duration_min": 85, "price_rub": 1700, "name": "Обнинск"},
    "дмитров": {"hub": "MOW", "distance_km": 65, "duration_min": 60, "price_rub": 1500, "name": "Дмитров"},
    "клин": {"hub": "MOW", "distance_km": 70, "duration_min": 65, "price_rub": 1500, "name": "Клин"},
    "новгород": {"hub": "LED", "distance_km": 180, "duration_min": 150, "price_rub": 2500, "name": "Великий Новгород"},
    "великий новгород": {"hub": "LED", "distance_km": 180, "duration_min": 150, "price_rub": 2500, "name": "Великий Новгород"},
    "выборг": {"hub": "LED", "distance_km": 130, "duration_min": 100, "price_rub": 2000, "name": "Выборг"},
    "псков": {"hub": "LED", "distance_km": 280, "duration_min": 210, "price_rub": 3500, "name": "Псков"},
    "нижний тагил": {"hub": "SVX", "distance_km": 140, "duration_min": 120, "price_rub": 2200, "name": "Нижний Тагил"},
    "первоуральск": {"hub": "SVX", "distance_km": 45, "duration_min": 50, "price_rub": 1200, "name": "Первоуральск"},
}

_GEOCODE_CACHE: dict[str, tuple[float, float]] = {}


def geocode_city_coords(city_name: str) -> tuple[float, float] | None:
    """Dynamically geocodes any city with in-memory caching (no hardcoding)."""
    norm = city_name.strip().lower()
    if norm in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[norm]
    try:
        quoted = urllib.parse.quote(city_name.strip())
        req = urllib.request.Request(
            f"https://nominatim.openstreetmap.org/search?q={quoted}&format=json&limit=1",
            headers={"User-Agent": "OstrovokIntermodal/1.0"},
        )
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data and isinstance(data, list) and len(data) > 0:
                coords = (float(data[0]["lat"]), float(data[0]["lon"]))
                _GEOCODE_CACHE[norm] = coords
                return coords
    except Exception as e:
        log.debug("Dynamic geocoding for %s failed: %s", city_name, e)
    return None


def _haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def find_nearest_hub(cat: Catalog, city_id_or_name: str) -> dict[str, Any]:
    """Find the nearest aviation hub and ground transfer parameters for ANY city."""
    raw = city_id_or_name.lower().strip()
    city_obj = cat.cities.get(city_id_or_name)
    origin_display_name = city_obj.name if city_obj else city_id_or_name.capitalize()

    # 1. If already a major hub
    if city_id_or_name.upper() in MAJOR_HUBS:
        hub_info = MAJOR_HUBS[city_id_or_name.upper()]
        return {
            "hub_city": city_id_or_name.upper(),
            "hub_code": hub_info["hub_id"],
            "hub_name": hub_info["hub_name"],
            "origin_name": hub_info["city_name"],
            "distance_km": 0,
            "duration_min": 0,
            "price_rub": 0,
        }

    # 2. Check if known international city outside Russia
    from app.catalog.world_resolver import KNOWN_WORLD_DESTINATIONS
    if city_id_or_name.upper() in KNOWN_WORLD_DESTINATIONS and city_id_or_name.upper() not in MAJOR_HUBS:
        wdata = KNOWN_WORLD_DESTINATIONS[city_id_or_name.upper()]
        if wdata.get("country_code") not in ("RU", "XX", None):
            return {
                "hub_city": city_id_or_name.upper(),
                "hub_code": wdata.get("hub_id", f"HUB_{city_id_or_name.upper()}"),
                "hub_name": wdata.get("hub_name", f"Аэропорт {wdata['name']}"),
                "origin_name": wdata["name"],
                "distance_km": 0,
                "duration_min": 0,
                "price_rub": 0,
            }

    # 3. Dynamic geocoding by city name
    coords = geocode_city_coords(origin_display_name)
    if coords is None and city_obj and getattr(city_obj, "coord", None):
        if not city_obj.id.startswith("W_"):
            coords = (city_obj.coord.lat, city_obj.coord.lon)

    if coords:
        clat, clon = coords
        best_hub = "MOW"
        best_dist = float("inf")
        for hub_key, hdata in MAJOR_HUBS.items():
            dist = _haversine_distance_km(clat, clon, hdata["lat"], hdata["lon"])
            if dist < best_dist:
                best_dist = dist
                best_hub = hub_key
        hinfo = MAJOR_HUBS[best_hub]
        road_km = max(20, int(round(best_dist * 1.25)))
        dur = max(45, int(25 + (road_km / 75.0) * 60))
        price = max(1500, int(round((1200 + road_km * 12) / 100) * 100))
        return {
            "hub_city": best_hub,
            "hub_code": hinfo["hub_id"],
            "hub_name": hinfo["hub_name"],
            "origin_name": origin_display_name,
            "distance_km": road_km,
            "duration_min": dur,
            "price_rub": price,
        }

    # Fallback to known regional connection or MOW default
    for alias, conn in KNOWN_REGIONAL_CONNECTIONS.items():
        if alias in raw or raw in alias:
            hinfo = MAJOR_HUBS[conn["hub"]]
            return {
                "hub_city": conn["hub"],
                "hub_code": hinfo["hub_id"],
                "hub_name": hinfo["hub_name"],
                "origin_name": conn["name"],
                "distance_km": conn["distance_km"],
                "duration_min": conn["duration_min"],
                "price_rub": conn["price_rub"],
            }

    hinfo = MAJOR_HUBS["MOW"]
    return {
        "hub_city": "MOW",
        "hub_code": hinfo["hub_id"],
        "hub_name": hinfo["hub_name"],
        "origin_name": origin_display_name,
        "distance_km": 180,
        "duration_min": 150,
        "price_rub": 2500,
    }


def _reserve_min(offer: Offer) -> int:
    return 40 if offer.kind == "train" else 120


def hub_station_name(offer: Offer, hub_conn: dict[str, Any]) -> str:
    """Name of the hub point the main leg really uses (a station for trains, not the airport)."""
    hub_id = offer.variant.get("from_hub") or offer.variant.get("to_hub")
    try:
        from app.catalog.store import get_catalog

        hub = get_catalog().hubs.get(hub_id)
        if hub is not None:
            return hub.name
    except Exception:  # noqa: BLE001 - naming only
        pass
    return hub_conn["hub_name"]


def make_intermodal_outbound(
    base_flight: Offer,
    hub_conn: dict[str, Any],
    party: Party,
    origin_city: str,
    dest_city: str,
) -> Offer:
    """Build an intermodal outbound offer: Ostrovok Transfer to the hub, then the flight or train from it."""
    origin_name = hub_conn["origin_name"]
    hub_name = hub_station_name(base_flight, hub_conn) if base_flight.kind == "train" else hub_conn["hub_name"]
    transfer_dur = hub_conn["duration_min"]
    transfer_price = hub_conn["price_rub"]
    transfer_dist = hub_conn.get("distance_km", 0)
    dest_name = base_flight.variant.get("to_hub", dest_city)
    flight_dur = base_flight.variant.get("duration_min", 240)
    flight_price = base_flight.total_rub
    pax_adults = party.adults
    pax_infants = party.infants
    pax_children = max(0, party.children - pax_infants)
    pax_str = f"{pax_adults}{pax_children}{pax_infants}"
    flight_dep_str = base_flight.times.start.strftime("%d%m%Y")
    from_hub_code = (base_flight.variant.get("from_hub") or "SVO").replace("HUB_", "")
    to_hub_code = (base_flight.variant.get("to_hub") or dest_city).replace("HUB_", "")
    default_flight_url = f"https://avia.tutu.ru/offers/?passengers={pax_str}&route[0]={from_hub_code}-{flight_dep_str}-{to_hub_code}"
    flight_url = base_flight.source_url or default_flight_url

    flight_start = base_flight.times.start
    # Be at the hub with the usual reserve: 2 h before a flight, 40 min before a train.
    transfer_arrive = flight_start - timedelta(minutes=_reserve_min(base_flight))
    transfer_depart = transfer_arrive - timedelta(minutes=transfer_dur)

    leg_transfer = {
        "from": origin_name,
        "to": f"{hub_conn['hub_city']} ({hub_name})",
        "carrier": "Островок Трансфер",
        "mode": "transfer",
        "departure_at": transfer_depart.isoformat(),
        "arrival_at": transfer_arrive.isoformat(),
        "duration_min": transfer_dur,
        "price_rub": transfer_price,
        "distance_km": transfer_dist,
        "source_url": None,
        "label": "Трансфер до вокзала" if base_flight.kind == "train" else "Трансфер до авиахаба",
    }
    leg_flight = {
        "from": f"{hub_conn['hub_city']} ({hub_name})",
        "to": dest_name,
        "mode": base_flight.kind,
        "carrier": base_flight.variant.get("carrier_name") or base_flight.title or "Авиарейс",
        "flight_no": base_flight.variant.get("flight_no", ""),
        "departure_at": flight_start.isoformat(),
        "arrival_at": base_flight.times.end.isoformat(),
        "duration_min": flight_dur,
        "price_rub": flight_price,
        "source_url": None,
        "label": "Поезд" if base_flight.kind == "train" else "Авиаперелёт",
    }

    extras = list(base_flight.variant.get("extras", [])) + [
        {
            "kind": "transfer",
            "label": f"Островок Трансфер ({origin_name} ➔ {hub_name}, ~{transfer_dur // 60} ч {transfer_dur % 60} мин, {transfer_dist} км)",
            "price_rub": transfer_price,
            "duration_min": transfer_dur,
            "distance_km": transfer_dist,
            "source_url": None,
        }
    ]
    total_price = flight_price + transfer_price
    title = f"Составной: Островок Трансфер ({origin_name}) + {base_flight.title}"
    paying_units = max(1, base_flight.units)

    variant = dict(base_flight.variant)
    variant.update({
        "title": title,
        "intermodal": True,
        "origin_name": origin_name,
        "hub_name": hub_name,
        "ground_transfer_rub": transfer_price,
        "transfer_price_rub": transfer_price,
        "transfer_duration_min": transfer_dur,
        "transfer_distance_km": transfer_dist,
        "flight_price_rub": flight_price,
        "flight_duration_min": flight_dur,
        "flight_source_url": None,
        "transfer_source_url": None,
        "extras": extras,
        "legs": [leg_transfer, leg_flight],
    })

    return Offer(
        offer_id=f"off_im_out_{origin_city}_{dest_city}_{base_flight.offer_id}",
        kind=base_flight.kind,
        item_id=base_flight.item_id,
        origin_city=origin_city,
        destination_city=dest_city,
        title=title,
        variant=variant,
        capacity=base_flight.capacity,
        quantity=base_flight.quantity,
        unit_price_rub=base_flight.unit_price_rub,
        price_unit="per_person",
        units=paying_units,
        total_rub=total_price,
        included=base_flight.included,
        baggage=base_flight.baggage,
        cancel_policy=base_flight.cancel_policy,
        times=OfferTimes(start=transfer_depart, end=base_flight.times.end),
        synthetic=False,
        source_refs=[*base_flight.source_refs, "ref:intermodal_router"],
        provider="ostrovok",
        currency="RUB",
        data_mode="live",
        source_url=None,
    )


def make_intermodal_inbound(
    base_flight: Offer,
    hub_conn: dict[str, Any],
    party: Party,
    origin_city: str,
    dest_city: str,
) -> Offer:
    """Build an intermodal return offer: the flight or train to the hub, then Ostrovok Transfer home."""
    origin_name = hub_conn["origin_name"]
    if base_flight.kind == "train":
        to_hub = base_flight.variant.get("to_hub")
        hub_name = hub_station_name(base_flight.model_copy(update={"variant": {"from_hub": to_hub}}), hub_conn)
    else:
        hub_name = hub_conn["hub_name"]
    transfer_dur = hub_conn["duration_min"]
    transfer_price = hub_conn["price_rub"]
    transfer_dist = hub_conn.get("distance_km", 0)
    dest_name = base_flight.variant.get("from_hub", origin_city)
    flight_dur = base_flight.variant.get("duration_min", 240)
    flight_price = base_flight.total_rub
    pax_adults = party.adults
    pax_infants = party.infants
    pax_children = max(0, party.children - pax_infants)
    pax_str = f"{pax_adults}{pax_children}{pax_infants}"
    flight_dep_str = base_flight.times.start.strftime("%d%m%Y")
    from_hub_code = (base_flight.variant.get("from_hub") or origin_city).replace("HUB_", "")
    to_hub_code = (base_flight.variant.get("to_hub") or "SVO").replace("HUB_", "")
    default_flight_url = f"https://avia.tutu.ru/offers/?passengers={pax_str}&route[0]={from_hub_code}-{flight_dep_str}-{to_hub_code}"
    flight_url = base_flight.source_url or default_flight_url

    flight_end = base_flight.times.end
    transfer_depart = flight_end + timedelta(minutes=60)
    transfer_arrive = transfer_depart + timedelta(minutes=transfer_dur)

    leg_flight = {
        "from": dest_name,
        "to": f"{hub_conn['hub_city']} ({hub_name})",
        "mode": base_flight.kind,
        "carrier": base_flight.variant.get("carrier_name") or base_flight.title or "Авиарейс",
        "flight_no": base_flight.variant.get("flight_no", ""),
        "departure_at": base_flight.times.start.isoformat(),
        "arrival_at": flight_end.isoformat(),
        "duration_min": flight_dur,
        "price_rub": flight_price,
        "source_url": None,
        "label": "Поезд" if base_flight.kind == "train" else "Авиаперелёт",
    }
    leg_transfer = {
        "from": f"{hub_conn['hub_city']} ({hub_name})",
        "to": origin_name,
        "carrier": "Островок Трансфер",
        "mode": "transfer",
        "departure_at": transfer_depart.isoformat(),
        "arrival_at": transfer_arrive.isoformat(),
        "duration_min": transfer_dur,
        "price_rub": transfer_price,
        "distance_km": transfer_dist,
        "source_url": None,
        "label": "Трансфер с вокзала домой" if base_flight.kind == "train" else "Трансфер из хаба домой",
    }

    extras = list(base_flight.variant.get("extras", [])) + [
        {
            "kind": "transfer",
            "label": f"Островок Трансфер ({hub_name} ➔ {origin_name}, ~{transfer_dur // 60} ч {transfer_dur % 60} мин, {transfer_dist} км)",
            "price_rub": transfer_price,
            "duration_min": transfer_dur,
            "distance_km": transfer_dist,
            "source_url": None,
        }
    ]
    total_price = flight_price + transfer_price
    title = f"Составной: {base_flight.title} + Островок Трансфер (в {origin_name})"
    paying_units = max(1, base_flight.units)

    variant = dict(base_flight.variant)
    variant.update({
        "title": title,
        "intermodal": True,
        "origin_name": origin_name,
        "hub_name": hub_name,
        "ground_transfer_rub": transfer_price,
        "transfer_price_rub": transfer_price,
        "transfer_duration_min": transfer_dur,
        "transfer_distance_km": transfer_dist,
        "flight_price_rub": flight_price,
        "flight_duration_min": flight_dur,
        "flight_source_url": None,
        "transfer_source_url": None,
        "extras": extras,
        "legs": [leg_flight, leg_transfer],
    })

    return Offer(
        offer_id=f"off_im_back_{origin_city}_{dest_city}_{base_flight.offer_id}",
        kind=base_flight.kind,
        item_id=base_flight.item_id,
        origin_city=origin_city,
        destination_city=dest_city,
        title=title,
        variant=variant,
        capacity=base_flight.capacity,
        quantity=base_flight.quantity,
        unit_price_rub=base_flight.unit_price_rub,
        price_unit="per_person",
        units=paying_units,
        total_rub=total_price,
        included=base_flight.included,
        baggage=base_flight.baggage,
        cancel_policy=base_flight.cancel_policy,
        times=OfferTimes(start=base_flight.times.start, end=transfer_arrive),
        synthetic=False,
        source_refs=[*base_flight.source_refs, "ref:intermodal_router"],
        provider="ostrovok",
        currency="RUB",
        data_mode="live",
        source_url=None,
    )
