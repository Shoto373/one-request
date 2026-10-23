"""PostgreSQL state store. Mutations are optimistic and transactional."""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.state.models import TripState


class SessionNotFound(Exception):
    pass


class VersionConflict(Exception):
    def __init__(self, expected: int, actual: int):
        self.expected = expected
        self.actual = actual
        super().__init__(f"expected_version {expected} is stale; current version is {actual}")


class StateStore:
    """Only the agent should call save_turn; readers may use get_state/get_session."""

    def __init__(self, database_url: str):
        self.database_url = database_url

    def connect(self):
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def create_session(self) -> TripState:
        state = TripState(session_id=uuid4())
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, state, version) VALUES (%s, %s, 0)",
                (state.session_id, Jsonb(state.model_dump(mode="json"))),
            )
        return state

    def get_state(self, session_id: UUID | str) -> TripState:
        with self.connect() as conn:
            row = conn.execute("SELECT state, version FROM sessions WHERE id=%s", (str(session_id),)).fetchone()
        if row is None:
            raise SessionNotFound(str(session_id))
        payload = row["state"]
        if isinstance(payload, str):
            import json
            payload = json.loads(payload)
        payload["version"] = row["version"]
        return TripState.model_validate(payload)

    def get_session(self, session_id: UUID | str) -> dict[str, Any]:
        state = self.get_state(session_id)
        with self.connect() as conn:
            messages = conn.execute(
                "SELECT id, role, text, created_at FROM messages WHERE session_id=%s ORDER BY created_at, id",
                (str(session_id),),
            ).fetchall()
        for item in messages:
            item["id"] = str(item["id"])
            item["created_at"] = item["created_at"].isoformat()
        return {"state": state, "messages": messages}

    def save_turn(
        self,
        state: TripState,
        *,
        expected_version: int,
        user_text: str,
        assistant_text: str,
        trace_steps: list[dict[str, Any]],
    ) -> TripState:
        """Atomically compare version and persist state, messages and trace."""
        new_version = expected_version + 1
        state_to_save = state.model_copy(update={"version": new_version})
        payload = state_to_save.model_dump(mode="json")
        with self.connect() as conn:
            row = conn.execute(
                "UPDATE sessions SET state=%s, version=%s, updated_at=now() "
                "WHERE id=%s AND version=%s RETURNING version",
                (Jsonb(payload), new_version, str(state.session_id), expected_version),
            ).fetchone()
            if row is None:
                current = conn.execute("SELECT version FROM sessions WHERE id=%s", (str(state.session_id),)).fetchone()
                if current is None:
                    raise SessionNotFound(str(state.session_id))
                raise VersionConflict(expected_version, current["version"])
            conn.execute("INSERT INTO messages (session_id, role, text) VALUES (%s, 'user', %s)", (str(state.session_id), user_text))
            conn.execute("INSERT INTO messages (session_id, role, text) VALUES (%s, 'assistant', %s)", (str(state.session_id), assistant_text))
            for step in trace_steps:
                conn.execute(
                    "INSERT INTO trace_steps (session_id, version, step_type, name, input, output, status) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (
                        str(state.session_id), new_version, step["step_type"], step["name"],
                        Jsonb(step.get("input")) if step.get("input") is not None else None,
                        Jsonb(step.get("output")) if step.get("output") is not None else None,
                        step.get("status", "ok"),
                    ),
                )
        return state_to_save

    def get_trace(self, session_id: UUID | str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT version, step_no, step_type, name, input, output, status, created_at "
                "FROM trace_steps WHERE session_id=%s ORDER BY step_no", (str(session_id),),
            ).fetchall()
        for row in rows:
            row["created_at"] = row["created_at"].isoformat()
        return rows
