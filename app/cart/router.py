"""REST: корзина, имитация оплаты, заказы (архитектура п.10)."""
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from app import profile
from app.cart import aftercare, service
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
    # анонимный профиль браузера: после оплаты запоминаем устойчивые предпочтения (app/profile.py)
    profile_id: UUID | None = None


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
    if body.profile_id and not replay:
        try:
            profile.remember(str(body.profile_id), order["snapshot"].get("requirements", []))
        except Exception:  # noqa: BLE001 — память не должна ломать оплату
            pass
    return {**order, "replay": replay}


@router.get("/orders/{order_id}")
def get_order(order_id: UUID) -> dict:
    return _call(service.get_order, str(order_id))


# --- работа после покупки (app/cart/aftercare.py) ---

class DateShiftIn(BaseModel):
    days: int = Field(ge=-14, le=14)


class DateShiftConfirmIn(DateShiftIn):
    idempotency_key: str = Field(min_length=8, max_length=100)


class KeyIn(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=100)


@router.get("/orders/{order_id}/aftercare")
def order_aftercare(order_id: UUID) -> dict:
    """Напоминания, условия отмены сейчас, ранний заезд, история изменений."""
    return _call(aftercare.aftercare, str(order_id))


@router.post("/orders/{order_id}/date-shift/quote")
def date_shift_quote(order_id: UUID, body: DateShiftIn) -> dict:
    """Пересборка купленной поездки на N дней: сохранено/заменено, штрафы, доплата. Ничего не меняет."""
    return _call(aftercare.quote_date_shift, str(order_id), body.days)


@router.post("/orders/{order_id}/date-shift", status_code=201)
def date_shift_confirm(order_id: UUID, body: DateShiftConfirmIn, response: Response) -> dict:
    change = _call(aftercare.confirm_date_shift, str(order_id), body.days, body.idempotency_key)
    response.status_code = 200 if change["replay"] else 201
    return change


@router.post("/orders/{order_id}/early-checkin", status_code=201)
def early_checkin(order_id: UUID, body: KeyIn, response: Response) -> dict:
    change = _call(aftercare.request_early_checkin, str(order_id), body.idempotency_key)
    response.status_code = 200 if change["replay"] else 201
    return change
