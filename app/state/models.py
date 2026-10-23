from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.catalog.models import Offer


RequirementKind = Literal["hard", "soft", "assumption", "unknown"]


class Requirement(BaseModel):
    key: str
    value: Any = None
    kind: RequirementKind
    source: str
    quote: str | None = None


class Claim(BaseModel):
    text: str
    refs: list[str] = Field(default_factory=list)


class Check(BaseModel):
    kind: str
    status: Literal["ok", "warn", "block"]
    text: str
    options: list[str] = Field(default_factory=list)


class Bundle(BaseModel):
    id: str
    label: str
    items: list[Offer] = Field(default_factory=list)
    total_rub: int | float
    checks: list[Check] = Field(default_factory=list)
    tradeoffs: list[Claim] = Field(default_factory=list)
    risks: list[Claim] = Field(default_factory=list)


class OpenQuestion(BaseModel):
    key: str
    question: str
    why: str


class HistoryEntry(BaseModel):
    version: int
    change: str
    kept: list[str] = Field(default_factory=list)
    replaced: list[str] = Field(default_factory=list)
    price_delta_rub: int | float | None = None


class TripState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    version: int = 0
    stage: Literal["clarify", "inspire", "select", "compare", "assemble", "checkout", "booked"] = "clarify"
    requirements: list[Requirement] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    destinations: list[dict[str, Any]] = Field(default_factory=list)
    bundles: list[Bundle] = Field(default_factory=list)
    selected_bundle_id: str | None = None
    history: list[HistoryEntry] = Field(default_factory=list)


class SessionView(BaseModel):
    state: TripState
    messages: list[dict[str, Any]]
