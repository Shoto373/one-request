"""Bundle, Check and Claim (architecture v2, section 6).

Track A works with structured checks (``CheckOption`` with price and guarantee).
TripState (track B) and the UI (track C) store ``Check.options`` as strings, so
everything leaving track A goes through ``check_to_wire`` / ``bundle_to_wire``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from app.catalog.models import Model, Offer

CheckKind = Literal[
    "checkin_date",
    "early_arrival",
    "late_arrival",
    "transfer_fit",
    "return_trip",
    "capacity",
    "pets",
    "budget",
    "visa",
    "availability",
]


class Claim(Model):
    """Every statement shown to the user is tied to data refs, e.g. climate:AYT:11.sea_temp, offer:<id>."""

    text: str
    refs: list[str] = Field(default_factory=list)


class CheckOption(Model):
    code: str = Field(description="wait | luggage_storage | early_checkin | prev_night | late_checkout | other_transport | other_hotel | ...")
    text: str
    price_rub: int | None = None
    guaranteed: bool | None = None


class Check(Model):
    kind: CheckKind
    status: Literal["ok", "warn", "block"]
    text: str
    options: list[CheckOption] = Field(default_factory=list)
    refs: list[str] = Field(default_factory=list)


class Bundle(Model):
    id: str
    label: str = Field(description="дешевле | удобнее с ребёнком | гибче отмена | ...")
    items: list[Offer]
    total_rub: int
    checks: list[Check]
    tradeoffs: list[Claim] = Field(default_factory=list)
    risks: list[Claim] = Field(default_factory=list)

    @model_validator(mode="after")
    def _total(self) -> Bundle:
        expected = sum(o.total_rub for o in self.items)
        if self.total_rub != expected:
            raise ValueError(f"Bundle.total_rub {self.total_rub} != sum(items) {expected}")
        return self

    @property
    def feasible(self) -> bool:
        return all(c.status != "block" for c in self.checks)


def rub(n: int) -> str:
    """12 345 ₽ — thousands separated by a space; never touches the surrounding text."""
    return f"{n:,} ₽".replace(",", " ")


CHECK_KIND_RU = {
    "checkin_date": "дата заезда", "early_arrival": "раннее прибытие", "late_arrival": "ночное прибытие и стойка",
    "transfer_fit": "трансфер", "return_trip": "обратная дорога", "capacity": "вместимость", "pets": "животное",
    "budget": "бюджет", "visa": "виза", "availability": "наличие",
}


def option_text(o: CheckOption) -> str:
    return f"{o.text} — {rub(o.price_rub)}" if o.price_rub else o.text


def check_to_wire(c: Check) -> dict[str, Any]:
    """Check as TripState/UI store it: options are strings, offer refs also as ``offer_ids``."""
    return {
        "kind": c.kind,
        "status": c.status,
        "text": c.text,
        "options": [option_text(o) for o in c.options],
        "offer_ids": [r.removeprefix("offer:") for r in c.refs if r.startswith("offer:")],
        "refs": c.refs,
    }


def bundle_to_wire(b: Bundle) -> dict[str, Any]:
    data = b.model_dump(mode="json")
    data["checks"] = [check_to_wire(c) for c in b.checks]
    return data
