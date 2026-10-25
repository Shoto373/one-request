"""Single product coverage configuration for supported vacation destinations."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.catalog.store import get_catalog
from app.planning.query import resolve_city

_CONFIG = Path(__file__).with_name("product_coverage.json")


@lru_cache(maxsize=1)
def destination_ids() -> tuple[str, ...]:
    raw = json.loads(_CONFIG.read_text(encoding="utf-8"))
    ids = tuple(dict.fromkeys(str(x).strip().upper() for x in raw.get("rest_destination_ids", []) if str(x).strip()))
    catalog = get_catalog()
    if not ids or any(city_id not in catalog.cities for city_id in ids):
        raise ValueError("product_coverage.json must list known rest destination city IDs")
    return ids


def fixed_destination_id() -> str:
    ids = destination_ids()
    if len(ids) != 1:
        raise ValueError("This UI requires one configured destination; update its selection interface before expanding coverage")
    return ids[0]


def resolve_destination(value: Any) -> str | None:
    return resolve_city(get_catalog(), value)


def destination_supported(value: Any) -> bool:
    city_id = resolve_destination(value)
    return city_id in destination_ids() if city_id else False


def coverage_payload() -> dict[str, Any]:
    catalog = get_catalog()
    destinations = [{"id": city_id, "name": catalog.cities[city_id].name}
                    for city_id in destination_ids()]
    return {
        "destinations": destinations,
        "destination_selection_required": len(destinations) > 1,
        "fixed_destination": destinations[0] if len(destinations) == 1 else None,
        "origin_policy": "user_city_within_provider_coverage",
        "provider_coverage_required": True,
    }
