"""REST: корзина, имитация оплаты, заказы (архитектура п.10)."""
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from app.cart import service
from app.cart.service import CartError

router = APIRouter(prefix="/api", tags=["cart"])


class CartIn(BaseModel):
    session_id: UUID
    version: int = Field(ge=0)
    bundle_id: str = Field(min_length=1)


class Payer(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=200)


class CheckoutIn(BaseModel):
    payer: Payer
    idempotency_key: str = Field(min_length=8, max_length=100)


def _call(fn, *args):
    try:
        return fn(*args)
    except CartError as e:
        raise HTTPException(e.status, e.detail())


@router.post("/cart", status_code=201)
def create_cart(body: CartIn) -> dict:
    return _call(service.create_cart, str(body.session_id), body.version, body.bundle_id)


@router.get("/cart/{cart_id}")
def get_cart(cart_id: UUID) -> dict:
    return _call(service.get_cart, str(cart_id))


@router.post("/cart/{cart_id}/checkout")
def checkout(cart_id: UUID, body: CheckoutIn, response: Response) -> dict:
    order, replay = _call(service.checkout, str(cart_id), body.payer.model_dump(), body.idempotency_key)
    response.status_code = 200 if replay else 201
    return {**order, "replay": replay}


@router.get("/orders/{order_id}")
def get_order(order_id: UUID) -> dict:
    return _call(service.get_order, str(order_id))
