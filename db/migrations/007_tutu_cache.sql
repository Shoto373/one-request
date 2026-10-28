-- Pinned Tutu MCP responses (TUTU_CACHE=pinned): a recorded real answer is replayed for the
-- same tool and arguments, so a demo shows the same fares at every step and does not wait on the network.
CREATE TABLE IF NOT EXISTS tutu_cache (
    key         text PRIMARY KEY,
    tool        text NOT NULL,
    args        jsonb NOT NULL,
    data        jsonb,
    error       text,
    fetched_at  timestamptz NOT NULL
);
