"""Track A destination-search boundary. This module contains no search/pricing logic."""
from __future__ import annotations

import importlib
import inspect
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class DestinationIntegrationUnavailable(RuntimeError):
    pass


class AlternativeChange(BaseModel):
    key: str
    value: Any
    kind: str = "hard"


class ConfirmedAlternative(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    label: str
    confirmed: bool = False
    changes: list[AlternativeChange] = Field(default_factory=list)
    refs: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class SuggestionResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    destinations: list[dict[str, Any]] = Field(default_factory=list)
    alternatives: list[ConfirmedAlternative] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    required_inputs: list[str] = Field(default_factory=list)


async def suggest_destinations(requirements: dict[str, Any], state: Any) -> SuggestionResult:
    """Call A's future app.planning.suggest.suggest_destinations implementation.

    A candidate is trusted as feasible only when the tool explicitly returns
    ``feasible: true``. No search or estimation is performed in this adapter.
    """
    try:
        module = importlib.import_module("app.planning.suggest")
        fn = getattr(module, "suggest_destinations")
    except (ImportError, AttributeError) as exc:
        raise DestinationIntegrationUnavailable(
            "Track A destination search is not available: expected "
            "app.planning.suggest.suggest_destinations(requirements, state)"
        ) from exc
    raw = fn(requirements=requirements, state=state)
    if inspect.isawaitable(raw):
        raw = await raw
    try:
        result = SuggestionResult.model_validate(raw)
    except ValidationError as exc:
        raise DestinationIntegrationUnavailable(f"Track A destination result violates the v2 contract: {exc.errors()[0]['msg']}") from exc
    return result.model_copy(update={
        "destinations": [x for x in result.destinations if x.get("feasible") is True],
        "alternatives": [x for x in result.alternatives if x.confirmed][:2],
    })
