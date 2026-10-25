"""Catalog objects and the Offer contract (architecture v2, section 4).

The catalog stores *objects*: cities, hubs (airports, stations), hotels with room
types and tariffs, dated flights and trains, transfer routes. Objects carry no
final price. Search turns an object into an *Offer*: a price for concrete dates
and a concrete party, where ``total_rub`` always covers the whole party and the
whole period.

Catalog files in ``data/seed/catalog`` are plain JSON arrays of these models
(see ``SEED_FILES``); ``meta.json`` is a single ``SeedMeta`` object.
"""

from __future__ import annotations

from datetime import date, time
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 1


class Model(BaseModel):
    """Strict base: unknown keys are an error, ``from`` style aliases round-trip."""

    model_config = ConfigDict(
        extra="forbid",
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
    )


# --------------------------------------------------------------------------- common


class GeoPoint(Model):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class DateRange(Model):
    """Range of nights/days: ``start`` inclusive, ``end`` exclusive."""

    start: date
    end: date
    note: str | None = None

    @model_validator(mode="after")
    def _ordered(self) -> DateRange:
        if self.end <= self.start:
            raise ValueError("DateRange.end must be after start")
        return self

    def contains(self, day: date) -> bool:
        return self.start <= day < self.end


class ServiceHours(Model):
    """Daily opening hours in local time. ``closes`` < ``opens`` means past midnight."""

    round_the_clock: bool
    opens: time | None = None
    closes: time | None = None

    @model_validator(mode="after")
    def _consistent(self) -> ServiceHours:
        if not self.round_the_clock and (self.opens is None or self.closes is None):
            raise ValueError("opens/closes required unless round_the_clock")
        return self

    def is_open(self, at: time) -> bool:
        if self.round_the_clock:
            return True
        assert self.opens is not None and self.closes is not None
        if self.opens <= self.closes:
            return self.opens <= at < self.closes
        return at >= self.opens or at < self.closes


class Pet(Model):
    kind: Literal["dog", "cat", "other"] = "dog"
    weight_kg: float = Field(gt=0, le=100)


class Party(Model):
    """Who travels. Input of every search function."""

    adults: int = Field(ge=1, le=9)
    children_ages: list[int] = Field(default_factory=list)
    pets: list[Pet] = Field(default_factory=list)
    bags: int | None = Field(default=None, ge=0, description="checked bags; default 1 per person aged 2+")

    @model_validator(mode="after")
    def _ages(self) -> Party:
        if any(a < 0 or a > 17 for a in self.children_ages):
            raise ValueError("children_ages must be 0..17")
        return self

    @property
    def children(self) -> int:
        return len(self.children_ages)

    @property
    def people(self) -> int:
        return self.adults + self.children

    def paying(self, free_under_age: int) -> int:
        """People who need a paid seat: adults plus children aged >= free_under_age."""
        return self.adults + sum(1 for a in self.children_ages if a >= free_under_age)

    @property
    def bag_count(self) -> int:
        if self.bags is not None:
            return self.bags
        return self.adults + sum(1 for a in self.children_ages if a >= 2)


# --------------------------------------------------------------------------- places


class VisaInfo(Model):
    """Entry rules for a citizen of Russia (static reference, see ``checked_at``)."""

    regime: Literal["domestic", "visa_free", "visa_on_arrival", "evisa", "visa_required"]
    max_stay_days: int | None = None
    documents: list[str]
    note: str
    checked_at: date


class City(Model):
    id: str = Field(description="IATA city code: MOW, LED, SVX, AER, KGD, KZN, IST, AYT, SSH, DXB, EVN")
    name: str
    name_en: str
    country_code: str = Field(min_length=2, max_length=2)
    country: str
    roles: list[Literal["origin", "destination"]]
    timezone: str = Field(description="IANA zone, e.g. Europe/Moscow")
    coord: GeoPoint
    population: int | None = None
    description: str | None = None
    currency: str
    visa: VisaInfo
    hub_ids: list[str]
    tags: list[str] = Field(default_factory=list, description="sea, beach, city, culture, ...")
    wikidata: str | None = None
    source_refs: list[str]


class Hub(Model):
    id: str = Field(description="airport IATA (SVO) or station id (st_kzn)")
    kind: Literal["airport", "station"]
    city_id: str
    name: str
    coord: GeoPoint
    timezone: str
    international: bool = False
    source_refs: list[str]


class ClimateMonth(Model):
    month: int = Field(ge=1, le=12)
    air_temp_day: float = Field(description="mean daily max, °C")
    air_temp_night: float = Field(description="mean daily min, °C")
    sea_temp: float | None = Field(default=None, description="mean sea surface, °C; None if no sea")
    precip_mm: float = Field(description="mean monthly total")
    rainy_days: float = Field(description="mean days with >= 1 mm")
    sun_hours: float = Field(description="mean sunshine hours per day")


class CityClimate(Model):
    """Refs for claims: ``climate:{city_id}:{month}.{field}``, e.g. climate:AYT:11.sea_temp."""

    city_id: str
    period: str = Field(description="years averaged, e.g. 2021-2025")
    months: list[ClimateMonth] = Field(min_length=12, max_length=12)
    source_refs: list[str]


