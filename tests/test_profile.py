"""Память предпочтений между сессиями: после оплаты — догадки «как в прошлый раз» в новой сессии."""
import os
import uuid

import pytest

from app.profile import extract

DATABASE_URL = os.getenv("TEST_DATABASE_URL")


def test_extract_keeps_only_stable_user_preferences():
    reqs = [
        {"key": "origin", "value": "Москва", "kind": "hard"},
        {"key": "pets", "value": {"kind": "dog"}, "kind": "hard"},
        {"key": "dates", "value": {"start": "2026-11-10", "end": "2026-11-17"}, "kind": "hard"},  # свойство поездки
        {"key": "budget_total_rub", "value": 100000, "kind": "hard"},  # тоже
        {"key": "adults", "value": 2, "kind": "assumption"},  # догадка агента, не слова пользователя
        {"key": "meal_preference", "value": "all_inclusive", "kind": "soft"},
    ]
    assert extract(reqs) == {"origin": "Москва", "pets": {"kind": "dog"}, "meal_preference": "all_inclusive"}


@pytest.mark.skipif(not DATABASE_URL, reason="set TEST_DATABASE_URL to a dedicated test database")
def test_checkout_remembers_and_new_session_starts_with_memory():
    import psycopg
    from psycopg.types.json import Jsonb
    from fastapi.testclient import TestClient

    os.environ["DATABASE_URL"] = DATABASE_URL
    from app.main import app

    item = {"offer_id": "off_x", "kind": "hotel", "item_id": "hotel", "total_rub": 1000, "quantity": 1,
            "unit_price_rub": 1000, "price_unit": "per_trip", "units": 1, "synthetic": True}
    bundle = {"id": "b1", "label": "дешевле", "items": [item], "total_rub": 1000, "checks": [], "tradeoffs": [], "risks": []}
    reqs = [{"key": "origin", "value": "Москва", "kind": "hard", "source": "user_text"},
            {"key": "pets", "value": {"kind": "dog"}, "kind": "hard", "source": "user_text"}]
    sid = str(uuid.uuid4())
    with psycopg.connect(DATABASE_URL, autocommit=True) as c:
        c.execute("INSERT INTO sessions (id, state, version) VALUES (%s, %s, 1)",
                  (sid, Jsonb({"session_id": sid, "version": 1, "stage": "checkout", "requirements": reqs, "bundles": [bundle]})))
    pid = str(uuid.uuid4())
    with TestClient(app) as client:
        cart = client.post("/api/cart", json={"session_id": sid, "version": 1, "bundle_id": "b1"}).json()
        r = client.post(f"/api/cart/{cart['cart_id']}/checkout", json={
            "payer": {"name": "И", "email": "i@example.com"}, "idempotency_key": "k-" + uuid.uuid4().hex, "profile_id": pid})
        assert r.status_code == 201
        assert client.get(f"/api/profiles/{pid}").json()["prefs"] == {"origin": "Москва", "pets": {"kind": "dog"}}

        new = client.post("/api/sessions", json={"profile_id": pid}).json()
        state = client.get(f"/api/sessions/{new['session_id']}").json()["state"]
        memory = {r["key"]: r for r in state["requirements"]}
        assert memory["origin"]["kind"] == "assumption" and memory["origin"]["source"] == "memory"
        assert "как в прошлый раз" in memory["pets"]["label"]

        assert client.delete(f"/api/profiles/{pid}").status_code == 204
        fresh = client.post("/api/sessions", json={"profile_id": pid}).json()
        assert client.get(f"/api/sessions/{fresh['session_id']}").json()["state"]["requirements"] == []
