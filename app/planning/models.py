"""Bundle, Check and Claim (architecture v2, section 6).

Defined here because ``build_bundles`` and ``validate_plan`` (track A) produce them;
TripState (track B) should import them from this module instead of redefining.
"""

from __future__ import annotations

from typing import Literal

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
