"""get_climate: monthly climate of a destination from the seed (Open-Meteo, 2021–2025)."""

from __future__ import annotations

from app.catalog.models import ClimateMonth
from app.catalog.store import Catalog


def climate_ref(city_id: str, month: int, field: str) -> str:
    """Claim ref format from architecture section 6: climate:AYT:11.sea_temp."""
    return f"climate:{city_id}:{month}.{field}"


def get_climate(cat: Catalog, city_id: str, month: int) -> ClimateMonth | None:
    climate = cat.climate.get(city_id)
    if climate is None:
        return None
    return next((m for m in climate.months if m.month == month), None)
