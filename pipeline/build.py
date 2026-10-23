"""Generate ``data/seed/catalog/*.json`` from snapshots (offline, deterministic).

    python -m pipeline.build

Every object is validated against ``app.catalog.models`` before it is written.
Files are JSON arrays with one object per line (compact, diff-friendly).
"""

from __future__ import annotations

import json
from pathlib import Path

from app.catalog.models import SEED_FILES, SeedMeta
from pipeline import config
from pipeline.edge_cases import plant_hotels, scan_transport, transfer_edges
from pipeline.flights import build_flights
from pipeline.hotels import build_hotels
from pipeline.places import build_climate, build_cities, build_hubs
from pipeline.trains import build_trains
from pipeline.transfers import build_transfers

SEED_DIR = Path(__file__).resolve().parent.parent / "data" / "seed" / "catalog"

SOURCES = [
    "Wikidata (CC0): города, координаты, население, описание, вокзалы",
    "OurAirports (public domain): аэропорты, координаты",
    "OpenStreetMap (ODbL, © участники OpenStreetMap): названия и координаты отелей, пляжи",
    "Open-Meteo (CC BY 4.0): архив погоды и температура моря",
    "Справочник визовых правил для граждан РФ (ручной, см. visa.checked_at)",
    f"Синтетика (seed={config.RANDOM_SEED}): рейсы, поезда, условия отелей, трансферы",
]


def write_array(path: Path, items: list[dict]) -> None:
    lines = ",\n".join(json.dumps(x, ensure_ascii=False, separators=(",", ":")) for x in items)
    path.write_text("[\n" + lines + "\n]\n", encoding="utf-8", newline="\n")


def main() -> None:
    cities = build_cities()
    hubs = build_hubs()
    climate = build_climate()
    hotels = build_hotels()
    edges = plant_hotels(hotels)
    flights = build_flights(hubs, cities)
    trains = build_trains(hubs)
    transfers = build_transfers(cities, hubs, hotels)
    edges += scan_transport(flights, trains) + transfer_edges()

    data = {
        "cities.json": cities,
        "hubs.json": hubs,
        "climate.json": climate,
        "hotels.json": hotels,
        "flights.json": flights,
        "trains.json": trains,
        "transfers.json": transfers,
    }
    SEED_DIR.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name, items in data.items():
        model = SEED_FILES[name]
        validated = [model.model_validate(x).model_dump(mode="json") for x in items]
        write_array(SEED_DIR / name, validated)
        counts[name.removesuffix(".json")] = len(validated)

    meta = SeedMeta(
        random_seed=config.RANDOM_SEED,
        window={"start": config.WINDOW_START, "end": config.WINDOW_END},
        counts=counts,
        edge_cases=edges,
        sources=SOURCES,
    )
    (SEED_DIR / "meta.json").write_text(
        json.dumps(meta.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    total = sum(p.stat().st_size for p in SEED_DIR.glob("*.json"))
    for name, n in counts.items():
        print(f"{name:10s} {n:6d}")
    print(f"edge cases {len(edges)}; seed size {total / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
