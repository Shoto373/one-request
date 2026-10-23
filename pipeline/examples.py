"""Write ``data/examples/*.json``: real outputs of the track A tools for tracks B and C.

    python -m pipeline.examples

Not seeded into the DB; use them for UI types, mocks and SSE wiring.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

from app.catalog.models import Party, Pet
from app.catalog.store import Catalog
from app.planning.build import build_trip_bundles
from app.planning.models import bundle_to_wire
from app.planning.query import TripQuery
from app.tools.search import search_hotels, search_transfers, search_transport

OUT = Path(__file__).resolve().parent.parent / "data" / "examples"

SCENARIOS = {
    # Sprint 1 checkpoint: «Из Москвы в Сочи 10–17 ноября, двое взрослых».
    "bundle_mow_aer.json": TripQuery("MOW", "AER", date(2026, 11, 10), date(2026, 11, 17), Party(adults=2),
                                     kinds=("flight",)),
    "bundle_mow_kzn_train.json": TripQuery("MOW", "KZN", date(2026, 11, 13), date(2026, 11, 15), Party(adults=2),
                                           kinds=("train",)),
    "bundle_mow_ayt_family_dog.json": TripQuery("MOW", "AYT", date(2026, 11, 12), date(2026, 11, 19),
                                                Party(adults=2, children_ages=[5], pets=[Pet(weight_kg=7)])),
    "bundle_mow_dxb_infeasible.json": TripQuery("MOW", "DXB", date(2026, 11, 10), date(2026, 11, 17), Party(adults=3),
                                                budget_rub=30_000, budget_hard=True),
}


def dump(name: str, data) -> None:
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"example {name}")


def main() -> None:
    cat = Catalog.load()
    OUT.mkdir(parents=True, exist_ok=True)
    for name, q in SCENARIOS.items():
        res = build_trip_bundles(cat, q)
        query = asdict(q)
        query["party"] = q.party.model_dump(mode="json")
        dump(name, {
            "query": json.loads(json.dumps(query, default=str)),
            "bundles": [bundle_to_wire(b) for b in res.bundles],  # as stored in TripState
            "notes": res.notes,
            "cheapest_total_rub": res.cheapest_total_rub,
        })
    party = Party(adults=2)
    flights = search_transport(cat, "MOW", "AER", date(2026, 11, 10), party, ("flight",))
    hotels = search_hotels(cat, "AER", date(2026, 11, 10), date(2026, 11, 17), party, cheapest_per_hotel=True)
    transfers = search_transfers(cat, "AER", hotels[0].item_id, party, flights[0].times.end, "to_hotel")
    dump("offers_mow_aer.json", {
        "flights": [o.model_dump(mode="json") for o in flights[:5]],
        "hotels": [o.model_dump(mode="json") for o in hotels],
        "transfers": [o.model_dump(mode="json") for o in transfers],
    })


if __name__ == "__main__":
    main()
