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
    """Adapt A's current ``replan(requirements, state, ...)`` contract to B's state shape.

    The returned mapping must contain ``bundles`` (the new active offers),
    ``kept``, ``replaced``, ``reasons`` and ``price_delta_rub``. A may also
    include ``notes``. The input state has the proposed requirements and still
    carries the selected source bundle so A can rebuild from that plan. ``change``
    is ``{upserts: [Requirement], remove_keys: [str]}``.
    """
    tool = _load("app.planning.replan", "replan")
    requirements = {
        item.key: item.value for item in state.requirements if item.kind != "unknown"
    }
    # A uses the selected old bundle in state and the complete accumulated diff
    # as an explicit input. Do not send only the final clarification turn.
    try:
        result = tool(requirements=requirements, state=state, bundle_id=state.selected_bundle_id, changes=changes)
    except TypeError as exc:
        # Older deployed A module. Do not silently drop the diff: this is an
        # integration failure, not an invitation to call an independent search.
        if "changes" in str(exc):
            raise DestinationIntegrationUnavailable("Track A replan must accept the accumulated changes argument") from exc
        raise
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, dict) or "bundle" not in result:
        raise TypeError("Track A replan must return a mapping with a bundle field")
    if result.get("input_changes") != changes:
        raise TypeError("Track A replan did not confirm receipt of the full accumulated changes diff")

    new_bundle = result.get("bundle")
    bundles = [new_bundle] if new_bundle is not None else []
    old_items = list(state.bundles[0].items) if state.bundles else []
    new_items = new_bundle.get("items", []) if isinstance(new_bundle, dict) else []
    old_by_object = {(item.kind, item.item_id): item for item in old_items}
    new_by_offer = {str(item.get("offer_id")): item for item in new_items}
    kept, replaced = [], []
    for item in result.get("kept", []) or []:
        offer_id = str(item.get("offer_id", "")) if isinstance(item, dict) else ""
        new = new_by_offer.get(offer_id, {})
        old = old_by_object.get((new.get("kind"), new.get("item_id")))
        if new.get("kind") and new.get("item_id"):
            kept.append({"ref": f"object:{new['kind']}:{new['item_id']}", "why": item.get("note", "объект сохранён")})
        if old and old.offer_id == offer_id:
            kept.append({"ref": f"offer:{offer_id}", "why": "предложение сохранено"})
        elif old and offer_id:
            replaced.append({"ref": f"offer:{old.offer_id}→offer:{offer_id}",
                             "why": item.get("note", "объект сохранён, предложение перепроверено")})
    for item in result.get("replaced", []) or []:
        before = item.get("from") or {}
        after = item.get("to") or {}
        old_id, new_id = before.get("offer_id"), after.get("offer_id")
        if old_id:
            replaced.append({"ref": f"offer:{old_id}→offer:{new_id or '—'}", "why": item.get("why", "замена по результату пересборки")})
    return {
        "bundles": bundles,
        "kept": kept,
        "replaced": replaced,
        "reasons": result.get("notes", []),
        "price_delta_rub": result.get("price_delta_rub"),
        "selected_bundle_id": new_bundle.get("id") if isinstance(new_bundle, dict) else None,
        "notes": result.get("notes", []),
        "alternatives": result.get("alternatives", []),
        "input_changes": result.get("input_changes", changes),
    }


def track_a_validate_bundle(bundle: dict[str, Any], state: TripState) -> list[dict[str, Any]]:
    """Run A's validation tool before the agent marks the selected bundle ready."""
    tool = _load("app.planning.validate", "validate_bundle")
    result = tool(bundle, state.model_dump(mode="json"))
    if not isinstance(result, list):
        raise TypeError("Track A validate_bundle must return a list of checks")
    return result
