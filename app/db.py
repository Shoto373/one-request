"""Idempotent PostgreSQL migrations and optional local JSON seed loading."""
from __future__ import annotations

import json
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb


ROOT = Path(__file__).resolve().parents[1]


def apply_migrations(database_url: str, migrations_dir: Path | None = None) -> None:
    migrations_dir = migrations_dir or ROOT / "db" / "migrations"
    with psycopg.connect(database_url) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        for path in sorted(migrations_dir.glob("*.sql")):
            found = conn.execute("SELECT 1 FROM schema_migrations WHERE name=%s", (path.name,)).fetchone()
            if found:
                continue
            # Each migration and its marker commit together. Errors propagate.
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"), prepare=False)
                conn.execute("INSERT INTO schema_migrations(name) VALUES (%s)", (path.name,))


def load_seed(database_url: str, seed_dir: Path | None = None) -> int:
    """Load available catalog_entries seed JSON idempotently; missing seed is OK."""
    seed_dir = seed_dir or ROOT / "data" / "seed"
    if not seed_dir.exists():
        return 0
    records: list[dict] = []
    for path in sorted(seed_dir.glob("*.json")):
        content = json.loads(path.read_text(encoding="utf-8"))
        entries = content if isinstance(content, list) else content.get("entries", [])
        records.extend(entries)
    if not records:
        return 0
    with psycopg.connect(database_url) as conn:
        for entry in records:
            conn.execute(
                "INSERT INTO catalog_entries(id, kind, payload, source, synthetic) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING",
                (entry["id"], entry["kind"], Jsonb(entry), entry.get("source"), entry.get("synthetic", True)),
            )
    return len(records)
