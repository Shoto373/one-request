"""Корзина и имитация оплаты против отдельной тестовой БД (TEST_DATABASE_URL), как в test_postgres_integration."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from psycopg.types.json import Jsonb

DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="set TEST_DATABASE_URL to a dedicated test database")

PAYER = {"name": "Иван", "email": "ivan@example.com"}


def offer(kind: str, total: int) -> dict:
    return {"offer_id": f"off_{kind}_{uuid.uuid4().hex[:6]}", "kind": kind, "item_id": kind, "total_rub": total,
            "quantity": 1, "unit_price_rub": total, "price_unit": "per_trip", "units": 1, "synthetic": True}


def bundle(bid: str = "b1", checks: list | None = None) -> dict:
    items = [offer("flight", 30000), offer("transfer", 2000), offer("hotel", 50000), offer("transfer", 2000)]
    return {"id": bid, "label": "дешевле", "items": items, "total_rub": 84000, "checks": checks or [],
            "tradeoffs": [], "risks": []}


@pytest.fixture(scope="module")
def client():
    os.environ["DATABASE_URL"] = DATABASE_URL  # приложение и сервис корзины работают с тестовой БД
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as c:  # lifespan применяет миграции
        yield c


def make_session(*bundles: dict, version: int = 3) -> str:
    sid = str(uuid.uuid4())
    state = {"session_id": sid, "version": version, "stage": "assemble",
             "requirements": [{"key": "budget_total_rub", "value": 100000, "kind": "hard", "source": "user_text"}],
             "bundles": list(bundles) or [bundle()]}
    with psycopg.connect(DATABASE_URL, autocommit=True) as c:
        c.execute("INSERT INTO sessions (id, state, version) VALUES (%s, %s, %s)", (sid, Jsonb(state), version))
    return sid


def bump_version(sid: str) -> None:
    with psycopg.connect(DATABASE_URL, autocommit=True) as c:
        c.execute("UPDATE sessions SET version = version + 1 WHERE id = %s", (sid,))


def new_cart(client, sid: str, version: int = 3, bid: str = "b1"):
    return client.post("/api/cart", json={"session_id": sid, "version": version, "bundle_id": bid})


def pay(client, cart_id: str, key: str | None = None):
    return client.post(f"/api/cart/{cart_id}/checkout",
                       json={"payer": PAYER, "idempotency_key": key or "key-" + uuid.uuid4().hex})


def test_cart_snapshot_and_checkout(client):
    r = new_cart(client, make_session())
    assert r.status_code == 201, r.text
    cart = r.json()
    assert cart["total_rub"] == 84000 and cart["snapshot"]["version"] == 3

    r = pay(client, cart["cart_id"])
    assert r.status_code == 201, r.text
    order = r.json()
    assert order["status"] == "paid" and order["replay"] is False and order["payment"]["mock"] is True
    assert client.get(f"/api/orders/{order['order_id']}").json()["total_rub"] == 84000


def test_repeat_add_returns_same_cart(client):
    sid = make_session()
    assert new_cart(client, sid).json()["cart_id"] == new_cart(client, sid).json()["cart_id"]


def test_version_conflict_on_cart(client):
    r = new_cart(client, make_session(), version=2)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "version_conflict"


def test_blocked_plan_not_added(client):
    sid = make_session(bundle(checks=[{"kind": "pets", "status": "block", "text": "Отель не принимает собак"}]))
    r = new_cart(client, sid)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "plan_blocked"


def test_total_mismatch_rejected(client):
    b = bundle()
    b["total_rub"] = 1000  # «дешёвый» итог, не равный сумме позиций
    r = new_cart(client, make_session(b))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "total_mismatch"


def test_plan_changed_after_cart(client):
    sid = make_session()
    cart = new_cart(client, sid).json()
    bump_version(sid)
    r = pay(client, cart["cart_id"])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "plan_changed"


def test_double_pay_same_key_one_order(client):
    cart = new_cart(client, make_session()).json()
    key = "key-" + uuid.uuid4().hex
    first = pay(client, cart["cart_id"], key).json()
    second = pay(client, cart["cart_id"], key)
    assert second.status_code == 200 and second.json()["replay"] is True
    assert second.json()["order_id"] == first["order_id"]


def test_double_pay_new_key_still_one_order(client):
    cart = new_cart(client, make_session()).json()
    first = pay(client, cart["cart_id"]).json()
    second = pay(client, cart["cart_id"]).json()
    assert second["order_id"] == first["order_id"] and second["replay"] is True


def test_concurrent_clicks_one_order(client):
    cart = new_cart(client, make_session()).json()
    key = "key-" + uuid.uuid4().hex
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(lambda _: pay(client, cart["cart_id"], key), range(8)))
    assert all(r.status_code in (200, 201) for r in results), [r.text for r in results]
    assert len({r.json()["order_id"] for r in results}) == 1
    assert sum(r.status_code == 201 for r in results) == 1


def test_key_reuse_for_other_cart_rejected(client):
    key = "key-" + uuid.uuid4().hex
    c1 = new_cart(client, make_session()).json()
    c2 = new_cart(client, make_session()).json()
    pay(client, c1["cart_id"], key)
    r = pay(client, c2["cart_id"], key)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "idempotency_key_reused"


def test_order_snapshot_immutable(client):
    cart = new_cart(client, make_session()).json()
    order = pay(client, cart["cart_id"]).json()
    with psycopg.connect(DATABASE_URL) as c, pytest.raises(psycopg.errors.RaiseException):
        c.execute("UPDATE orders SET total_rub = 1 WHERE id = %s", (order["order_id"],))