# --------------------------------------------------------------------------- hotels

MealCode = Literal["RO", "BB", "HB", "FB", "AI"]
MEAL_INCLUDED: dict[str, list[str]] = {
    "RO": [],
    "BB": ["breakfast"],
    "HB": ["breakfast", "dinner"],
    "FB": ["breakfast", "lunch", "dinner"],
    "AI": ["all_inclusive"],
}


class Reception(Model):
    hours: ServiceHours
    late_arrival_possible: bool = Field(
        description="arrival while reception is closed: True = keybox/by arrangement (warn), False = block"
    )
    note: str | None = None


class EarlyCheckin(Model):
    available: bool
    from_: time | None = Field(default=None, alias="from")
    price_rub: int | None = None
    guaranteed: bool = False


class LateCheckout(Model):
    available: bool
    until: time | None = None
    price_rub: int | None = None
    guaranteed: bool = False


class HotelPets(Model):
    allowed: bool
    max_weight_kg: float | None = None
    fee_rub_per_night: int = 0
    note: str | None = None


class HotelKids(Model):
    kids_club: bool = False
    kids_pool: bool = False
    cribs: bool = False
    kids_menu: bool = False


class RoomType(Model):
    id: str
    name: str
    max_adults: int = Field(ge=1)
    max_children: int = Field(ge=0)
    max_occupancy: int = Field(ge=1, description="adults + children aged 2+; infants under 2 sleep in a crib")
    beds: str
    area_m2: int
    base_price_rub: int = Field(gt=0, description="per room-night, occupancy up to 2, room only, month factor 1.0")
    inventory: int = Field(ge=0, description="rooms of this type sellable per night")


class Tariff(Model):
    id: str
    name: str
    meal: MealCode
    refundable: bool
    free_cancel_days: int | None = Field(
        default=None, description="free cancellation until 23:59 local, N days before check-in"
    )
    penalty_nights: int = Field(default=1, description="penalty after the deadline, in nights")
    price_factor: float = Field(gt=0, description="multiplier over room base price (meal and flexibility)")
    room_ids: list[str] = Field(description="room types this tariff is sold for")


class Hotel(Model):
    id: str
    city_id: str
    name: str
    kind: Literal["hotel", "resort", "boutique", "guesthouse", "apart"]
    coord: GeoPoint
    address: str | None = None
    stars: int | None = Field(default=None, ge=1, le=5)
    stars_source: Literal["osm", "generated"]
    rating: float = Field(ge=0, le=10)
    reviews_count: int
    dist_center_km: float
    dist_beach_km: float | None = None
    checkin_time: time
    checkout_time: time
    reception: Reception
    early_checkin: EarlyCheckin
    late_checkout: LateCheckout
    luggage_storage: bool
    pets: HotelPets
    kids: HotelKids
    amenities: list[str]
    rooms: list[RoomType] = Field(min_length=1)
    tariffs: list[Tariff] = Field(min_length=1)
    month_factors: list[float] = Field(min_length=12, max_length=12, description="index 0 = January")
    weekend_factor: float = Field(description="applied to Friday and Saturday nights")
    extra_guest_rub_per_night: int = Field(description="per guest above 2 in a room")
    child_free_age: int = Field(description="extra children younger than this stay free")
    open_periods: list[DateRange] = Field(default_factory=list, description="empty = open all year")
    sold_out: list[DateRange] = Field(default_factory=list, description="nights with no rooms at all")
    synthetic: bool = True
    source_refs: list[str]

    def room(self, room_id: str) -> RoomType:
        return next(r for r in self.rooms if r.id == room_id)


# --------------------------------------------------------------------------- transport


class Baggage(Model):
    cabin_kg: int
    checked_pieces: int = Field(ge=0)
    checked_kg: int | None = None


class TransportPets(Model):
    cabin: bool
    cabin_max_kg: float | None = Field(default=None, description="pet + carrier")
    cabin_fee_rub: int = 0
    hold: bool = False
    hold_fee_rub: int = 0
    note: str | None = None


class FlightSegment(Model):
    flight_no: str
    carrier: str
    from_hub: str
    to_hub: str
    depart: AwareDatetime
    arrive: AwareDatetime
    duration_min: int
    aircraft: str


class Fare(Model):
    code: Literal["light", "standard", "flex"]
    name: str
    price_rub: int = Field(gt=0, description="per passenger aged infant_free_age+")
    baggage: Baggage
    refundable: bool
    refund_fee_rub: int | None = None
    seats_left: int = Field(ge=0)


class Flight(Model):
    """One dated departure (direct or with connections), times with UTC offset."""

    id: str
    carrier: str
    carrier_name: str
    flight_no: str
    origin_city: str
    dest_city: str
    from_hub: str
    to_hub: str
    depart: AwareDatetime
    arrive: AwareDatetime
    duration_min: int
    stops: int = Field(ge=0)
    segments: list[FlightSegment] = Field(min_length=1)
    fares: list[Fare] = Field(min_length=1)
    pets: TransportPets
    international: bool
    infant_free_age: int = 2
    synthetic: bool = True
    source_refs: list[str]


