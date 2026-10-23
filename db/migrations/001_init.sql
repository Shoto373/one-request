-- Track B storage. catalog_entries is an explicitly temporary persistence
-- boundary until track A supplies its catalog schema and migration.
CREATE TABLE IF NOT EXISTS catalog_entries (
    id text PRIMARY KEY,
    kind text NOT NULL,
    payload jsonb NOT NULL,
    source text,
    synthetic boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sessions (
    id uuid PRIMARY KEY,
    state jsonb NOT NULL,
    version integer NOT NULL DEFAULT 0 CHECK (version >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS messages (
    id bigserial PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role text NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    text text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS messages_session_idx ON messages(session_id, created_at, id);

CREATE TABLE IF NOT EXISTS trace_steps (
    step_no bigserial PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    version integer NOT NULL,
    step_type text NOT NULL,
    name text NOT NULL,
    input jsonb,
    output jsonb,
    status text NOT NULL DEFAULT 'ok',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS trace_session_idx ON trace_steps(session_id, step_no);

CREATE TABLE IF NOT EXISTS carts (
    id uuid PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES sessions(id),
    trip_version integer NOT NULL,
    bundle_id text NOT NULL,
    snapshot jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (session_id, trip_version, bundle_id)
);

CREATE TABLE IF NOT EXISTS orders (
    id uuid PRIMARY KEY,
    cart_id uuid REFERENCES carts(id),
    session_id uuid NOT NULL REFERENCES sessions(id),
    trip_version integer NOT NULL,
    bundle_id text NOT NULL,
    idempotency_key text NOT NULL UNIQUE,
    snapshot jsonb NOT NULL,
    status text NOT NULL DEFAULT 'created',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION reject_order_snapshot_change() RETURNS trigger AS $$
BEGIN
    IF NEW.snapshot IS DISTINCT FROM OLD.snapshot
       OR NEW.session_id IS DISTINCT FROM OLD.session_id
       OR NEW.trip_version IS DISTINCT FROM OLD.trip_version
       OR NEW.bundle_id IS DISTINCT FROM OLD.bundle_id
       OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key THEN
        RAISE EXCEPTION 'order snapshot and booking identity are immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS orders_snapshot_immutable ON orders;
CREATE TRIGGER orders_snapshot_immutable BEFORE UPDATE ON orders
FOR EACH ROW EXECUTE FUNCTION reject_order_snapshot_change();

CREATE TABLE IF NOT EXISTS eval_runs (
    id uuid PRIMARY KEY,
    scenario text NOT NULL,
    status text NOT NULL,
    result jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
