"""Live smoke of the app planner and one accumulated requirements replan."""
from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
from datetime import date, timedelta
from uuid import uuid4

from app.planning.build import build_bundles
from app.planning.replan import replan
from app.providers.base import ProviderError
from app.state.models import Bundle, Requirement, TripState


def requirement(key, value, kind="hard"):
    return Requirement(key=key, value=value, kind=kind, source="user_text")


def result_view(bundle):
    return {"label": bundle.get("label"), "total_rub": bundle.get("total_rub"),
            "price_note": bundle.get("price_note"),
            "offers": [{"kind": o["kind"], "title": o["title"], "total_rub": o["total_rub"],
                        "data_mode": o["data_mode"], "fetched_at": o["fetched_at"]}
                       for o in bundle.get("items", [])],
            "checks": [{"kind": c["kind"], "status": c["status"], "text": c["text"]}
                       for c in bundle.get("checks", [])]}


async def run(depart: date, return_day: date) -> dict:
    state = TripState(session_id=uuid4(), stage="clarify", requirements=[
        requirement("origin", "MOW", "assumption"), requirement("destination", "AER", "assumption"),
        requirement("dates", {"start": depart.isoformat(), "end": return_day.isoformat()}),
        requirement("adults", 2), requirement("children_ages", [5]),
        requirement("budget_total_rub", 150000), requirement("transport", "flight or train", "soft"),
    ])
    report = {"dates": {"departure": depart.isoformat(), "return": return_day.isoformat()},
              "party": {"adults": 2, "children_ages": [5]}, "budget_total_rub": 150000,
              "route_scope": "Moscow ↔ Sochi", "mode": "live Tutu MCP"}
    try:
        bundles = build_bundles({r.key: r.value for r in state.requirements}, state)
        if inspect.isawaitable(bundles):
            bundles = await bundles
    except ProviderError as exc:
        report["initial_search"] = {"status": "blocked", "reason": str(exc)}
        return report
    report["initial_search"] = {"status": "returned", "count": len(bundles),
                                "bundles": [result_view(bundle) for bundle in bundles]}
    if not bundles:
        return report
    selected = min(bundles, key=lambda b: b["total_rub"])
    state.bundles = [Bundle.model_validate(selected)]
    state.selected_bundle_id = selected["id"]
    state.stage = "assemble"
    shifted_start, shifted_end = depart + timedelta(days=2), return_day + timedelta(days=2)
    previous = state.requirements
    state.requirements = [r for r in previous if r.key not in {"dates", "pets", "pet_weight_kg"}]
    state.requirements.extend([
        requirement("dates", {"start": shifted_start.isoformat(), "end": shifted_end.isoformat()}),
        requirement("pets", {"kind": "dog"}), requirement("pet_weight_kg", 8),
    ])
    diff = {"upserts": [{"key": "dates", "value": {"start": shifted_start.isoformat(), "end": shifted_end.isoformat()}, "kind": "hard"},
                         {"key": "pets", "value": {"kind": "dog"}, "kind": "hard"},
                         {"key": "pet_weight_kg", "value": 8, "kind": "hard"}], "remove_keys": []}
    try:
        rebuilt = replan({r.key: r.value for r in state.requirements}, state,
                         bundle_id=state.selected_bundle_id, changes=diff)
        if inspect.isawaitable(rebuilt):
            rebuilt = await rebuilt
        report["replan"] = {"input_changes": rebuilt.get("input_changes"), "kept": rebuilt.get("kept"),
                            "replaced": rebuilt.get("replaced"), "price_delta_rub": rebuilt.get("price_delta_rub"),
                            "bundle": result_view(rebuilt["bundle"]) if rebuilt.get("bundle") else None,
                            "status": "returned" if rebuilt.get("bundle") else "no_feasible_bundle"}
    except ProviderError as exc:
        report["replan"] = {"status": "blocked", "reason": str(exc), "requirements_saved_in_local_state": True,
                            "source_bundle_stale": True}
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="explicitly allow bounded read-only Tutu calls")
    parser.add_argument("--departure", type=date.fromisoformat)
    parser.add_argument("--return-date", type=date.fromisoformat)
    args = parser.parse_args()
    if not args.live:
        parser.error("pass --live to contact Tutu MCP; ordinary tests never use the provider")
    os.environ["TRAVEL_DATA_PROVIDER"] = "tutu"
    departure = args.departure or (date.today() + timedelta(days=28))
    return_day = args.return_date or (departure + timedelta(days=7))
    print(json.dumps(asyncio.run(run(departure, return_day)), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
