"""Temporary Offer contract shim for track A.

Replace this module with the track A catalog implementation when it lands. This
model mirrors architecture v2's Offer and deliberately contains no pricing or
search logic.
"""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Offer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    offer_id: str
    kind: Literal["hotel", "flight", "train", "transfer"]
    item_id: str
    variant: dict[str, Any] = Field(default_factory=dict)
    capacity: dict[str, Any] = Field(default_factory=dict)
    quantity: int = 1
    unit_price_rub: int | float
    price_unit: str
    units: int
    total_rub: int | float
    included: list[str] = Field(default_factory=list)
    baggage: Any | None = None
    cancel_policy: dict[str, Any] | None = None
    times: dict[str, Any] | None = None
    synthetic: bool = True
    source_refs: list[str] = Field(default_factory=list)
