"""In-memory catalog loaded from ``data/seed`` with lookup indexes.

Search tools are deterministic functions over this object. The seed directory is
``$SEED_DIR`` or ``<repo>/data/seed``.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path

from app.catalog.models import (
    City,
    CityClimate,
    Flight,
    Hotel,
    Hub,
    SeedMeta,
    Train,
    TransferRoute,
)

DEFAULT_SEED_DIR = Path(__file__).resolve().parents[2] / "data" / "seed"


def seed_dir() -> Path:
    return Path(os.environ.get("SEED_DIR") or DEFAULT_SEED_DIR)


@dataclass
class Catalog:
    cities: dict[str, City]
    hubs: dict[str, Hub]
    climate: dict[str, CityClimate]
    hotels: dict[str, Hotel]
    flights: dict[str, Flight]
    trains: dict[str, Train]
    transfers: dict[str, TransferRoute]
    meta: SeedMeta | None = None
    hotels_by_city: dict[str, list[Hotel]] = field(default_factory=lambda: defaultdict(list))
    flights_by_route_day: dict[tuple[str, str, date], list[Flight]] = field(default_factory=lambda: defaultdict(list))
    trains_by_route_day: dict[tuple[str, str, date], list[Train]] = field(default_factory=lambda: defaultdict(list))
    transfers_by_pair: dict[tuple[str, str], list[TransferRoute]] = field(default_factory=lambda: defaultdict(list))

    def __post_init__(self) -> None:
        for h in self.hotels.values():
            self.hotels_by_city[h.city_id].append(h)
        # Keyed by local departure date at the origin.
        for f in self.flights.values():
            self.flights_by_route_day[(f.origin_city, f.dest_city, f.depart.date())].append(f)
        for t in self.trains.values():
            self.trains_by_route_day[(t.origin_city, t.dest_city, t.depart.date())].append(t)
        for r in self.transfers.values():
            self.transfers_by_pair[(r.hub_id, r.hotel_id)].append(r)

    @classmethod
    def load(cls, path: Path | None = None) -> Catalog:
        path = path or seed_dir()

        def read(name: str, model):
            items = json.loads((path / name).read_text(encoding="utf-8"))
            objs = [model.model_validate(x) for x in items]
            return {getattr(o, "id", None) or o.city_id: o for o in objs}

        meta_path = path / "meta.json"
        return cls(
            cities=read("cities.json", City),
            hubs=read("hubs.json", Hub),
            climate=read("climate.json", CityClimate),
            hotels=read("hotels.json", Hotel),
            flights=read("flights.json", Flight),
            trains=read("trains.json", Train),
            transfers=read("transfers.json", TransferRoute),
            meta=SeedMeta.model_validate_json(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else None,
        )

    def city_of_hub(self, hub_id: str) -> City:
        return self.cities[self.hubs[hub_id].city_id]


@lru_cache(maxsize=1)
def get_catalog() -> Catalog:
    return Catalog.load()