class TrainCar(Model):
    car_class: Literal["sitting", "platzkart", "kupe", "sv"]
    name: str
    price_rub: int = Field(gt=0, description="per seat or berth")
    seats_left: int = Field(ge=0)
    pets_allowed: bool
    pet_fee_rub: int = 0
    bedding_included: bool
    refund_fee_rub: int


class Train(Model):
    id: str
    train_no: str
    name: str | None = None
    origin_city: str
    dest_city: str
    from_hub: str
    to_hub: str
    depart: AwareDatetime
    arrive: AwareDatetime
    duration_min: int
    cars: list[TrainCar] = Field(min_length=1)
    child_free_age: int = Field(default=5, description="younger children travel free without own berth")
    hand_luggage_kg: int = 36
    synthetic: bool = True
    source_refs: list[str]


class TransferRoute(Model):
    """Hub <-> hotel route, valid in both directions."""

    id: str
    city_id: str
    hub_id: str
    hotel_id: str
    mode: Literal["taxi", "minivan", "shuttle", "public"]
    provider: str
    distance_km: float
    duration_min: int
    price_rub: int
    price_unit: Literal["per_vehicle", "per_person"]
    capacity_pax: int
    capacity_bags: int
    pets_allowed: bool
    pet_fee_rub: int = 0
    hours: ServiceHours
    child_seat: bool
    free_cancel_hours: int = Field(description="free cancellation until N hours before pickup")
    child_free_age: int = 2
    synthetic: bool = True
    source_refs: list[str]


# --------------------------------------------------------------------------- offer (section 4 contract)


class Capacity(Model):
    adults: int
    children: int
    pets: bool


class CancelPolicy(Model):
    free_until: AwareDatetime | None = Field(description="None = no free cancellation")
    penalty_rub: int


class OfferTimes(Model):
    start: AwareDatetime
    end: AwareDatetime


class Offer(Model):
    """Priced proposal for concrete dates and party.

    Invariant: ``total_rub == unit_price_rub * units * quantity + sum(e["price_rub"] for e in variant["extras"])``.
    ``variant`` is descriptive and kind-specific (hotel_name, room_type, tariff, fare, car_class,
    mode, from/to hubs, nightly_rub, extras, structured baggage...). Missing ``extras`` means no extras.
    ``title`` and ``baggage`` are ready-to-show strings for the UI (web/src/api/types.ts).
    Search always fills every field; defaults only keep minimal hand-written offers
    (track B fixtures of the former shim) valid.
    """

    offer_id: str
    kind: Literal["hotel", "flight", "train", "transfer"]
    item_id: str
    origin_city: str | None = None
    destination_city: str | None = None
    origin_provider_id: str | None = None
    destination_provider_id: str | None = None
    title: str | None = None
    variant: dict[str, Any] = Field(default_factory=dict)
    capacity: Capacity | None = None
    quantity: int = Field(default=1, ge=1)
    unit_price_rub: int = Field(ge=0)
    price_unit: Literal["per_room_night", "per_person", "per_vehicle", "per_trip"]
    units: int = Field(ge=1)
    total_rub: int = Field(ge=0)
    included: list[str] = Field(default_factory=list)
    baggage: str | None = None
    cancel_policy: CancelPolicy | None = None
    times: OfferTimes | None = None
    synthetic: bool = True
    source_refs: list[str] = Field(default_factory=list)
    provider: str = "catalog"
    provider_offer_id: str | None = None
    fetched_at: AwareDatetime | None = None
    expires_at: AwareDatetime | None = None
    currency: str = "RUB"
    data_mode: Literal["live", "cache", "estimated", "demo"] = "demo"
    search_params: dict[str, Any] = Field(default_factory=dict)
    source_url: str | None = None
    revalidation: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _total_matches(self) -> Offer:
        extras = sum(int(e["price_rub"]) for e in self.variant.get("extras", []))
        expected = self.unit_price_rub * self.units * self.quantity + extras
        if self.total_rub != expected:
            raise ValueError(f"total_rub {self.total_rub} != unit*units*quantity+extras {expected}")
        return self


# --------------------------------------------------------------------------- seed meta


class EdgeCase(Model):
    id: str
    kind: Literal[
        "night_arrival_next_day",
        "no_24h_reception",
        "no_pets_hotel",
        "no_pets_transfer",
        "no_pets_transport",
        "no_options_dates",
        "hotel_closed_season",
        "out_of_schedule",
        "train_route",
        "budget_infeasible",
    ]
    refs: list[str]
    note: str


class SeedMeta(Model):
    schema_version: int = SCHEMA_VERSION
    random_seed: int
    window: DateRange = Field(description="dates covered by dated transport")
    counts: dict[str, int]
    edge_cases: list[EdgeCase]
    sources: list[str]


SEED_FILES: dict[str, type[Model]] = {
    "cities.json": City,
    "hubs.json": Hub,
    "climate.json": CityClimate,
    "hotels.json": Hotel,
    "flights.json": Flight,
    "trains.json": Train,
    "transfers.json": TransferRoute,
}
