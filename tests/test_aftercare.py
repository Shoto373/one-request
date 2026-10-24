"""Работа после покупки: условия отмены, напоминания, сдвиг дат через пересборку, ранний заезд."""
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.cart.aftercare import cancel_terms, reminders

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def offer(kind, total, policy=None, start="2026-11-10T06:10:00+03:00", end="2026-11-10T08:40:00+03:00"):
    return {"offer_id": f"o_{kind}_{uuid.uuid4().hex[:4]}", "kind": kind, "item_id": kind, "title": kind,
            "total_rub": total, "cancel_policy": policy, "times": {"start": start, "end": end}}


def test_cancel_terms_follow_offer_policy():
    assert cancel_terms(offer("transfer", 500, {"free_until": "2026-11-09T00:00:00+03:00", "penalty_rub": 100}), NOW) \
        == {"status": "free", "penalty_rub": 0, "free_until": "2026-11-09T00:00:00+03:00"}
    late = cancel_terms(offer("hotel", 20000, {"free_until": "2026-10-30T00:00:00+03:00", "penalty_rub": 5000}), NOW)
    assert late["status"] == "penalty" and late["penalty_rub"] == 5000
    assert cancel_terms(offer("flight", 9000, None), NOW) == {"status": "nonrefundable", "penalty_rub": 9000, "free_until": None}
    # штраф не больше стоимости позиции
    assert cancel_terms(offer("train", 300, {"free_until": None, "penalty_rub": 900}), NOW)["penalty_rub"] == 300


def test_reminders_are_sorted_and_include_online_checkin_and_deadlines():
    b = {"items": [
        offer("hotel", 20000, {"free_until": "2026-11-05T23:59:00+03:00", "penalty_rub": 3000},
              start="2026-11-10T15:00:00+03:00", end="2026-11-17T12:00:00+03:00"),
        offer("flight", 9000),
    ]}
    r = reminders(b, NOW)
    kinds = [x["kind"] for x in r]
    assert kinds[0] == "free_cancel_deadline" and "checkin_online" in kinds and kinds[-1] == "checkout"
    assert [x["at"] for x in r] == sorted(x["at"] for x in r)


DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@pytest.mark.skipif(not DATABASE_URL, reason="set TEST_DATABASE_URL to a dedicated test database")
def test_date_shift_quote_confirm_is_idempotent():
    import psycopg
    from psycopg.types.json import Jsonb
    from fastapi.testclient import TestClient

    os.environ["DATABASE_URL"] = DATABASE_URL
    from app.main import app

    example = json.loads((Path(__file__).parents[1] / "data/examples/bundle_mow_aer.json").read_text(encoding="utf-8"))
    bundle = example["bundles"][0]
    reqs = [{"key": "origin", "value": "Москва", "kind": "hard", "source": "user_text"},
            {"key": "destination", "value": "Сочи", "kind": "hard", "source": "user_text"},
            {"key": "dates", "value": {"start": "2026-11-10", "end": "2026-11-17"}, "kind": "hard", "source": "user_text"},
            {"key": "adults", "value": 2, "kind": "hard", "source": "user_text"}]
    sid = str(uuid.uuid4())
    state = {"session_id": sid, "version": 3, "stage": "checkout", "requirements": reqs, "bundles": [bundle],
             "selected_bundle_id": bundle["id"]}
    with psycopg.connect(DATABASE_URL, autocommit=True) as c:
        c.execute("INSERT INTO sessions (id, state, version) VALUES (%s, %s, %s)", (sid, Jsonb(state), 3))
    with TestClient(app) as client:
        cart = client.post("/api/cart", json={"session_id": sid, "version": 3, "bundle_id": bundle["id"]}).json()
        order = client.post(f"/api/cart/{cart['cart_id']}/checkout",
                            json={"payer": {"name": "И", "email": "i@example.com"}, "idempotency_key": "k-" + uuid.uuid4().hex}).json()
        oid = order["order_id"]
        assert client.get(f"/api/orders/{oid}/aftercare").json()["reminders"]
        q = client.post(f"/api/orders/{oid}/date-shift/quote", json={"days": 2}).json()
        assert q["feasible"] and q["new_dates"] == {"start": "2026-11-12", "end": "2026-11-19"}
        assert q["to_pay_rub"] == q["new_total_rub"] - q["old_total_rub"] + q["penalty_total_rub"]
        key = "shift-" + uuid.uuid4().hex
        first = client.post(f"/api/orders/{oid}/date-shift", json={"days": 2, "idempotency_key": key})
        again = client.post(f"/api/orders/{oid}/date-shift", json={"days": 2, "idempotency_key": key})
        assert first.status_code == 201 and again.status_code == 200 and again.json()["replay"]
        after = client.get(f"/api/orders/{oid}/aftercare").json()
        assert len(after["changes"]) == 1 and after["total_rub"] == q["new_total_rub"]
        # снимок заказа не изменился
        assert client.get(f"/api/orders/{oid}").json()["snapshot"]["bundle"]["items"] == order["snapshot"]["bundle"]["items"]
