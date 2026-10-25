from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


ProviderMode = Literal["demo", "live"]


class ProviderError(RuntimeError):
    """A provider failed; callers must not replace live results with demo data."""


class ProviderUnavailable(ProviderError):
    pass


class ProviderResponseError(ProviderError):
    pass


@dataclass(frozen=True)
class SearchResponse:
    provider: str
    tool: str
    fetched_at: str
    args: dict[str, Any]
    data: dict[str, Any]
