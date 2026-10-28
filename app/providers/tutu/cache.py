"""Pinned Tutu MCP responses for repeatable demos (``TUTU_CACHE=pinned``).

The first call with given tool and arguments goes to Tutu and its real answer (or its error
text) is stored in Postgres; later identical calls replay it with the original ``fetched_at``.
Nothing is invented: a pinned answer is a recorded supplier response. Off by default.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

log = logging.getLogger(__name__)


def enabled() -> bool:
    return os.getenv("TUTU_CACHE", "off").strip().lower() == "pinned" and bool(os.getenv("DATABASE_URL"))


def key(tool: str, args: dict[str, Any]) -> str:
    return tool + ":" + json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)


def get(tool: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """{"data", "error", "fetched_at"} of a recorded answer, or None."""
    try:
        import psycopg
        with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=3) as conn:
            row = conn.execute("SELECT data, error, fetched_at FROM tutu_cache WHERE key = %s",
                               (key(tool, args),)).fetchone()
    except Exception as exc:  # the cache must never break a live search
        log.warning("tutu cache read failed: %s", type(exc).__name__)
        return None
    if row is None:
        return None
    return {"data": row[0], "error": row[1], "fetched_at": row[2].isoformat()}


def put(tool: str, args: dict[str, Any], *, data: dict[str, Any] | None = None, error: str | None = None,
        fetched_at: str) -> None:
    try:
        import psycopg
        from psycopg.types.json import Jsonb
        with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=3) as conn:
            conn.execute(
                "INSERT INTO tutu_cache (key, tool, args, data, error, fetched_at) VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (key) DO NOTHING",
                (key(tool, args), tool, Jsonb(args), Jsonb(data) if data is not None else None, error, fetched_at),
            )
    except Exception as exc:
        log.warning("tutu cache write failed: %s", type(exc).__name__)
