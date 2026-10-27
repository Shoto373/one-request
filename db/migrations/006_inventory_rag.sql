-- Migration 006: RAG Travel Inventory across Hotels, Flights, Trains, and Transfers
-- Stores 6 months of comprehensive travel inventory with direct booking URLs and source references.

CREATE TABLE IF NOT EXISTS inventory_hotels (
    id text PRIMARY KEY,
    city_id text NOT NULL,
    name text NOT NULL,
    stars integer NOT NULL DEFAULT 3,
    rating numeric(3, 1) NOT NULL DEFAULT 8.0,
    kind text NOT NULL DEFAULT 'hotel',
    address text,
    coord_lat numeric(10, 6),
    coord_lon numeric(10, 6),
    dist_center_km numeric(5, 2),
    dist_beach_km numeric(5, 2),
    amenities jsonb NOT NULL DEFAULT '[]'::jsonb,
    rooms jsonb NOT NULL DEFAULT '[]'::jsonb,
    tariffs jsonb NOT NULL DEFAULT '[]'::jsonb,
    full_slug text NOT NULL,
    source_url text NOT NULL,
    source_provider text NOT NULL DEFAULT 'ostrovok',
    source_raw_url text NOT NULL,
    description text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_inv_hotels_city ON inventory_hotels(city_id, stars, rating);
CREATE INDEX IF NOT EXISTS idx_inv_hotels_search ON inventory_hotels USING gin(to_tsvector('russian', name || ' ' || coalesce(address, '') || ' ' || description));

CREATE TABLE IF NOT EXISTS inventory_flights (
    id text PRIMARY KEY,
    origin_city text NOT NULL,
    dest_city text NOT NULL,
    carrier_name text NOT NULL,
    carrier_code text NOT NULL,
    flight_no text NOT NULL,
    depart_date date NOT NULL,
    depart_time time NOT NULL,
    arrive_time time NOT NULL,
    duration_min integer NOT NULL,
    price_rub integer NOT NULL,
    baggage jsonb NOT NULL DEFAULT '{}'::jsonb,
    is_direct boolean NOT NULL DEFAULT true,
    source_url text NOT NULL,
    source_provider text NOT NULL DEFAULT 'tutu',
    source_raw_url text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_inv_flights_route_date ON inventory_flights(origin_city, dest_city, depart_date, price_rub);

CREATE TABLE IF NOT EXISTS inventory_trains (
    id text PRIMARY KEY,
    origin_city text NOT NULL,
    dest_city text NOT NULL,
    train_no text NOT NULL,
    carrier text NOT NULL DEFAULT 'ФПК / РЖД',
    depart_date date NOT NULL,
    depart_time time NOT NULL,
    arrive_time time NOT NULL,
    duration_min integer NOT NULL,
    price_rub integer NOT NULL,
    coach_type text NOT NULL,
    source_url text NOT NULL,
    source_provider text NOT NULL DEFAULT 'tutu',
    source_raw_url text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_inv_trains_route_date ON inventory_trains(origin_city, dest_city, depart_date, price_rub);

CREATE TABLE IF NOT EXISTS inventory_transfers (
    id text PRIMARY KEY,
    origin_city text NOT NULL,
    dest_city text NOT NULL,
    hub_id text NOT NULL,
    distance_km numeric(6, 1) NOT NULL,
    duration_min integer NOT NULL,
    price_rub integer NOT NULL,
    car_class text NOT NULL DEFAULT 'comfort',
    source_url text NOT NULL,
    source_provider text NOT NULL DEFAULT 'ostrovok',
    source_raw_url text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_inv_transfers_route ON inventory_transfers(origin_city, dest_city);
