"""Bounded, read-only smoke test of the official Tutu MCP.

Never invokes checkout, passenger registration, booking, reservation, or payment.
Run deliberately with ``--live``; normal pytest does not contact Tutu.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date, timedelta

from app.providers.tutu.client import TutuMCPClient
from app.product_scope import coverage_payload, fixed_destination_id
from app.catalog.store import get_catalog


def summary(label: str, data: dict, key: str) -> dict:
    rows = data.get(key) or []
    first = rows[0] if rows and isinstance(rows[0], dict) else {}
    meta = data.get("meta") or {}
    meta_view = {k: meta.get(k) for k in ("transport", "round_trip", "return_date", "from", "to", "page_size",
                                             "total_returned", "total_matched", "has_more") if k in meta}
    if meta.get("pricing"):
        pricing = meta["pricing"]
        meta_view["pricing"] = {k: pricing.get(k) for k in ("basis", "passengers", "note") if k in pricing}
    legs = first.get("legs") or []
    route = [{"label": leg.get("label"), "from": leg.get("from"), "to": leg.get("to"),
              "departure_at": leg.get("departure_at"), "arrival_at": leg.get("arrival_at"),
              "segments": [{k: seg.get(k) for k in ("from", "to", "departure_at", "arrival_at", "carrier", "voyage_no") if k in seg}
                           for seg in leg.get("segments") or []]} for leg in legs]
    best = first.get("best_offer") or {}
    fares = first.get("variants") or first.get("fares") or []
    if isinstance(fares, dict):
        fares = [fares]
    if not isinstance(fares, list):
        fares = []
    return {
        "case": label, "count_returned": len(rows), "meta": meta_view,
        "first_result": {k: first.get(k) for k in ("name", "hotel_id", "offer_id", "departure_at", "arrival_at",
                                                       "price", "price_party", "carriers", "transport", "search_results_url") if k in first}
                       | ({"route_legs": route} if route else {})
                       | ({"best_offer": {k: best.get(k) for k in ("price", "breakfast_included", "meal_name", "free_cancellation", "checkout_url") if k in best}}
                          if best else {})
                       | ({"fare_facts": [{k: fare.get(k) for k in ("name", "fare", "price", "conditions", "baggage", "refundable") if k in fare}
                                           for fare in fares[:2] if isinstance(fare, dict)]} if fares else {}),
    }


async def run(depart: date, return_day: date, origin: str) -> list[dict]:
    report = []
    destination = get_catalog().cities[fixed_destination_id()].name
    async with TutuMCPClient(max_calls=8, max_parallel=2) as client:
        rail_out, rail_back, avia, hotels = await asyncio.gather(
            client.call("search_rail", {"origin": origin, "destination": destination, "departure_date": depart.isoformat(),
                                         "passengers": 3, "page": 1, "page_size": 5, "sort": "price_asc", "view": "compact"}),
            client.call("search_rail", {"origin": destination, "destination": origin, "departure_date": return_day.isoformat(),
                                         "passengers": 3, "page": 1, "page_size": 5, "sort": "price_asc", "view": "compact"}),
            client.call("search_avia", {"origin": origin, "destination": destination, "departure_date": depart.isoformat(),
                                         "return_date": return_day.isoformat(), "adults": 2, "children": 1, "infants": 0,
                                         "page": 1, "page_size": 5, "sort": "price_asc", "view": "compact"}),
            client.call("search_hotels", {"city_name": destination, "check_in": depart.isoformat(), "check_out": return_day.isoformat(),
                                           "adults": 2, "children_ages": [5], "page": 1, "page_size": 5, "view": "compact"}),
        )
        report.extend([
            summary(f"rail_{origin}_{destination}", rail_out.data, "offers"),
            summary(f"rail_{destination}_{origin}", rail_back.data, "offers"),
            summary(f"avia_{origin}_{destination}_round_trip", avia.data, "offers"),
            summary(f"hotel_{destination}_2_adults_child_5", hotels.data, "hotels"),
        ])
        rows = hotels.data.get("hotels") or []
        if rows:
            row = rows[0]
            details = await client.call("get_offer_details", {
                "product_type": "hotels", "hotel_id": row.get("hotel_id"),
                "check_in": depart.isoformat(), "check_out": return_day.isoformat(),
                "adults": 2, "children_ages": [5], "view": "compact",
            })
            raw = details.data
            hotel = raw.get("hotel") or {}
            rate_rows = [rate for room in raw.get("rooms") or [] for rate in room.get("rates") or []]
            report.append({"case": "hotel_offer_details", "hotel_id": row.get("hotel_id"),
                           "check_in_time": hotel.get("check_in_time"), "check_out_time": hotel.get("check_out_time"),
                           "amenities": [a for group in hotel.get("amenity_groups") or [] for a in group.get("amenities") or []],
                           "rates": [{k: rate.get(k) for k in ("price", "price_basis", "breakfast_included", "free_cancellation",
                                                                  "free_cancellation_until", "pay_at_hotel", "pay_online") if k in rate}
                                     for rate in rate_rows[:3]], "unknown": ["room_size_sqm", "max_occupancy", "pet weight rules"]})
        report.append({"server_version": client.server_version, "read_only_calls": client.calls_used,
                       "booking_or_payment_called": False})
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="explicitly allow read-only network requests")
    parser.add_argument("--departure", type=date.fromisoformat)
    parser.add_argument("--return-date", type=date.fromisoformat)
    parser.add_argument("--origin", required=True, help="Город отправления, который разрешит Туту")
    args = parser.parse_args()
    if not args.live:
        parser.error("pass --live to make four bounded read-only requests to Tutu MCP")
    departure = args.departure or (date.today() + timedelta(days=28))
    return_date = args.return_date or (departure + timedelta(days=7))
    print(json.dumps({"route": {"origin_query": args.origin, "product_coverage": coverage_payload()},
                      "results": asyncio.run(run(departure, return_date, args.origin))},
                     ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
