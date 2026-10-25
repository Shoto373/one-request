"""Корзина и имитация оплаты (архитектура п.6). Таблицы: carts/orders из 001_init.sql + 002_cart_checkout.sql.

Инварианты:
- корзина = снимок {session_id, trip_version, bundle_id}; создаётся только из актуальной версии TripState;
- checkout заново проверяет именно эту версию: сессия не ушла вперёд, в комплекте нет block-проверок;
- заказ хранит неизменяемый снимок (триггеры в БД);
- повторный checkout (тот же ключ или та же корзина) возвращает тот же заказ, второй не создаётся.
"""
from __future__ import annotations

import importlib
import asyncio
import inspect
import os
import secrets
from datetime import datetime, timezone
from typing import Any, Callable

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


class CartError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: Any):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra

    def detail(self) -> dict:
        return {"code": self.code, "message": self.message, **self.extra}


def _connect() -> psycopg.Connection:
    url = os.getenv("DATABASE_URL", "")
    if not url:
        raise CartError(503, "database_unavailable", "DATABASE_URL is not configured")
    return psycopg.connect(url, row_factory=dict_row)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---- перепроверка плана: validate_plan трека A, если он уже есть ----

def _default_validate(bundle: dict, _state: dict) -> list[dict]:
    return bundle.get("checks") or []


def _validator() -> Callable[[dict, dict], list[dict]]:
    try:
        return importlib.import_module("app.planning.validate").validate_bundle
    except (ImportError, AttributeError):
        return _default_validate


# ---- сессия (таблица sessions — трек B) ----

def _load_session(conn: psycopg.Connection, session_id: str) -> tuple[dict, int]:
    row = conn.execute("SELECT state, version FROM sessions WHERE id = %s", (session_id,)).fetchone()
    if not row:
        raise CartError(404, "session_not_found", "Сессия не найдена")
    return row["state"], row["version"]


def _check_bundle(bundle: dict, state: dict) -> list[dict]:
    items = bundle.get("items") or []
    if not items:
        raise CartError(422, "empty_bundle", "В комплекте нет позиций")
    total = sum(round(i["total_rub"]) for i in items)
    if total != round(bundle.get("total_rub", -1)):
        raise CartError(422, "total_mismatch", "Итог комплекта не сходится с суммой позиций",
                        bundle_total=bundle.get("total_rub"), items_total=total)
    checks = _validator()(bundle, state)
    if inspect.isawaitable(checks):
        checks = asyncio.run(checks)
    blocking = [c for c in checks if c.get("status") == "block"]
    if blocking:
        raise CartError(422, "plan_blocked", "В плане есть блокирующие проблемы", checks=blocking)
    return checks


# ---- операции ----

def create_cart(session_id: str, version: int, bundle_id: str) -> dict:
    with _connect() as conn:
        state, current = _load_session(conn, session_id)
        if current != version:
            raise CartError(409, "version_conflict", "План изменился, обновите страницу", current_version=current)
        bundle = next((b for b in state.get("bundles") or [] if b.get("id") == bundle_id), None)
        if bundle is None:
            raise CartError(404, "bundle_not_found", "Комплект не найден в этой версии плана")
        checks = _check_bundle(bundle, state)
        snapshot = {
            "session_id": session_id,
            "version": version,
            "bundle": {**bundle, "checks": checks},
            "requirements": state.get("requirements") or [],
            "created_at": _now(),
        }
        total = round(bundle["total_rub"])
        # (session_id, trip_version, bundle_id) уникальны: повторное «Оформить» вернёт ту же корзину
        row = conn.execute(
            "INSERT INTO carts (session_id, trip_version, bundle_id, snapshot, total_rub) VALUES (%s, %s, %s, %s, %s)"
            " ON CONFLICT (session_id, trip_version, bundle_id) DO NOTHING RETURNING *",
            (session_id, version, bundle_id, Jsonb(snapshot), total),
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT * FROM carts WHERE session_id = %s AND trip_version = %s AND bundle_id = %s",
                (session_id, version, bundle_id),
            ).fetchone()
    return _cart_out(row)


def get_cart(cart_id: str) -> dict:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM carts WHERE id = %s", (cart_id,)).fetchone()
    if not row:
        raise CartError(404, "cart_not_found", "Корзина не найдена")
    return _cart_out(row)


def checkout(cart_id: str, payer: dict, idempotency_key: str) -> tuple[dict, bool]:
    """Возвращает (заказ, replay). replay=True — заказ уже был, новый не создан."""
    with _connect() as conn:
        same_key = conn.execute("SELECT * FROM orders WHERE idempotency_key = %s", (idempotency_key,)).fetchone()
        if same_key:
            if str(same_key["cart_id"]) != cart_id:
                raise CartError(409, "idempotency_key_reused", "Ключ оплаты уже использован для другой корзины")
            return _order_out(same_key), True

        # блокировка корзины сериализует параллельные клики
        cart = conn.execute("SELECT * FROM carts WHERE id = %s FOR UPDATE", (cart_id,)).fetchone()
        if not cart:
            raise CartError(404, "cart_not_found", "Корзина не найдена")
        if cart["status"] == "ordered":
            order = conn.execute("SELECT * FROM orders WHERE cart_id = %s", (cart_id,)).fetchone()
            return _order_out(order), True

        state, current = _load_session(conn, str(cart["session_id"]))
        if current != cart["trip_version"]:
            raise CartError(409, "plan_changed", "План изменился после добавления в корзину — проверьте новую версию",
                            cart_version=cart["trip_version"], current_version=current)
        snapshot = cart["snapshot"]
        checks = _check_bundle(snapshot["bundle"], state)

        order_snapshot = {**snapshot, "bundle": {**snapshot["bundle"], "checks": checks}, "ordered_at": _now()}
        payment = {"mock": True, "method": "card", "ref": f"MOCK-{secrets.token_hex(4).upper()}", "paid_at": _now()}
        order = conn.execute(
            "INSERT INTO orders (cart_id, session_id, trip_version, bundle_id, idempotency_key, snapshot, status,"
            " total_rub, payer, payment) VALUES (%s, %s, %s, %s, %s, %s, 'paid', %s, %s, %s) RETURNING *",
            (cart_id, cart["session_id"], cart["trip_version"], cart["bundle_id"], idempotency_key,
             Jsonb(order_snapshot), cart["total_rub"], Jsonb(payer), Jsonb(payment)),
        ).fetchone()
        conn.execute("UPDATE carts SET status = 'ordered' WHERE id = %s", (cart_id,))
    return _order_out(order), False


def get_order(order_id: str) -> dict:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM orders WHERE id = %s", (order_id,)).fetchone()
    if not row:
        raise CartError(404, "order_not_found", "Заказ не найден")
    return _order_out(row)


def _cart_out(r: dict) -> dict:
    return {"cart_id": str(r["id"]), "status": r["status"], "total_rub": r["total_rub"],
            "snapshot": r["snapshot"], "created_at": r["created_at"].isoformat()}


def _order_out(r: dict) -> dict:
    return {"order_id": str(r["id"]), "cart_id": str(r["cart_id"]), "status": r["status"],
            "total_rub": r["total_rub"], "snapshot": r["snapshot"], "payer": r["payer"],
            "payment": r["payment"], "created_at": r["created_at"].isoformat()}
