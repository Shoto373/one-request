"""Память предпочтений между сессиями (доп. задача ТЗ).

После оплаты запоминаем устойчивые предпочтения из требований поездки: откуда летит, кто едет,
животное, питание, транспорт, стиль бюджета. В новой сессии они приходят догадками (kind=assumption,
source=memory) — агент учитывает их, а пользователь видит «как в прошлый раз» и может убрать одним кликом.
Даты, направление и бюджет в рублях не запоминаем: это свойства конкретной поездки, а не человека.
"""
from __future__ import annotations

import os
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.state.models import Requirement

MEMORY_KEYS = ("origin", "adults", "children_count", "children_ages", "pets", "pet_weight_kg",
               "meal_preference", "transport", "budget_preference", "lodging_stars")

MEAL = {"all_inclusive": "Всё включено", "breakfast": "С завтраком", "half_board": "Полупансион", "full_board": "Полный пансион"}
LABELS = {
    "origin": lambda v: f"Вылет: {v}",
    "adults": lambda v: f"{v} взр.",
    "children_count": lambda v: f"Детей: {v}",
    "children_ages": lambda v: "Возраст детей: " + ", ".join(map(str, v)),
    "pets": lambda v: "С собакой" if (v or {}).get("kind") == "dog" else "С питомцем",
    "pet_weight_kg": lambda v: f"Питомец {float(v):g} кг",
    "meal_preference": lambda v: MEAL.get(v, str(v)),
    "transport": lambda v: "Поездом" if v == "train" else "Самолётом",
    "budget_preference": lambda v: "Подешевле",
    "lodging_stars": lambda v: f"Отель {v}★",
}


def _connect() -> psycopg.Connection:
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row, autocommit=True)


def extract(requirements: list[dict]) -> dict[str, Any]:
    """Только то, что сказал сам пользователь (hard/soft), и только устойчивые ключи."""
    return {r["key"]: r["value"] for r in requirements
            if r.get("key") in MEMORY_KEYS and r.get("kind") in ("hard", "soft") and r.get("value") not in (None, "", [])}


def remember(profile_id: str, requirements: list[dict]) -> dict[str, Any]:
    prefs = extract(requirements)
    if not prefs:
        return {}
    with _connect() as conn:
        row = conn.execute(
            "INSERT INTO profiles (id, prefs, trips) VALUES (%s, %s, 1) "
            "ON CONFLICT (id) DO UPDATE SET prefs = profiles.prefs || EXCLUDED.prefs, trips = profiles.trips + 1, "
            "updated_at = now() RETURNING prefs", (profile_id, Jsonb(prefs))).fetchone()
    return row["prefs"]


def get(profile_id: str) -> dict[str, Any]:
    with _connect() as conn:
        row = conn.execute("SELECT prefs, trips, updated_at FROM profiles WHERE id = %s", (profile_id,)).fetchone()
    if not row:
        return {"profile_id": profile_id, "prefs": {}, "trips": 0}
    return {"profile_id": profile_id, "prefs": row["prefs"], "trips": row["trips"],
            "updated_at": row["updated_at"].isoformat()}


def forget(profile_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM profiles WHERE id = %s", (profile_id,))


def memory_requirements(profile_id: str) -> list[Requirement]:
    """Догадки из памяти для новой сессии; подпись прямо говорит, откуда значение."""
    prefs = get(profile_id)["prefs"]
    out = []
    for key in MEMORY_KEYS:
        if key not in prefs:
            continue
        try:
            label = f"{LABELS[key](prefs[key])} — как в прошлый раз"
        except Exception:  # noqa: BLE001 — подпись вторична, значение важнее
            label = None
        out.append(Requirement(key=key, value=prefs[key], kind="assumption", source="memory", quote=None, label=label))
    return out
