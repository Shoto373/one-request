"""Hybrid RAG Retrieval Service.

Performs multi-criteria hybrid search combining exact relational filtering
(dates, routes, stars, prices) with semantic text ranking across Ostrovok hotels,
Tutu flights, Tutu trains, and Ostrovok transfers.

Every returned item includes:
- source_url: direct booking/property link for the user
- source_provider: 'ostrovok' or 'tutu'
- source_raw_url: original API/scrape source reference URL
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date
from typing import Any

import psycopg

log = logging.getLogger(__name__)

DEFAULT_DB_URL = os.getenv("DATABASE_URL", "postgresql://app:app@localhost:5432/trip")


def get_db_url() -> str:
    url = os.getenv("DATABASE_URL") or DEFAULT_DB_URL
    # If running locally from host machine and url points to 'db', swap to 'localhost'
    if "@db:" in url and not os.path.exists("/.dockerenv"):
        url = url.replace("@db:", "@localhost:")
    return url


def search_rag_inventory(
    query: str,
    origin_city: str,
    dest_city: str,
    depart_date: date,
    return_date: date | None = None,
    min_stars: int = 0,
    with_pets: bool = False,
    max_price: int | None = None,
    limit: int = 5,
    db_url: str | None = None,
) -> dict[str, Any]:
    """Retrieve verified travel options from PostgreSQL RAG inventory.
    
    Returns structured results with direct booking URLs and provenance citations.
    """
    db_url = db_url or get_db_url()
    results: dict[str, Any] = {
        "query": query,
        "origin_city": origin_city,
        "dest_city": dest_city,
        "depart_date": depart_date.isoformat(),
        "return_date": return_date.isoformat() if return_date else None,
        "hotels": [],
        "flights": [],
        "trains": [],
        "transfers": [],
        "sources": [],
    }

    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                # 1. Search Hotels with Full-Text Semantic Ranking
                # Extract descriptive keywords from query if any
                clean_query = re.sub(r'[^а-яА-Яa-zA-Z0-9\s]+', ' ', query).strip()
                keywords = [w for w in clean_query.split() if len(w) > 3][:5]
                ts_query = " | ".join(keywords) if keywords else ""
                
                hotel_sql = """
                    SELECT id, city_id, name, stars, rating, kind, address,
                           dist_center_km, dist_beach_km, amenities, rooms, tariffs,
                           full_slug, source_url, source_provider, source_raw_url, description
                    FROM inventory_hotels
                    WHERE city_id = %s
                      AND stars >= %s
                """
                params: list[Any] = [dest_city, min_stars]
                if ts_query:
                    hotel_sql += " ORDER BY ts_rank_cd(to_tsvector('russian', name || ' ' || coalesce(address,'') || ' ' || description), to_tsquery('russian', %s)) DESC, rating DESC LIMIT %s"
                    params.extend([ts_query, limit])
                else:
                    hotel_sql += " ORDER BY rating DESC, stars DESC LIMIT %s"
                    params.append(limit)

                try:
                    cur.execute(hotel_sql, params)
                    for row in cur.fetchall():
                        results["hotels"].append({
                            "id": row[0],
                            "city_id": row[1],
                            "name": row[2],
                            "stars": row[3],
                            "rating": float(row[4]),
                            "kind": row[5],
                            "address": row[6],
                            "dist_center_km": float(row[7]) if row[7] is not None else None,
                            "dist_beach_km": float(row[8]) if row[8] is not None else None,
                            "amenities": row[9],
                            "rooms": row[10],
                            "tariffs": row[11],
                            "full_slug": row[12],
                            "source_url": row[13],
                            "source_provider": row[14],
                            "source_raw_url": row[15],
                            "description": row[16],
                        })
                except Exception as e:
                    log.debug("Hotel query with FTS fallback: %s", e)
                    # Simple fallback without FTS query
                    cur.execute(
                        "SELECT id, city_id, name, stars, rating, kind, address, dist_center_km, dist_beach_km, amenities, rooms, tariffs, full_slug, source_url, source_provider, source_raw_url, description FROM inventory_hotels WHERE city_id = %s ORDER BY rating DESC LIMIT %s",
                        (dest_city, limit)
                    )
                    for row in cur.fetchall():
                        results["hotels"].append({
                            "id": row[0], "city_id": row[1], "name": row[2], "stars": row[3],
                            "rating": float(row[4]), "kind": row[5], "address": row[6],
                            "dist_center_km": float(row[7]) if row[7] is not None else None,
                            "dist_beach_km": float(row[8]) if row[8] is not None else None,
                            "amenities": row[9], "rooms": row[10], "tariffs": row[11],
                            "full_slug": row[12], "source_url": row[13], "source_provider": row[14],
                            "source_raw_url": row[15], "description": row[16],
                        })

                # 2. Search Flights on requested date
                flight_sql = """
                    SELECT id, origin_city, dest_city, carrier_name, carrier_code, flight_no,
                           depart_date, depart_time, arrive_time, duration_min, price_rub,
                           baggage, is_direct, source_url, source_provider, source_raw_url
                    FROM inventory_flights
                    WHERE origin_city = %s AND dest_city = %s AND depart_date = %s
                    ORDER BY price_rub ASC LIMIT %s
                """
                cur.execute(flight_sql, (origin_city, dest_city, depart_date, limit))
                for row in cur.fetchall():
                    results["flights"].append({
                        "id": row[0],
                        "origin_city": row[1],
                        "dest_city": row[2],
                        "carrier_name": row[3],
                        "carrier_code": row[4],
                        "flight_no": row[5],
                        "depart_date": row[6].isoformat(),
                        "depart_time": str(row[7]),
                        "arrive_time": str(row[8]),
                        "duration_min": row[9],
                        "price_rub": row[10],
                        "baggage": row[11],
                        "is_direct": row[12],
                        "source_url": row[13],
                        "source_provider": row[14],
                        "source_raw_url": row[15],
                    })

                # 3. Search Trains on requested date
                train_sql = """
                    SELECT id, origin_city, dest_city, train_no, carrier,
                           depart_date, depart_time, arrive_time, duration_min, price_rub,
                           coach_type, source_url, source_provider, source_raw_url
                    FROM inventory_trains
                    WHERE origin_city = %s AND dest_city = %s AND depart_date = %s
                    ORDER BY price_rub ASC LIMIT %s
                """
                cur.execute(train_sql, (origin_city, dest_city, depart_date, limit))
                for row in cur.fetchall():
                    results["trains"].append({
                        "id": row[0],
                        "origin_city": row[1],
                        "dest_city": row[2],
                        "train_no": row[3],
                        "carrier": row[4],
                        "depart_date": row[5].isoformat(),
                        "depart_time": str(row[6]),
                        "arrive_time": str(row[7]),
                        "duration_min": row[8],
                        "price_rub": row[9],
                        "coach_type": row[10],
                        "source_url": row[11],
                        "source_provider": row[12],
                        "source_raw_url": row[13],
                    })

                # 4. Search Transfers
                transf_sql = """
                    SELECT id, origin_city, dest_city, hub_id, distance_km,
                           duration_min, price_rub, car_class, source_url,
                           source_provider, source_raw_url
                    FROM inventory_transfers
                    WHERE origin_city = %s
                    ORDER BY price_rub ASC LIMIT %s
                """
                cur.execute(transf_sql, (origin_city, limit))
                for row in cur.fetchall():
                    results["transfers"].append({
                        "id": row[0],
                        "origin_city": row[1],
                        "dest_city": row[2],
                        "hub_id": row[3],
                        "distance_km": float(row[4]),
                        "duration_min": row[5],
                        "price_rub": row[6],
                        "car_class": row[7],
                        "source_url": row[8],
                        "source_provider": row[9],
                        "source_raw_url": row[10],
                    })

        # Collect unique sources
        seen_urls = set()
        for cat_items in (results["hotels"], results["flights"], results["trains"], results["transfers"]):
            for item in cat_items:
                raw_u = item.get("source_raw_url")
                prov = item.get("source_provider")
                if raw_u and raw_u not in seen_urls:
                    seen_urls.add(raw_u)
                    results["sources"].append({
                        "provider": prov,
                        "raw_url": raw_u,
                        "booking_url": item.get("source_url"),
                    })

    except Exception as e:
        log.warning("RAG database retrieval failed: %s", e)

    return results


def format_rag_sources_markdown(rag_data: dict[str, Any]) -> str:
    """Format markdown block with verified sources and direct booking links for the user."""
    lines = ["\n### 🔗 Прямые ссылки на бронирование и источники данных:"]
    
    # 1. Hotels
    if rag_data.get("hotels"):
        lines.append("\n**🏨 Отели (Островок):**")
        for h in rag_data["hotels"][:4]:
            rating_str = f"★ {h['stars']} (рейтинг {h['rating']})"
            lines.append(f"- [{h['name']} ({rating_str})]({h['source_url']}) — {h['address']}")
            lines.append(f"  *Источник данных: [{h['source_provider'].capitalize()} API]({h['source_raw_url']})*")

    # 2. Flights
    if rag_data.get("flights"):
        lines.append("\n**✈️ Авиабилеты (Туту Авиа):**")
        for f in rag_data["flights"][:3]:
            lines.append(f"- [Рейс {f['flight_no']} ({f['carrier_name']}): вылет {f['depart_time']}, {f['price_rub']} ₽]({f['source_url']})")
            lines.append(f"  *Источник расписания: [Туту API]({f['source_raw_url']})*")

    # 3. Trains
    if rag_data.get("trains"):
        lines.append("\n**🚆 Поезда (Туту Ж/Д):**")
        for t in rag_data["trains"][:3]:
            lines.append(f"- [Поезд № {t['train_no']} ({t['carrier']}): {t['coach_type']}, {t['price_rub']} ₽]({t['source_url']})")
            lines.append(f"  *Источник тарифов: [Туту Ж/Д]({t['source_raw_url']})*")

    # 4. Transfers
    if rag_data.get("transfers"):
        lines.append("\n**🚗 Трансферы (Островок Трансфер):**")
        for tr in rag_data["transfers"][:2]:
            lines.append(f"- [Островок Трансфер {tr['origin_city']} ➔ {tr['dest_city']} ({tr['distance_km']} км, {tr['price_rub']} ₽)]({tr['source_url']})")

    return "\n".join(lines)


def build_sources_summary(state: Any) -> str:
    """Collect direct booking links and source citations from state bundles and PostgreSQL RAG inventory."""
    sources_section = ["### 🔗 Прямые ссылки на бронирование и источники данных:"]
    seen_source_urls = set()
    bundle_hotels = []
    bundle_flights = []
    bundle_trains = []
    bundle_transfers = []

    # 1. Links from current state bundles
    for b in getattr(state, "bundles", []):
        for it in getattr(b, "items", []):
            v = it.variant if isinstance(it.variant, dict) else {}
            legs = v.get("legs", [])
            if legs:
                for leg in legs:
                    leg_url = leg.get("source_url")
                    if leg.get("mode") == "transfer":
                        t_from = leg.get("from", "Город")
                        t_to = leg.get("to", "Хаб")
                        t_price = leg.get("price_rub", 0)
                        t_url = leg_url or "https://ostrovok.ru/transfers/"
                        if t_url not in seen_source_urls:
                            seen_source_urls.add(t_url)
                            bundle_transfers.append(
                                f"- [Островок Трансфер {t_from} ➔ {t_to} ({t_price:,.0f} ₽)]({t_url})\n"
                                f"  *Источник тарифов: [Островок Трансфер API](https://ostrovok.ru/transfer/)*"
                            )
                    else:
                        fl_carrier = leg.get("carrier", "Авиарейс")
                        fl_no = leg.get("flight_no", "")
                        fl_price = leg.get("price_rub", 0)
                        fl_url = leg_url or getattr(it, "source_url", None)
                        if fl_url and fl_url not in seen_source_urls:
                            seen_source_urls.add(fl_url)
                            bundle_flights.append(
                                f"- [Рейс {fl_carrier} {fl_no} ({fl_price:,.0f} ₽)]({fl_url})\n"
                                f"  *Источник расписания: [Туту.ру Авиа API](https://api.tutu.ru/avia/)*"
                            )

            s_url = getattr(it, "source_url", None) or (v.get("booking_url") if isinstance(v, dict) else None)
            if not s_url or s_url in seen_source_urls:
                continue
            seen_source_urls.add(s_url)
            if it.kind == "hotel":
                h_title = v.get("hotel_name") or (it.title.split(",")[0] if it.title else "Отель")
                rating = v.get("rating", 9.0)
                stars = v.get("stars", 4)
                bundle_hotels.append(
                    f"- [{h_title} (★ {stars}, рейтинг {rating})]({s_url})\n"
                    f"  *Источник данных: [Островок Live API](https://api.worldota.net/api/b2b/v3/hotel/info/)*"
                )
            elif it.kind == "flight":
                f_no = v.get("flight_no", "")
                c_name = v.get("carrier_name", "Авиарейс")
                price = f"{it.total_rub:,.0f} ₽".replace(",", " ")
                bundle_flights.append(
                    f"- [Рейс {c_name} {f_no} ({price})]({s_url})\n"
                    f"  *Источник расписания: [Туту.ру Авиа API](https://api.tutu.ru/avia/)*"
                )
            elif it.kind == "train":
                t_no = v.get("train_no", "")
                price = f"{it.total_rub:,.0f} ₽".replace(",", " ")
                bundle_trains.append(
                    f"- [Поезд {t_no} ({price})]({s_url})\n"
                    f"  *Источник расписания: [Туту.ру Ж/Д API](https://api.tutu.ru/trains/)*"
                )
            elif it.kind == "transfer":
                orig_c = v.get("origin_name", getattr(it, "origin_city", "Город"))
                dest_c = v.get("hub_name", getattr(it, "destination_city", "Хаб"))
                price = f"{it.total_rub:,.0f} ₽".replace(",", " ")
                bundle_transfers.append(
                    f"- [Островок Трансфер {orig_c} ➔ {dest_c} ({price})]({s_url})\n"
                    f"  *Источник тарифов: [Островок Трансфер API](https://ostrovok.ru/transfer/)*"
                )

    # 2. Enrich from RAG PostgreSQL inventory
    try:
        from app.planning.query import parse_dates
        requirements = {r.key: r.value for r in getattr(state, "requirements", [])}
        dates_val = requirements.get("dates")
        parsed_d = parse_dates(dates_val) if dates_val else None
        dep_d = parsed_d[0] if parsed_d else date(2026, 12, 30)
        ret_d = parsed_d[1] if parsed_d else date(2027, 1, 6)
        orig_city = str(requirements.get("origin") or "Москва")
        dest_city = str(requirements.get("destination") or "Казань")

        rag_res = search_rag_inventory(
            query=f"{dest_city} отель отдых",
            origin_city=orig_city,
            dest_city=dest_city,
            depart_date=dep_d,
            return_date=ret_d,
            limit=4,
        )
        for h in rag_res.get("hotels", []):
            if h.get("source_url") and h["source_url"] not in seen_source_urls:
                seen_source_urls.add(h["source_url"])
                bundle_hotels.append(
                    f"- [{h['name']} (★ {h['stars']}, рейтинг {h['rating']})]({h['source_url']}) — {h.get('address', '')}\n"
                    f"  *Источник данных: [{h['source_provider'].capitalize()} API]({h['source_raw_url']})*"
                )
        for fl in rag_res.get("flights", []):
            if fl.get("source_url") and fl["source_url"] not in seen_source_urls:
                seen_source_urls.add(fl["source_url"])
                bundle_flights.append(
                    f"- [Рейс {fl['flight_no']} ({fl['carrier_name']}): вылет {fl['depart_time']}, {fl['price_rub']} ₽]({fl['source_url']})\n"
                    f"  *Источник расписания: [Туту API]({fl['source_raw_url']})*"
                )
        for tr in rag_res.get("trains", []):
            if tr.get("source_url") and tr["source_url"] not in seen_source_urls:
                seen_source_urls.add(tr["source_url"])
                bundle_trains.append(
                    f"- [Поезд № {tr['train_no']} ({tr['carrier']}): {tr['coach_type']}, {tr['price_rub']} ₽]({tr['source_url']})\n"
                    f"  *Источник тарифов: [Туту Ж/Д]({tr['source_raw_url']})*"
                )
        for tf in rag_res.get("transfers", []):
            if tf.get("source_url") and tf["source_url"] not in seen_source_urls:
                seen_source_urls.add(tf["source_url"])
                bundle_transfers.append(
                    f"- [Островок Трансфер {tf['origin_city']} ➔ {tf['dest_city']} ({tf['distance_km']} км, {tf['price_rub']} ₽)]({tf['source_url']})\n"
                    f"  *Источник тарифов: [{tf['source_provider'].capitalize()} API]({tf['source_raw_url']})*"
                )
    except Exception as e:
        log.warning("RAG enrichment failed: %s", e)

    if bundle_hotels:
        sources_section.append("\n**🏨 Отели (Островок):**\n" + "\n".join(bundle_hotels[:5]))
    if bundle_flights:
        sources_section.append("\n**✈️ Авиабилеты (Туту Авиа):**\n" + "\n".join(bundle_flights[:4]))
    if bundle_trains:
        sources_section.append("\n**🚆 Поезда (Туту Ж/Д):**\n" + "\n".join(bundle_trains[:3]))
    if bundle_transfers:
        sources_section.append("\n**🚗 Трансферы (Островок Трансфер):**\n" + "\n".join(bundle_transfers[:3]))

    return "\n".join(sources_section)
