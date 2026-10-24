"""Работа после покупки: напоминания, условия отмены, ранний заезд, сдвиг дат через пересборку.

Снимок заказа неизменяем; изменение — запись в order_changes с новым составом и расчётом денег.
Действующий план заказа = последняя подтверждённая поправка со сменой состава (иначе снимок).
Деньги считаются только из Offer: цена позиций, cancel_policy (free_until, penalty_rub) и условия отеля.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from psycopg.types.json import Jsonb

from app.cart.service import CartError, _connect, get_order


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _title(o: dict) -> str:
    return o.get("title") or o.get("variant", {}).get("hotel_name") or o.get("item_id", "")


def _changes(order_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM order_changes WHERE order_id = %s ORDER BY created_at", (order_id,)).fetchall()
    return [{"change_id": str(r["id"]), "kind": r["kind"], "status": r["status"], "quote": r["quote"],
             "created_at": r["created_at"].isoformat()} for r in rows]


def current_bundle(order: dict, changes: list[dict] | None = None) -> dict:
    """Действующий состав: последняя поправка со сменой состава, иначе снимок заказа."""
    changes = _changes(order["order_id"]) if changes is None else changes
    for ch in reversed(changes):
        if ch["kind"] == "date_shift" and ch["status"] == "confirmed":
            return ch["quote"]["new_bundle"]
    return order["snapshot"]["bundle"]


def cancel_terms(o: dict, now: datetime) -> dict:
    """Что будет при отмене позиции прямо сейчас — только из cancel_policy предложения."""
    policy = o.get("cancel_policy")
    if not policy:
        return {"status": "nonrefundable", "penalty_rub": o["total_rub"], "free_until": None}
    free_until = _dt(policy.get("free_until"))
    if free_until and now < free_until:
        return {"status": "free", "penalty_rub": 0, "free_until": policy["free_until"]}
    return {"status": "penalty", "penalty_rub": min(int(policy.get("penalty_rub") or 0), o["total_rub"]),
            "free_until": policy.get("free_until")}


def reminders(bundle: dict, now: datetime) -> list[dict]:
    out: list[dict] = []
    for o in bundle["items"]:
        times = o.get("times") or {}
        start, end = _dt(times.get("start")), _dt(times.get("end"))
        if o["kind"] == "flight" and start:
            out.append({"at": (start - timedelta(hours=24)).isoformat(), "kind": "checkin_online",
                        "text": f"Онлайн-регистрация: {_title(o)}", "offer_id": o["offer_id"]})
        if o["kind"] in ("flight", "train") and start:
            out.append({"at": start.isoformat(), "kind": "departure", "text": f"Отправление: {_title(o)}",
                        "offer_id": o["offer_id"]})
        if o["kind"] == "transfer" and start:
            out.append({"at": start.isoformat(), "kind": "transfer", "text": f"Трансфер: {_title(o)}",
                        "offer_id": o["offer_id"]})
        if o["kind"] == "hotel" and start:
            out.append({"at": start.isoformat(), "kind": "checkin", "text": f"Заселение: {_title(o)}",
                        "offer_id": o["offer_id"]})
            if end:
                out.append({"at": end.isoformat(), "kind": "checkout", "text": f"Выезд: {_title(o)}",
                            "offer_id": o["offer_id"]})
        free_until = _dt((o.get("cancel_policy") or {}).get("free_until"))
        if free_until and free_until > now:
            out.append({"at": free_until.isoformat(), "kind": "free_cancel_deadline",
                        "text": f"Последний день бесплатной отмены: {_title(o)}", "offer_id": o["offer_id"]})
    kinds = {o["offer_id"]: o["kind"] for o in bundle["items"]}
    for r in out:
        r["past"] = _dt(r["at"]) <= now
        r["offer_kind"] = kinds.get(r["offer_id"])
    return sorted(out, key=lambda r: r["at"])


def early_checkin_option(bundle: dict) -> dict | None:
    """Ранний заезд — только по условиям отеля из каталога; без гарантии честно говорим «по наличию»."""
    hotel_offer = next((o for o in bundle["items"] if o["kind"] == "hotel"), None)
    if not hotel_offer:
        return None
    from app.catalog.store import get_catalog

    hotel = get_catalog().hotels.get(hotel_offer["item_id"])
    if hotel is None:
        return None
    ec = hotel.early_checkin
    arrival = next((o for o in bundle["items"] if o["kind"] == "transfer"
                    and (o.get("times") or {}).get("end", "") <= (hotel_offer.get("times") or {}).get("start", "")), None)
    return {
        "hotel": _title(hotel_offer),
        "available": ec.available,
        "from": ec.from_.strftime("%H:%M") if ec.from_ else None,
        "price_rub": ec.price_rub or 0,
        "guaranteed": ec.guaranteed,
        "standard_checkin": hotel.checkin_time.strftime("%H:%M"),
        "luggage_storage": hotel.luggage_storage,
        "arrival_at": (arrival or {}).get("times", {}).get("end"),
        "source_refs": [f"hotel:{hotel.id}.early_checkin"],
    }


def aftercare(order_id: str) -> dict:
    order = get_order(order_id)
    changes = _changes(order_id)
    bundle = current_bundle(order, changes)
    now = _now()
    items = [{"offer_id": o["offer_id"], "kind": o["kind"], "title": _title(o), "total_rub": o["total_rub"],
              **cancel_terms(o, now)} for o in bundle["items"]]
    return {
        "order_id": order_id,
        "bundle": bundle,
        "total_rub": sum(o["total_rub"] for o in bundle["items"]),
        "reminders": reminders(bundle, now),
        "cancellation": {"items": items, "penalty_total_rub": sum(i["penalty_rub"] for i in items)},
        "early_checkin": early_checkin_option(bundle),
        "changes": changes,
    }


def _requirements(order: dict, bundle: dict) -> dict[str, Any]:
    reqs = {r["key"]: r["value"] for r in order["snapshot"].get("requirements", []) if r.get("kind") != "unknown"}
    if not isinstance(reqs.get("dates"), dict):
        hotel = next(o for o in bundle["items"] if o["kind"] == "hotel")
        reqs["dates"] = {"start": hotel["times"]["start"][:10], "end": hotel["times"]["end"][:10]}
    return reqs


def quote_date_shift(order_id: str, days: int) -> dict:
    """Пересборка купленной поездки на N дней: что сохраняется, что меняется и сколько это стоит.

    Цена изменения = разница стоимости составов + штрафы за отмену заменённых позиций по их cancel_policy.
    Сохранённые позиции не отменяются и штрафа не дают.
    """
    if not days or abs(days) > 14:
        raise CartError(422, "invalid_shift", "Сдвиг дат — от 1 до 14 дней в любую сторону")
    order = get_order(order_id)
    bundle = current_bundle(order)
    reqs = _requirements(order, bundle)
    old_dates = reqs["dates"]
    new_dates = {k: (date.fromisoformat(v[:10]) + timedelta(days=days)).isoformat() for k, v in old_dates.items()}
    reqs["dates"] = new_dates
    from app.planning.replan import replan

    state = {"bundles": [bundle], "selected_bundle_id": bundle.get("id"), "requirements": order["snapshot"].get("requirements", [])}
    result = replan(reqs, state, bundle.get("id"),
                    {"upserts": [{"key": "dates", "value": new_dates, "kind": "hard"}], "remove_keys": []})
    new_bundle = result.get("bundle")
    if hasattr(new_bundle, "model_dump"):
        new_bundle = new_bundle.model_dump(mode="json")
    if not new_bundle:
        return {"order_id": order_id, "days": days, "feasible": False, "old_dates": old_dates, "new_dates": new_dates,
                "notes": result.get("notes") or ["На новые даты собрать поездку не получилось."]}

    now = _now()
    new_ids = {o["offer_id"] for o in new_bundle["items"]}
    old_ids = {o["offer_id"] for o in bundle["items"]}
    kept = [o for o in bundle["items"] if o["offer_id"] in new_ids]
    replaced = [o for o in bundle["items"] if o["offer_id"] not in new_ids]
    added = [o for o in new_bundle["items"] if o["offer_id"] not in old_ids]
    new_items = {o["item_id"] for o in new_bundle["items"]}
    # тот же отель/рейс на новые даты — для поставщика это отмена и новая бронь, штраф по тарифу всё равно есть
    penalties = [{"offer_id": o["offer_id"], "title": _title(o), "same_item": o["item_id"] in new_items,
                  **cancel_terms(o, now)} for o in replaced]
    old_total = sum(o["total_rub"] for o in bundle["items"])
    new_total = sum(o["total_rub"] for o in new_bundle["items"])
    penalty_total = sum(p["penalty_rub"] for p in penalties)
    return {
        "order_id": order_id, "days": days, "feasible": True,
        "old_dates": old_dates, "new_dates": new_dates, "change": result.get("change"),
        "kept": [{"offer_id": o["offer_id"], "title": _title(o), "kind": o["kind"]} for o in kept],
        "replaced": [{"offer_id": o["offer_id"], "title": _title(o), "kind": o["kind"],
                      "same_item": o["item_id"] in new_items} for o in replaced],
        "added": [{"offer_id": o["offer_id"], "title": _title(o), "kind": o["kind"], "total_rub": o["total_rub"]} for o in added],
        "penalties": penalties,
        "old_total_rub": old_total, "new_total_rub": new_total,
        "price_delta_rub": new_total - old_total, "penalty_total_rub": penalty_total,
        # к оплате (если < 0 — к возврату): разница составов + штрафы за отменённое
        "to_pay_rub": new_total - old_total + penalty_total,
        "new_bundle": new_bundle,
    }


def _store_change(order_id: str, kind: str, quote: dict, key: str) -> dict:
    with _connect() as conn:
        same = conn.execute("SELECT * FROM order_changes WHERE idempotency_key = %s", (key,)).fetchone()
        if same:
            if str(same["order_id"]) != order_id or same["kind"] != kind:
                raise CartError(409, "idempotency_key_reused", "Ключ уже использован для другого изменения")
            return {"change_id": str(same["id"]), "kind": kind, "quote": same["quote"], "replay": True}
        # блокировка заказа: два параллельных изменения не наложатся друг на друга
        conn.execute("SELECT id FROM orders WHERE id = %s FOR UPDATE", (order_id,))
        row = conn.execute(
            "INSERT INTO order_changes (id, order_id, kind, idempotency_key, quote) VALUES (%s, %s, %s, %s, %s) RETURNING *",
            (str(uuid.uuid4()), order_id, kind, key, Jsonb(quote))).fetchone()
    return {"change_id": str(row["id"]), "kind": kind, "quote": quote, "replay": False}


def confirm_date_shift(order_id: str, days: int, key: str) -> dict:
    with _connect() as conn:
        same = conn.execute("SELECT * FROM order_changes WHERE idempotency_key = %s", (key,)).fetchone()
    if same:  # повтор подтверждения — без новой пересборки
        return _store_change(order_id, "date_shift", same["quote"], key)
    quote = quote_date_shift(order_id, days)
    if not quote["feasible"]:
        raise CartError(422, "shift_infeasible", "На новые даты поездка не собирается", notes=quote["notes"])
    quote["payment"] = {"mock": True, "amount_rub": quote["to_pay_rub"], "paid_at": _now().isoformat()}
    return _store_change(order_id, "date_shift", quote, key)


def request_early_checkin(order_id: str, key: str) -> dict:
    order = get_order(order_id)
    option = early_checkin_option(current_bundle(order))
    if not option or not option["available"]:
        raise CartError(422, "early_checkin_unavailable", "Отель не предоставляет ранний заезд")
    quote = {**option, "status_text": "подтверждён" if option["guaranteed"] else "запрошен: отель подтвердит по наличию",
             "payment": {"mock": True, "amount_rub": option["price_rub"] if option["guaranteed"] else 0,
                         "note": None if option["guaranteed"] else "оплата на месте, если номер будет готов"}}
    return _store_change(order_id, "early_checkin", quote, key)
