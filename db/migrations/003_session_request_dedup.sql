-- Idempotency for chat messages and agent commands. A request id is globally
-- bound to one session and one canonical request body for its lifetime.
CREATE TABLE IF NOT EXISTS session_requests (
    request_id uuid PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    fingerprint text NOT NULL,
    status text NOT NULL CHECK (status IN ('processing', 'completed', 'failed')),
    committed_version integer,
    error jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS session_requests_session_idx ON session_requests(session_id, created_at);
