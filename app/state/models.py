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
    label: str | None = None


class Claim(BaseModel):
    text: str
    refs: list[str] = Field(default_factory=list)


class Check(BaseModel):
    id: str | None = None
    kind: str
    status: Literal["ok", "warn", "block"]
    text: str
    options: list[str] = Field(default_factory=list)
    refs: list[str] = Field(default_factory=list)
    offer_ids: list[str] = Field(default_factory=list)


class Bundle(BaseModel):
    id: str
    label: str
    items: list[Offer] = Field(default_factory=list)
    total_rub: int | float
    checks: list[Check] = Field(default_factory=list)
    advantages: list[Claim] = Field(default_factory=list)
    tradeoffs: list[Claim] = Field(default_factory=list)
    risks: list[Claim] = Field(default_factory=list)
    price_note: str | None = None


class OpenQuestion(BaseModel):
    key: str
    question: str
    why: str = ""
    options: list[str] = Field(default_factory=list)


class HistoryEntry(BaseModel):
    version: int
    change: str
    kept: list[str] = Field(default_factory=list)
    replaced: list[str] = Field(default_factory=list)
    price_delta_rub: int | float | None = None
    reasons: list[str] = Field(default_factory=list)
    item_reasons: dict[str, str] = Field(default_factory=dict)
    previous_bundle: dict[str, Any] | None = None
    resulting_bundle_id: str | None = None


class PendingReplan(BaseModel):
    status: Literal["stale_source"] = "stale_source"
    source_bundle: Bundle
    previous_requirements: list[Requirement] = Field(default_factory=list)
    change: str = ""


class TripState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    version: int = 0
    stage: Literal["clarify", "inspire", "select", "compare", "assemble", "checkout", "booked"] = "clarify"
    requirements: list[Requirement] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    destinations: list[dict[str, Any]] = Field(default_factory=list)
    proposed_changes: list[dict[str, Any]] = Field(default_factory=list)
    suppressed_assumptions: list[str] = Field(default_factory=list)
    selected_destination_id: str | None = None
    bundles: list[Bundle] = Field(default_factory=list)
    selected_bundle_id: str | None = None
    pending_replan: PendingReplan | None = None
    history: list[HistoryEntry] = Field(default_factory=list)


class SessionView(BaseModel):
    state: TripState
    messages: list[dict[str, Any]]
