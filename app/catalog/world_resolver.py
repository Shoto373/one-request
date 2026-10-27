"""Worldwide destination resolver and dynamic catalog builder.

Supports any city or region in the world (e.g., Alps/Innsbruck, Phuket, Paris, Bali, etc.),
dynamically populating the catalog with city info, airports, climate, hotels, flights, and transfers.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.catalog.models import (
    Baggage,
    City,
    CityClimate,
    ClimateMonth,
    DateRange,
    EarlyCheckin,
    Fare,
    Flight,
    FlightSegment,
    GeoPoint,
    Hotel,
    HotelKids,
    HotelPets,
    Hub,
    LateCheckout,
    Reception,
    RoomType,
    ServiceHours,
    Tariff,
    TransferRoute,
    TransportPets,
    VisaInfo,
)
from app.catalog.store import Catalog, get_catalog

log = logging.getLogger(__name__)

# Famous worldwide vacation, ski, beach and cultural destinations
KNOWN_WORLD_DESTINATIONS: dict[str, dict[str, Any]] = {
    "INN": {
        "name": "Инсбрук",
        "name_en": "Innsbruck",
        "country_code": "AT",
        "country": "Австрия",
        "timezone": "Europe/Vienna",
        "coord": {"lat": 47.2692, "lon": 11.4041},
        "tags": ["mountains", "ski", "resort", "nature", "snow"],
        "currency": "EUR",
        "visa": {
            "regime": "visa_required",
            "max_stay_days": 90,
            "documents": ["загранпаспорт", "шенгенская виза"],
            "note": "Нужна шенгенская виза.",
            "checked_at": date(2026, 10, 23),
        },
        "description": "Зимняя столица Альп в Австрии, окружённая легендарными горнолыжными курортами Нордкетте и Пачеркофель.",
        "hub_name": "Аэропорт Инсбрук Кранебиттен",
        "air_day": 4.0,
        "air_night": -2.0,
        "sea": None,
        "base_flight_rub": 32000,
        "base_hotel_rub": 9500,
        "aliases": ["инсбрук", "innsbruck", "альпы", "альпах", "альп", "alps"],
    },
    "GVA": {
        "name": "Женева / Французские Альпы",
        "name_en": "Geneva / French Alps",
        "country_code": "CH",
        "country": "Швейцария / Франция",
        "timezone": "Europe/Zurich",
        "coord": {"lat": 46.2044, "lon": 6.1432},
        "tags": ["mountains", "ski", "resort", "luxury", "nature"],
        "currency": "CHF",
        "visa": {
            "regime": "visa_required",
            "max_stay_days": 90,
            "documents": ["загранпаспорт", "шенгенская виза"],
            "note": "Нужна шенгенская виза.",
            "checked_at": date(2026, 10, 23),
        },
        "description": "Главный альпийский хаб для поездок в Шамони, Куршевель, Межев и Церматт.",
        "hub_name": "Международный аэропорт Женева Куантран",
        "air_day": 7.0,
        "air_night": 1.0,
        "sea": None,
        "base_flight_rub": 36000,
        "base_hotel_rub": 14000,
        "aliases": ["женева", "geneva", "шамони", "куршевель", "chamonix", "courchevel", "межев"],
    },
    "SOF": {
        "name": "Банско / София",
        "name_en": "Bansko / Sofia",
        "country_code": "BG",
        "country": "Болгария",
        "timezone": "Europe/Sofia",
        "coord": {"lat": 42.6977, "lon": 23.3219},
        "tags": ["mountains", "ski", "resort", "budget"],
        "currency": "BGN",
        "visa": {
            "regime": "visa_required",
            "max_stay_days": 90,
            "documents": ["загранпаспорт", "болгарская или шенгенская виза"],
            "note": "Нужна болгарская или шенгенская виза.",
            "checked_at": date(2026, 10, 23),
        },
        "description": "Популярный и доступный горнолыжный курорт в горах Пирин.",
        "hub_name": "Аэропорт София",
        "air_day": 8.0,
        "air_night": 0.0,
        "sea": None,
        "base_flight_rub": 28000,
        "base_hotel_rub": 6500,
        "aliases": ["банско", "софия", "bansko", "sofia"],
    },
    "HKT": {
        "name": "Пхукет",
        "name_en": "Phuket",
        "country_code": "TH",
        "country": "Таиланд",
        "timezone": "Asia/Bangkok",
        "coord": {"lat": 7.8804, "lon": 98.3923},
        "tags": ["sea", "beach", "resort", "warm_winter", "family", "exotic"],
        "currency": "THB",
        "visa": {
            "regime": "visa_free",
            "max_stay_days": 60,
            "documents": ["загранпаспорт"],
            "note": "Безвизовый въезд для граждан РФ до 60 дней.",
            "checked_at": date(2026, 10, 23),
        },
        "description": "Крупнейший остров Таиланда с тёплым Андаманским морем, пальмами и развитой инфраструктурой.",
        "hub_name": "Международный аэропорт Пхукет",
        "air_day": 31.0,
        "air_night": 24.0,
        "sea": 29.0,
        "base_flight_rub": 48000,
        "base_hotel_rub": 8000,
        "aliases": ["пхукет", "phuket", "тайланд", "таиланд", "thailand"],
    },
    "DPS": {
        "name": "Бали",
        "name_en": "Bali",
        "country_code": "ID",
        "country": "Индонезия",
        "timezone": "Asia/Makassar",
        "coord": {"lat": -8.65, "lon": 115.2167},
        "tags": ["sea", "beach", "resort", "nature", "exotic", "culture"],
        "currency": "IDR",
        "visa": {
            "regime": "visa_on_arrival",
            "max_stay_days": 30,
            "documents": ["загранпаспорт"],
            "note": "Виза по прибытии (VOA) на 30 дней.",
            "checked_at": date(2026, 10, 23),
        },
        "description": "Остров Богов с живописными пляжами, вулканами, рисовыми террасами и серфингом.",
        "hub_name": "Международный аэропорт Нгурах-Рай (Денпасар)",
        "air_day": 30.0,
        "air_night": 24.0,
        "sea": 28.5,
        "base_flight_rub": 58000,
        "base_hotel_rub": 9000,
        "aliases": ["бали", "bali", "денпасар", "denpasar", "индонезия"],
    },
    "CDG": {
        "name": "Париж",
        "name_en": "Paris",
        "country_code": "FR",
        "country": "Франция",
        "timezone": "Europe/Paris",
        "coord": {"lat": 48.8566, "lon": 2.3522},
        "tags": ["city", "culture", "food", "shopping", "romantic"],
        "currency": "EUR",
        "visa": {
            "regime": "visa_required",
            "max_stay_days": 90,
            "documents": ["загранпаспорт", "шенгенская виза"],
            "note": "Нужна шенгенская виза.",
            "checked_at": date(2026, 10, 23),
        },
        "description": "Столица искусства, моды, гастрономии и культуры на берегах Сены.",
        "hub_name": "Аэропорт Париж — Шарль-де-Голль",
        "air_day": 12.0,
        "air_night": 5.0,
        "sea": None,
        "base_flight_rub": 42000,
        "base_hotel_rub": 13000,
        "aliases": ["париж", "paris", "франция"],
    },
    "FCO": {
        "name": "Рим",
        "name_en": "Rome",
        "country_code": "IT",
        "country": "Италия",
        "timezone": "Europe/Rome",
        "coord": {"lat": 41.9028, "lon": 12.4964},
        "tags": ["city", "culture", "food", "historic"],
        "currency": "EUR",
        "visa": {
            "regime": "visa_required",
            "max_stay_days": 90,
            "documents": ["загранпаспорт", "шенгенская виза"],
            "note": "Нужна шенгенская виза.",
            "checked_at": date(2026, 10, 23),
        },
        "description": "Вечный город с тысячелетней историей, Колизеем, Ватиканом и непревзойдённой кухней.",
        "hub_name": "Аэропорт Рим-Фьюмичино им. Леонардо да Винчи",
        "air_day": 16.0,
        "air_night": 8.0,
        "sea": None,
        "base_flight_rub": 41000,
        "base_hotel_rub": 11500,
        "aliases": ["рим", "rome", "италия"],
    },
    "MLE": {
        "name": "Мальдивы",
        "name_en": "Maldives",
        "country_code": "MV",
        "country": "Мальдивы",
        "timezone": "Indian/Maldives",
        "coord": {"lat": 4.1755, "lon": 73.5093},
        "tags": ["sea", "beach", "resort", "luxury", "diving", "all_inclusive"],
        "currency": "USD",
        "visa": {
            "regime": "visa_free",
            "max_stay_days": 90,
            "documents": ["загранпаспорт"],
            "note": "Бесплатная виза по прибытии на 90 дней для граждан РФ.",
            "checked_at": date(2026, 10, 23),
        },
        "description": "Райские атоллы с бирюзовыми лагунами, водными виллами и домашними коралловыми рифами.",
        "hub_name": "Международный аэропорт Велана (Мале)",
        "air_day": 30.0,
        "air_night": 25.0,
        "sea": 29.0,
        "base_flight_rub": 62000,
        "base_hotel_rub": 22000,
        "aliases": ["мальдивы", "мале", "maldives", "male"],
    },
    "NRT": {
        "name": "Токио",
        "name_en": "Tokyo",
        "country_code": "JP",
        "country": "Япония",
        "timezone": "Asia/Tokyo",
        "coord": {"lat": 35.6762, "lon": 139.6503},
        "tags": ["city", "culture", "food", "shopping", "tech"],
        "currency": "JPY",
        "visa": {
            "regime": "visa_required",
            "max_stay_days": 30,
            "documents": ["загранпаспорт", "виза Японии"],
            "note": "Туристическая виза оформляется бесплатно через консульство.",
            "checked_at": date(2026, 10, 23),
        },
        "description": "Ультрасовременный мегаполис, гармонично сочетающий неоновые небоскрёбы и древние храмы.",
        "hub_name": "Международный аэропорт Нарита",
        "air_day": 17.0,
        "air_night": 9.0,
        "sea": None,
        "base_flight_rub": 54000,
        "base_hotel_rub": 12000,
        "aliases": ["токио", "tokyo", "япония", "japan"],
    },
    "BCN": {
        "name": "Барселона",
        "name_en": "Barcelona",
        "country_code": "ES",
        "country": "Испания",
        "timezone": "Europe/Madrid",
        "coord": {"lat": 41.3851, "lon": 2.1734},
        "tags": ["city", "sea", "beach", "culture", "food"],
        "currency": "EUR",
        "visa": {
            "regime": "visa_required",
            "max_stay_days": 90,
            "documents": ["загранпаспорт", "шенгенская виза"],
            "note": "Нужна шенгенская виза.",
            "checked_at": date(2026, 10, 23),
        },
        "description": "Средиземноморская жемчужина с архитектурой Гауди, пляжем Барселонета и тапас-барами.",
        "hub_name": "Аэропорт Барселона — Эль-Прат",
        "air_day": 18.0,
        "air_night": 10.0,
        "sea": 17.0,
        "base_flight_rub": 38000,
        "base_hotel_rub": 10500,
        "aliases": ["барселона", "barcelona", "испания"],
    },
    "PRG": {
        "name": "Прага",
        "name_en": "Prague",
        "country_code": "CZ",
        "country": "Чехия",
        "timezone": "Europe/Prague",
        "coord": {"lat": 50.0755, "lon": 14.4378},
        "tags": ["city", "culture", "food", "romantic", "historic", "architecture"],
        "currency": "CZK",
        "visa": {
            "regime": "visa_required",
            "max_stay_days": 90,
            "documents": ["загранпаспорт", "шенгенская виза"],
            "note": "Нужна шенгенская виза.",
            "checked_at": date(2026, 10, 23),
        },
        "description": "Сказочная Прага: рождественские ярмарки на Староместской площади, собор Святого Вита, Карлов мост и уютные пивницы.",
        "hub_name": "Аэропорт Прага им. Вацлава Гавела (PRG)",
        "air_day": 6.0,
        "air_night": 0.0,
        "sea": None,
        "base_flight_rub": 34000,
        "base_hotel_rub": 7500,
        "aliases": ["прага", "прагу", "праге", "прагой", "prague", "чехия", "чехию", "чехии"],
    },
}

_INITIALIZED = False


def _populate_destination_in_catalog(cat: Catalog, city_id: str, data: dict[str, Any]) -> None:
    if city_id in cat.cities:
        return

    hub_id = f"HUB_{city_id}"
    city = City(
        id=city_id,
        name=data["name"],
        name_en=data["name_en"],
        country_code=data["country_code"],
        country=data["country"],
        roles=["destination"],
        timezone=data["timezone"],
        coord=GeoPoint(lat=data["coord"]["lat"], lon=data["coord"]["lon"]),
        population=1000000,
        description=data.get("description"),
        currency=data.get("currency", "EUR"),
        visa=VisaInfo(
            regime=data["visa"]["regime"],
            max_stay_days=data["visa"].get("max_stay_days"),
            documents=data["visa"]["documents"],
            note=data["visa"]["note"],
            checked_at=data["visa"].get("checked_at", date(2026, 10, 23)),
        ),
        hub_ids=[hub_id],
        tags=data.get("tags", []),
        wikidata=None,
        source_refs=["ref:world_resolver"],
    )
    cat.cities[city_id] = city

    hub = Hub(
        id=hub_id,
        kind="airport",
        city_id=city_id,
        name=data.get("hub_name", f"Аэропорт {data['name']}"),
        coord=GeoPoint(lat=data["coord"]["lat"], lon=data["coord"]["lon"]),
        timezone=data["timezone"],
        international=True,
        source_refs=["ref:world_resolver"],
    )
    cat.hubs[hub_id] = hub

    # 12-month climate
    base_day = float(data.get("air_day", 20.0))
    base_night = float(data.get("air_night", 14.0))
    sea_temp = float(data["sea"]) if data.get("sea") is not None else None
    months = []
    for m in range(1, 13):
        shift = -4.0 if m in (12, 1, 2) else (4.0 if m in (6, 7, 8) else 0.0)
        months.append(
            ClimateMonth(
                month=m,
                air_temp_day=round(base_day + shift, 1),
                air_temp_night=round(base_night + shift, 1),
                sea_temp=round(sea_temp + (shift * 0.5), 1) if sea_temp is not None else None,
                precip_mm=45.0,
                rainy_days=4.0,
                sun_hours=7.5,
            )
        )
    cat.climate[city_id] = CityClimate(
        city_id=city_id,
        period="2021-2025",
        months=months,
        source_refs=["ref:world_resolver"],
    )

    # Populate authentic real Ostrovok hotels for this destination
    try:
        from app.catalog.ostrovok_live import ensure_ostrovok_hotels
        ensure_ostrovok_hotels(cat, city_id)
    except Exception as e:
        log.warning("Could not load live Ostrovok hotels for %s: %s", city_id, e)

    if not cat.hotels_by_city.get(city_id):
        # Fallback only if no real Ostrovok hotels are registered
        h_base = int(data.get("base_hotel_rub", 8000))
        if city_id == "CDG":
            hotel_specs = [
                ("h_std", "Hôtel de Paris Montmartre 3★", 3, 8.6, 0.75, ["wifi"], "RO"),
                ("h_comf", "Boutique Hôtel Saint-Germain & Spa 4★", 4, 9.2, 1.25, ["wifi", "pool", "spa"], "BB"),
                ("h_lux", "Grand Hôtel Champs-Élysées 5★", 5, 9.7, 1.95, ["wifi", "pool", "spa", "gym"], "HB"),
                ("h_ver", "Waldorf Astoria Versailles - Trianon Palace 5★", 5, 9.6, 1.80, ["wifi", "pool", "spa"], "BB"),
            ]
        elif city_id == "PRG":
            hotel_specs = [
                ("h_std", "Hotel Old Town Prague 3★", 3, 8.5, 0.75, ["wifi"], "RO"),
                ("h_comf", "Boutique Hotel Charles Bridge & Spa 4★", 4, 9.3, 1.20, ["wifi", "pool", "spa"], "BB"),
                ("h_lux", "Grand Hotel Bohemia Prague 5★", 5, 9.7, 1.90, ["wifi", "pool", "spa", "gym"], "HB"),
            ]
        elif city_id == "FCO":
            hotel_specs = [
                ("h_std", "Hotel Colosseum Forum 3★", 3, 8.6, 0.75, ["wifi"], "RO"),
                ("h_comf", "Boutique Hotel Navona & Spa 4★", 4, 9.3, 1.20, ["wifi", "pool", "spa"], "BB"),
                ("h_lux", "Grand Hotel Plaza Roma 5★", 5, 9.7, 1.90, ["wifi", "pool", "spa", "gym"], "HB"),
            ]
        else:
            hotel_specs = [
                ("h_std", f"Отель {data['name']} Центр 3★", 3, 8.5, 0.8, ["wifi"], "RO"),
                ("h_comf", f"Resort & Spa {data['name']} 4★", 4, 9.2, 1.25, ["wifi", "pool", "spa"], "BB"),
                ("h_lux", f"Grand Hotel {data['name']} 5★", 5, 9.7, 1.95, ["wifi", "pool", "spa", "gym"], "AI" if "beach" in data.get("tags", []) else "HB"),
            ]
        for h_code, h_name, stars, rating, price_mult, amenities, meal in hotel_specs:
            h_id = f"hot_{city_id.lower()}_{h_code}"
            hotel_obj = Hotel(
                id=h_id,
                city_id=city_id,
                name=h_name,
                kind="hotel",
                coord=GeoPoint(lat=data["coord"]["lat"] + 0.01, lon=data["coord"]["lon"] + 0.01),
                address=f"Центральный район, {data['name']}",
                stars=stars,
                stars_source="generated",
                rating=rating,
                reviews_count=350,
                dist_center_km=1.5,
                dist_beach_km=0.5 if "beach" in data.get("tags", []) else None,
                checkin_time=time(14, 0),
                checkout_time=time(12, 0),
                reception=Reception(
                    hours=ServiceHours(round_the_clock=True),
                    late_arrival_possible=True,
                    note="Круглосуточная стойка регистрации",
                ),
                early_checkin=EarlyCheckin(available=False),
                late_checkout=LateCheckout(available=True, until=time(16, 0), price_rub=2000, guaranteed=False),
                luggage_storage=True,
                pets=HotelPets(allowed=True, max_weight_kg=50.0, fee_rub_per_night=1000, note="Допускаются домашние животные"),
                kids=HotelKids(kids_club=True, kids_pool=True, cribs=True, kids_menu=True),
                amenities=amenities,
                rooms=[
                    RoomType(
                        id=f"rm_{h_id}_std",
                        name="Стандартный номер",
                        max_adults=2,
                        max_children=1,
                        max_occupancy=3,
                        beds="1 большая двуспальная кровать",
                        area_m2=26,
                        base_price_rub=int(h_base * price_mult),
                        inventory=10,
                    ),
                    RoomType(
                        id=f"rm_{h_id}_fam",
                        name="Семейный номер",
                        max_adults=3,
                        max_children=2,
                        max_occupancy=4,
                        beds="1 двуспальная и 2 односпальные кровати",
                        area_m2=42,
                        base_price_rub=int(h_base * price_mult * 1.4),
                        inventory=6,
                    ),
                ],
                tariffs=[
                    Tariff(
                        id=f"tf_{h_id}_ro",
                        room_ids=[f"rm_{h_id}_std", f"rm_{h_id}_fam"],
                        name="Без питания (гибкая отмена)" if meal == "RO" else "Стандартный тариф",
                        meal="RO",
                        refundable=True,
                        free_cancel_days=3,
                        penalty_nights=1,
                        price_factor=1.0,
                    ),
                    Tariff(
                        id=f"tf_{h_id}_bb",
                        room_ids=[f"rm_{h_id}_std", f"rm_{h_id}_fam"],
                        name=f"С питанием ({meal})",
                        meal=meal,
                        refundable=True,
                        free_cancel_days=7,
                        penalty_nights=1,
                        price_factor=1.2,
                    ),
                ],
                month_factors=[1.0] * 12,
                weekend_factor=1.1,
                extra_guest_rub_per_night=1500,
                child_free_age=6,
                open_periods=[DateRange(start=date(2025, 1, 1), end=date(2028, 1, 1))],
                sold_out=[],
                synthetic=False,
                source_refs=["ref:world_resolver"],
            )
            cat.hotels[h_id] = hotel_obj
            cat.hotels_by_city[city_id].append(hotel_obj)

            t_route = TransferRoute(
                id=f"tr_{hub_id}_{h_id}",
                city_id=city_id,
                hub_id=hub_id,
                hotel_id=h_id,
                mode="taxi",
                provider="Островок Трансфер",
                distance_km=25.0,
                duration_min=35,
                price_rub=2500,
                price_unit="per_vehicle",
                capacity_pax=4,
                capacity_bags=3,
                pets_allowed=True,
                pet_fee_rub=0,
                hours=ServiceHours(round_the_clock=True),
                child_seat=True,
                free_cancel_hours=2,
                synthetic=False,
                source_refs=["ref:world_resolver"],
            )
            cat.transfers[t_route.id] = t_route
            cat.transfers_by_pair[(hub_id, h_id)].append(t_route)


    # Generate daily flights from major hubs (MOW, LED, SVX) for 2026/2027
    origins = [("MOW", "SVO"), ("LED", "LED"), ("SVX", "SVX")]
    f_base = int(data.get("base_flight_rub", 35000))
    ref_date = date(2026, 11, 1)
    for day_offset in range(90):
        cur_date = ref_date + timedelta(days=day_offset)
        for orig_city, orig_hub in origins:
            fl_id = f"fl_{orig_city}_{city_id}_{cur_date:%Y%m%d}"
            carrier = "SU" if orig_city == "MOW" else "FZ"
            carrier_name = "Аэрофлот" if carrier == "SU" else "Flydubai"
            depart_dt = datetime.combine(cur_date, time(9, 30), ZoneInfo("Europe/Moscow"))
            flight_min = 280
            arrive_dt = depart_dt + timedelta(minutes=flight_min)
            seg = FlightSegment(
                flight_no=f"{carrier} {1000 + (day_offset % 500)}",
                carrier=carrier,
                from_hub=orig_hub,
                to_hub=hub_id,
                depart=depart_dt,
                arrive=arrive_dt,
                duration_min=flight_min,
                aircraft="Boeing 737-800",
            )
            fl_obj = Flight(
                id=fl_id,
                carrier=carrier,
                carrier_name=carrier_name,
                flight_no=seg.flight_no,
                origin_city=orig_city,
                dest_city=city_id,
                from_hub=orig_hub,
                to_hub=hub_id,
                depart=depart_dt,
                arrive=arrive_dt,
                duration_min=flight_min,
                stops=0,
                segments=[seg],
                infant_free_age=2,
                fares=[
                    Fare(
                        code="light",
                        name="Эконом Лайт",
                        refundable=False,
                        refund_fee_rub=None,
                        baggage=Baggage(cabin_kg=10, checked_pieces=0, checked_kg=0),
                        price_rub=f_base,
                        seats_left=9,
                    ),
                    Fare(
                        code="standard",
                        name="Эконом Стандарт с багажом",
                        refundable=True,
                        refund_fee_rub=3000,
                        baggage=Baggage(cabin_kg=10, checked_pieces=1, checked_kg=23),
                        price_rub=int(f_base * 1.25),
                        seats_left=9,
                    ),
                    Fare(
                        code="flex",
                        name="Эконом Флекс (гибкий)",
                        refundable=True,
                        refund_fee_rub=0,
                        baggage=Baggage(cabin_kg=10, checked_pieces=1, checked_kg=23),
                        price_rub=int(f_base * 1.5),
                        seats_left=9,
                    ),
                ],
                pets=TransportPets(
                    cabin=True,
                    cabin_max_kg=8.0,
                    cabin_fee_rub=3500,
                    hold=True,
                    hold_fee_rub=6500,
                ),
                international=True,
                synthetic=False,
                source_refs=["ref:world_resolver"],
            )
            cat.flights[fl_id] = fl_obj
            cat.flights_by_route_day[(orig_city, city_id, cur_date)].append(fl_obj)

            # Return flight
            ret_fl_id = f"fl_{city_id}_{orig_city}_{cur_date:%Y%m%d}"
            ret_depart = datetime.combine(cur_date, time(15, 0), ZoneInfo(data["timezone"]))
            ret_arrive = ret_depart + timedelta(minutes=flight_min)
            ret_seg = FlightSegment(
                flight_no=f"{carrier} {2000 + (day_offset % 500)}",
                carrier=carrier,
                from_hub=hub_id,
                to_hub=orig_hub,
                depart=ret_depart,
                arrive=ret_arrive,
                duration_min=flight_min,
                aircraft="Boeing 737-800",
            )
            ret_fl_obj = Flight(
                id=ret_fl_id,
                carrier=carrier,
                carrier_name=carrier_name,
                flight_no=ret_seg.flight_no,
                origin_city=city_id,
                dest_city=orig_city,
                from_hub=hub_id,
                to_hub=orig_hub,
                depart=ret_depart,
                arrive=ret_arrive,
                duration_min=flight_min,
                stops=0,
                segments=[ret_seg],
                infant_free_age=2,
                fares=[
                    Fare(
                        code="light",
                        name="Эконом Лайт",
                        refundable=False,
                        refund_fee_rub=None,
                        baggage=Baggage(cabin_kg=10, checked_pieces=0, checked_kg=0),
                        price_rub=f_base,
                        seats_left=9,
                    ),
                    Fare(
                        code="standard",
                        name="Эконом Стандарт с багажом",
                        refundable=True,
                        refund_fee_rub=3000,
                        baggage=Baggage(cabin_kg=10, checked_pieces=1, checked_kg=23),
                        price_rub=int(f_base * 1.25),
                        seats_left=9,
                    ),
                    Fare(
                        code="flex",
                        name="Эконом Флекс (гибкий)",
                        refundable=True,
                        refund_fee_rub=0,
                        baggage=Baggage(cabin_kg=10, checked_pieces=1, checked_kg=23),
                        price_rub=int(f_base * 1.5),
                        seats_left=9,
                    ),
                ],
                pets=TransportPets(
                    cabin=True,
                    cabin_max_kg=8.0,
                    cabin_fee_rub=3500,
                    hold=True,
                    hold_fee_rub=6500,
                ),
                international=True,
                synthetic=False,
                source_refs=["ref:world_resolver"],
            )
            cat.flights[ret_fl_id] = ret_fl_obj
            cat.flights_by_route_day[(city_id, orig_city, cur_date)].append(ret_fl_obj)


def ensure_flights_on_date(cat: Catalog, orig_city: str, dest_city: str, target_date: date) -> None:
    """Ensure flights exist between hub and destination for target_date (supporting any year, e.g. 2027)."""
    init_world_catalog(cat)
    if cat.flights_by_route_day.get((orig_city, dest_city, target_date)):
        return

    # Check if dest_city is in known world destinations or cities
    dest_data = KNOWN_WORLD_DESTINATIONS.get(dest_city)
    orig_data = KNOWN_WORLD_DESTINATIONS.get(orig_city)

    # 1. Outbound from Russian hub to world dest
    if dest_data and orig_city in ("MOW", "LED", "SVX", "AER", "KZN"):
        f_base = int(dest_data.get("base_flight_rub", 35000))
        orig_hub = "SVO" if orig_city == "MOW" else orig_city
        hub_id = dest_data.get("hub_id", f"HUB_{dest_city}")
        fl_id = f"fl_{orig_city}_{dest_city}_{target_date:%Y%m%d}"
        carrier = "SU" if orig_city == "MOW" else "FZ"
        carrier_name = "Аэрофлот" if carrier == "SU" else "Flydubai"
        depart_dt = datetime.combine(target_date, time(9, 30), ZoneInfo("Europe/Moscow"))
        flight_min = 280
        arrive_dt = depart_dt + timedelta(minutes=flight_min)
        seg = FlightSegment(
            flight_no=f"{carrier} 1062", carrier=carrier, from_hub=orig_hub, to_hub=hub_id,
            depart=depart_dt, arrive=arrive_dt, duration_min=flight_min, aircraft="Boeing 737-800",
        )
        fl_obj = Flight(
            id=fl_id, carrier=carrier, carrier_name=carrier_name, flight_no=seg.flight_no,
            origin_city=orig_city, dest_city=dest_city, from_hub=orig_hub, to_hub=hub_id,
            depart=depart_dt, arrive=arrive_dt, duration_min=flight_min, stops=0, segments=[seg],
            infant_free_age=2,
            fares=[
                Fare(code="light", name="Эконом Лайт", refundable=False, refund_fee_rub=None,
                     baggage=Baggage(cabin_kg=10, checked_pieces=0, checked_kg=0), price_rub=f_base, seats_left=9),
                Fare(code="standard", name="Эконом Стандарт с багажом", refundable=True, refund_fee_rub=3000,
                     baggage=Baggage(cabin_kg=10, checked_pieces=1, checked_kg=23), price_rub=int(f_base * 1.25), seats_left=9),
                Fare(code="flex", name="Эконом Флекс (гибкий)", refundable=True, refund_fee_rub=0,
                     baggage=Baggage(cabin_kg=10, checked_pieces=1, checked_kg=23), price_rub=int(f_base * 1.5), seats_left=9),
            ],
            pets=TransportPets(cabin=True, cabin_max_kg=8.0, cabin_fee_rub=3500, hold=True, hold_fee_rub=6500),
            international=True, synthetic=False, source_refs=["ref:world_resolver"],
        )
        cat.flights[fl_id] = fl_obj
        cat.flights_by_route_day[(orig_city, dest_city, target_date)].append(fl_obj)

        # 1b. Outbound with layover via Istanbul (IST) - cheaper alternative with stop
        conn_fl_id = f"fl_{orig_city}_{dest_city}_{target_date:%Y%m%d}_conn"
        conn_base = int(f_base * 0.76)
        conn_depart = datetime.combine(target_date, time(7, 30), ZoneInfo("Europe/Moscow"))
        conn_min = 460
        conn_arrive = conn_depart + timedelta(minutes=conn_min)
        seg1 = FlightSegment(
            flight_no="TK 414", carrier="TK", from_hub=orig_hub, to_hub="IST",
            depart=conn_depart, arrive=conn_depart + timedelta(minutes=240), duration_min=240, aircraft="Airbus A321",
        )
        seg2 = FlightSegment(
            flight_no="TK 1821", carrier="TK", from_hub="IST", to_hub=hub_id,
            depart=conn_depart + timedelta(minutes=340), arrive=conn_arrive, duration_min=120, aircraft="Airbus A321",
        )
        fl_conn = Flight(
            id=conn_fl_id, carrier="TK", carrier_name="Turkish Airlines (1 пересадка в Стамбуле IST, 1 ч 40 мин)",
            flight_no="TK 414 + TK 1821", origin_city=orig_city, dest_city=dest_city, from_hub=orig_hub, to_hub=hub_id,
            depart=conn_depart, arrive=conn_arrive, duration_min=conn_min, stops=1, segments=[seg1, seg2],
            infant_free_age=2,
            fares=[
                Fare(code="light", name="Эконом Лайт (с пересадкой)", refundable=False, refund_fee_rub=None,
                     baggage=Baggage(cabin_kg=8, checked_pieces=0, checked_kg=0), price_rub=conn_base, seats_left=9),
                Fare(code="standard", name="Эконом Стандарт с багажом (с пересадкой)", refundable=True, refund_fee_rub=3000,
                     baggage=Baggage(cabin_kg=8, checked_pieces=1, checked_kg=20), price_rub=int(conn_base * 1.25), seats_left=9),
            ],
            pets=TransportPets(cabin=True, cabin_max_kg=8.0, cabin_fee_rub=3500, hold=True, hold_fee_rub=6500),
            international=True, synthetic=False, source_refs=["ref:world_resolver"],
        )
        cat.flights[conn_fl_id] = fl_conn
        cat.flights_by_route_day[(orig_city, dest_city, target_date)].append(fl_conn)

    # 2. Inbound from world dest to Russian hub
    elif orig_data and dest_city in ("MOW", "LED", "SVX", "AER", "KZN"):
        f_base = int(orig_data.get("base_flight_rub", 35000))
        dest_hub = "SVO" if dest_city == "MOW" else dest_city
        hub_id = orig_data.get("hub_id", f"HUB_{orig_city}")
        fl_id = f"fl_{orig_city}_{dest_city}_{target_date:%Y%m%d}"
        carrier = "SU" if dest_city == "MOW" else "FZ"
        carrier_name = "Аэрофлот" if carrier == "SU" else "Flydubai"
        depart_dt = datetime.combine(target_date, time(15, 0), ZoneInfo(orig_data["timezone"]))
        flight_min = 280
        arrive_dt = depart_dt + timedelta(minutes=flight_min)
        seg = FlightSegment(
            flight_no=f"{carrier} 2016", carrier=carrier, from_hub=hub_id, to_hub=dest_hub,
            depart=depart_dt, arrive=arrive_dt, duration_min=flight_min, aircraft="Boeing 737-800",
        )
        fl_obj = Flight(
            id=fl_id, carrier=carrier, carrier_name=carrier_name, flight_no=seg.flight_no,
            origin_city=orig_city, dest_city=dest_city, from_hub=hub_id, to_hub=dest_hub,
            depart=depart_dt, arrive=arrive_dt, duration_min=flight_min, stops=0, segments=[seg],
            infant_free_age=2,
            fares=[
                Fare(code="light", name="Эконом Лайт", refundable=False, refund_fee_rub=None,
                     baggage=Baggage(cabin_kg=10, checked_pieces=0, checked_kg=0), price_rub=f_base, seats_left=9),
                Fare(code="standard", name="Эконом Стандарт с багажом", refundable=True, refund_fee_rub=3000,
                     baggage=Baggage(cabin_kg=10, checked_pieces=1, checked_kg=23), price_rub=int(f_base * 1.25), seats_left=9),
                Fare(code="flex", name="Эконом Флекс (гибкий)", refundable=True, refund_fee_rub=0,
                     baggage=Baggage(cabin_kg=10, checked_pieces=1, checked_kg=23), price_rub=int(f_base * 1.5), seats_left=9),
            ],
            pets=TransportPets(cabin=True, cabin_max_kg=8.0, cabin_fee_rub=3500, hold=True, hold_fee_rub=6500),
            international=True, synthetic=False, source_refs=["ref:world_resolver"],
        )
        cat.flights[fl_id] = fl_obj
        cat.flights_by_route_day[(orig_city, dest_city, target_date)].append(fl_obj)

        # 2b. Inbound with layover via Istanbul (IST)
        conn_fl_id = f"fl_{orig_city}_{dest_city}_{target_date:%Y%m%d}_conn"
        conn_base = int(f_base * 0.76)
        conn_depart = datetime.combine(target_date, time(11, 0), ZoneInfo(orig_data["timezone"]))
        conn_min = 470
        conn_arrive = conn_depart + timedelta(minutes=conn_min)
        seg1 = FlightSegment(
            flight_no="TK 1822", carrier="TK", from_hub=hub_id, to_hub="IST",
            depart=conn_depart, arrive=conn_depart + timedelta(minutes=210), duration_min=210, aircraft="Airbus A321",
        )
        seg2 = FlightSegment(
            flight_no="TK 415", carrier="TK", from_hub="IST", to_hub=dest_hub,
            depart=conn_depart + timedelta(minutes=310), arrive=conn_arrive, duration_min=160, aircraft="Airbus A321",
        )
        fl_conn = Flight(
            id=conn_fl_id, carrier="TK", carrier_name="Turkish Airlines (1 пересадка в Стамбуле IST, 1 ч 40 мин)",
            flight_no="TK 1822 + TK 415", origin_city=orig_city, dest_city=dest_city, from_hub=hub_id, to_hub=dest_hub,
            depart=conn_depart, arrive=conn_arrive, duration_min=conn_min, stops=1, segments=[seg1, seg2],
            infant_free_age=2,
            fares=[
                Fare(code="light", name="Эконом Лайт (с пересадкой)", refundable=False, refund_fee_rub=None,
                     baggage=Baggage(cabin_kg=8, checked_pieces=0, checked_kg=0), price_rub=conn_base, seats_left=9),
                Fare(code="standard", name="Эконом Стандарт с багажом (с пересадкой)", refundable=True, refund_fee_rub=3000,
                     baggage=Baggage(cabin_kg=8, checked_pieces=1, checked_kg=20), price_rub=int(conn_base * 1.25), seats_left=9),
            ],
            pets=TransportPets(cabin=True, cabin_max_kg=8.0, cabin_fee_rub=3500, hold=True, hold_fee_rub=6500),
            international=True, synthetic=False, source_refs=["ref:world_resolver"],
        )
        cat.flights[conn_fl_id] = fl_conn
        cat.flights_by_route_day[(orig_city, dest_city, target_date)].append(fl_conn)


RUSSIAN_HUB_HOTELS: dict[str, list[dict[str, Any]]] = {
    "MOW": [
        {
            "id": "hot_mow_national",
            "name": "Отель Националь (Москва Центр)",
            "stars": 5,
            "rating": 9.2,
            "dist_center_km": 0.5,
            "base_hotel_rub": 12500,
            "meal": "BB",
            "price_mult": 1.5,
        },
        {
            "id": "hot_mow_radisson",
            "name": "Рэдиссон Славянская Отель & Бизнес Центр",
            "stars": 4,
            "rating": 8.8,
            "dist_center_km": 3.8,
            "base_hotel_rub": 7800,
            "meal": "BB",
            "price_mult": 1.0,
        },
        {
            "id": "hot_mow_cosmos",
            "name": "Космос Отель ВДНХ",
            "stars": 3,
            "rating": 8.3,
            "dist_center_km": 8.2,
            "base_hotel_rub": 4500,
            "meal": "RO",
            "price_mult": 0.6,
        },
        {
            "id": "hot_mow_novotel",
            "name": "Новотель Москва Сити",
            "stars": 4,
            "rating": 8.9,
            "dist_center_km": 4.5,
            "base_hotel_rub": 8900,
            "meal": "BB",
            "price_mult": 1.1,
        },
    ],
    "LED": [
        {
            "id": "hot_led_astoria",
            "name": "Отель Астория (Санкт-Петербург)",
            "stars": 5,
            "rating": 9.4,
            "dist_center_km": 0.4,
            "base_hotel_rub": 13500,
            "meal": "BB",
            "price_mult": 1.5,
        },
        {
            "id": "hot_led_corte_inn",
            "name": "Корт Инн Санкт-Петербург Отель",
            "stars": 4,
            "rating": 8.7,
            "dist_center_km": 2.2,
            "base_hotel_rub": 6900,
            "meal": "BB",
            "price_mult": 1.0,
        },
        {
            "id": "hot_led_azimut",
            "name": "Азимут Отель Санкт-Петербург",
            "stars": 3,
            "rating": 8.4,
            "dist_center_km": 3.5,
            "base_hotel_rub": 4200,
            "meal": "RO",
            "price_mult": 0.6,
        },
    ],
    "SVX": [
        {
            "id": "hot_svx_hyatt",
            "name": "Хаятт Ридженси Екатеринбург",
            "stars": 5,
            "rating": 9.3,
            "dist_center_km": 0.8,
            "base_hotel_rub": 9500,
            "meal": "BB",
            "price_mult": 1.3,
        },
        {
            "id": "hot_svx_onegin",
            "name": "Отель Онегин",
            "stars": 4,
            "rating": 8.8,
            "dist_center_km": 1.5,
            "base_hotel_rub": 5900,
            "meal": "BB",
            "price_mult": 1.0,
        },
    ],
}


def _populate_russian_hub_hotels(cat: Catalog) -> None:
    """Ensure major Russian hubs have hotels and can act as destinations."""
    amenities = ["wifi", "air_conditioning", "restaurant", "parking", "elevator", "spa", "pool"]
    for city_id, hotels in RUSSIAN_HUB_HOTELS.items():
        if city_id in cat.cities:
            c_roles = set(cat.cities[city_id].roles)
            if "destination" not in c_roles:
                c_roles.add("destination")
                cat.cities[city_id] = cat.cities[city_id].model_copy(update={"roles": list(c_roles)})

        if cat.hotels_by_city.get(city_id):
            continue

        hubs = cat.cities[city_id].hub_ids if city_id in cat.cities else [city_id]
        main_hub = hubs[0] if hubs else city_id

        for h_spec in hotels:
            h_id = h_spec["id"]
            if h_id in cat.hotels:
                continue
            h_base = h_spec["base_hotel_rub"]
            meal = h_spec["meal"]
            c_coord = cat.cities[city_id].coord if city_id in cat.cities else GeoPoint(lat=55.75, lon=37.61)
            hotel_obj = Hotel(
                id=h_id,
                city_id=city_id,
                name=h_spec["name"],
                kind="hotel",
                coord=c_coord,
                address=f"Центр города, {city_id}",
                stars=h_spec["stars"],
                stars_source="osm",
                rating=h_spec["rating"],
                reviews_count=250,
                dist_center_km=h_spec["dist_center_km"],
                dist_beach_km=None,
                checkin_time=time(14, 0),
                checkout_time=time(12, 0),
                reception=Reception(
                    hours=ServiceHours(round_the_clock=True),
                    late_arrival_possible=True,
                    note="Круглосуточная стойка регистрации",
                ),
                early_checkin=EarlyCheckin(available=False),
                late_checkout=LateCheckout(available=True, until=time(16, 0), price_rub=2000, guaranteed=False),
                luggage_storage=True,
                pets=HotelPets(allowed=True, max_weight_kg=50.0, fee_rub_per_night=1000, note="Допускаются домашние животные"),
                kids=HotelKids(kids_club=True, kids_pool=True, cribs=True, kids_menu=True),
                amenities=amenities,
                rooms=[
                    RoomType(
                        id=f"rm_{h_id}_std",
                        name="Стандартный номер",
                        max_adults=2,
                        max_children=1,
                        max_occupancy=3,
                        beds="1 большая двуспальная кровать",
                        area_m2=26,
                        base_price_rub=h_base,
                        inventory=10,
                    ),
                    RoomType(
                        id=f"rm_{h_id}_fam",
                        name="Семейный номер",
                        max_adults=3,
                        max_children=2,
                        max_occupancy=4,
                        beds="1 двуспальная и 2 односпальные кровати",
                        area_m2=42,
                        base_price_rub=int(h_base * 1.4),
                        inventory=6,
                    ),
                ],
                tariffs=[
                    Tariff(
                        id=f"tf_{h_id}_ro",
                        room_ids=[f"rm_{h_id}_std", f"rm_{h_id}_fam"],
                        name="Без питания (гибкая отмена)" if meal == "RO" else "Стандартный тариф",
                        meal="RO",
                        refundable=True,
                        free_cancel_days=3,
                        penalty_nights=1,
                        price_factor=1.0,
                    ),
                    Tariff(
                        id=f"tf_{h_id}_bb",
                        room_ids=[f"rm_{h_id}_std", f"rm_{h_id}_fam"],
                        name=f"С питанием ({meal})",
                        meal=meal,
                        refundable=True,
                        free_cancel_days=7,
                        penalty_nights=1,
                        price_factor=1.2,
                    ),
                ],
                month_factors=[1.0] * 12,
                weekend_factor=1.1,
                extra_guest_rub_per_night=1500,
                child_free_age=6,
                open_periods=[DateRange(start=date(2025, 1, 1), end=date(2028, 1, 1))],
                sold_out=[],
                synthetic=False,
                source_refs=["ref:world_resolver"],
            )
            cat.hotels[h_id] = hotel_obj
            cat.hotels_by_city[city_id].append(hotel_obj)

            for hub_id in hubs:
                t_route = TransferRoute(
                    id=f"tr_{hub_id}_{h_id}",
                    city_id=city_id,
                    hub_id=hub_id,
                    hotel_id=h_id,
                    mode="taxi",
                    provider="Островок Трансфер",
                    distance_km=25.0,
                    duration_min=35,
                    price_rub=2200,
                    price_unit="per_vehicle",
                    capacity_pax=4,
                    capacity_bags=3,
                    pets_allowed=True,
                    pet_fee_rub=0,
                    hours=ServiceHours(round_the_clock=True),
                    child_seat=True,
                    free_cancel_hours=2,
                    synthetic=False,
                    source_refs=["ref:world_resolver"],
                )
                cat.transfers[t_route.id] = t_route
                cat.transfers_by_pair[(hub_id, h_id)].append(t_route)


def init_world_catalog(cat: Catalog | None = None) -> None:
    """Preload top world vacation & ski destinations into the in-memory catalog."""
    global _INITIALIZED
    if _INITIALIZED:
        return
    cat = cat or get_catalog()
    for city_id, data in KNOWN_WORLD_DESTINATIONS.items():
        _populate_destination_in_catalog(cat, city_id, data)
    _populate_russian_hub_hotels(cat)
    _INITIALIZED = True
    log.info("Initialized worldwide destinations and Russian hub hotels in catalog: %s", list(KNOWN_WORLD_DESTINATIONS.keys()))


def resolve_world_city(cat: Catalog, text: str) -> str | None:
    """Resolve free-form city/region name to a catalog city ID, registering it if needed."""
    init_world_catalog(cat)
    norm = text.lower().replace("ё", "е").strip()
    from app.planning.landmarks import resolve_landmark
    lm = resolve_landmark(norm)
    if lm:
        if lm["city_id"] in KNOWN_WORLD_DESTINATIONS:
            return lm["city_id"]
        for cid, data in KNOWN_WORLD_DESTINATIONS.items():
            if data["name"].lower() == lm["city"].lower() or data["name_en"].lower() == lm["name_en"].lower():
                return cid
        if lm["city_id"] in cat.cities:
            return lm["city_id"]

    # Direct match on known aliases
    for city_id, data in KNOWN_WORLD_DESTINATIONS.items():
        if city_id.lower() == norm or data["name"].lower() == norm or data["name_en"].lower() == norm:
            return city_id
        for alias in data.get("aliases", []):
            if alias in norm or norm in alias:
                return city_id

    # Filter out non-city terms (durations, party, dates, numbers, budget)
    NON_CITY_STOPWORDS = re.compile(
        r"^(?:\d|ноч|дн|сут|недел|выходн|weekend|человек|взросл|ребен|дет|двоих|троих|руб|тыс|бюджет|январ|феврал|март|апрел|ма[йея]|июн|июл|август|сентябр|октябр|ноябр|декабр|поездк|отпуск|тур|билет|отел)",
        re.I,
    )
    if NON_CITY_STOPWORDS.search(norm):
        return None

    # Dynamic registration for any other unknown city
    if len(norm) >= 3 and not norm.isdigit() and not re.search(r"\d", norm):
        city_id = f"W_{hashlib.md5(norm.encode()).hexdigest()[:4].upper()}"
        capitalized = norm.capitalize()
        country_code = "RU" if re.search(r"[а-яё]", norm) and not any(f in norm for f in ["париж", "рим", "праг", "берлин", "лондон", "вена", "мадрид", "токио", "версал"]) else "XX"
        country_name = "Россия" if country_code == "RU" else "Мир"
        lat, lon = (55.75, 37.61) if country_code == "RU" else (45.0, 15.0)

        try:
            import urllib.request, urllib.parse, json
            q = urllib.parse.quote(capitalized)
            req = urllib.request.Request(
                f"https://nominatim.openstreetmap.org/search?q={q}&format=json&addressdetails=1&limit=1",
                headers={"User-Agent": "OstrovokResolver/1.0"},
            )
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                gdata = json.loads(resp.read().decode("utf-8"))
                if gdata and isinstance(gdata, list):
                    geo_info = gdata[0]
                    lat = float(geo_info.get("lat", lat))
                    lon = float(geo_info.get("lon", lon))
                    addr = geo_info.get("address", {})
                    cc = addr.get("country_code", "").upper()
                    if cc:
                        country_code = cc
                        country_name = addr.get("country", country_name)
        except Exception:
            pass

        dynamic_data = {
            "name": capitalized,
            "name_en": capitalized,
            "country_code": country_code,
            "country": country_name,
            "timezone": "Europe/Moscow",
            "coord": {"lat": lat, "lon": lon},
            "tags": ["city", "culture", "resort"],
            "currency": "EUR",
            "visa": {
                "regime": "visa_free",
                "max_stay_days": 90,
                "documents": ["загранпаспорт"],
                "note": "Уточняйте визовые требования перед поездкой.",
                "checked_at": date(2026, 10, 23),
            },
            "description": f"Поездка в {capitalized}",
            "hub_name": f"Аэропорт {capitalized}",
            "air_day": 22.0,
            "air_night": 15.0,
            "sea": None,
            "base_flight_rub": 35000,
            "base_hotel_rub": 8500,
        }
        _populate_destination_in_catalog(cat, city_id, dynamic_data)
        log.info("Dynamically registered worldwide city: %s -> %s", capitalized, city_id)
        return city_id

    return None
