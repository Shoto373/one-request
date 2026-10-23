"""Track A adapters used by the agent for transactional plan rebuilds.

Track A owns the calculations. This module only defines the boundary expected by
the orchestrator and refuses to substitute a fresh independent search when A's
replan tool is not installed.
"""
from __future__ import annotations

import importlib
import inspect
from typing import Any

from app.agent.destination_search import DestinationIntegrationUnavailable
from app.state.models import TripState


def _load(module_name: str, function_name: str):
    try:
        return getattr(importlib.import_module(module_name), function_name)
    except (ImportError, AttributeError) as exc:
        raise DestinationIntegrationUnavailable(
            f"Track A {function_name} is unavailable; the current plan was not changed"
        ) from exc


async def track_a_replan(state: TripState, changes: dict[str, Any]) -> dict[str, Any]:
    """Call ``app.planning.replan.replan(state, change)`` without doing A's work here.

    The returned mapping must contain ``bundles`` (the new active offers),
    ``kept``, ``replaced``, ``reasons`` and ``price_delta_rub``. A may also
    include ``notes``. The input state has the proposed requirements and still
    carries the selected source bundle so A can rebuild from that plan. ``change``
    is ``{upserts: [Requirement], remove_keys: [str]}``.
    """
    tool = _load("app.planning.replan", "replan")
    result = tool(state, changes)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, dict) or "bundles" not in result:
        raise TypeError("Track A replan must return a mapping with a bundles field")
    return result


def track_a_validate_bundle(bundle: dict[str, Any], state: TripState) -> list[dict[str, Any]]:
    """Run A's validation tool before the agent marks the selected bundle ready."""
    tool = _load("app.planning.validate", "validate_bundle")
    result = tool(bundle, state.model_dump(mode="json"))
    if not isinstance(result, list):
        raise TypeError("Track A validate_bundle must return a list of checks")
    return result
