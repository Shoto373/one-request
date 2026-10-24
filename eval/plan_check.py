"""Deterministic track-A pass over the fixed scenarios, no LLM (sprint 4, A2/A3).

For every scenario the expected requirements (``expected.requirements``, i.e. what the agent must
extract) go straight into the real track A tools, the way the orchestrator calls them:

    build_bundles -> pick ``fixtures.track_a.select`` (default b1) -> optional ``fixtures.track_a.then``
    (new requirements -> replan) -> cart-like snapshot {bundle, requirements}

and the independent assertions from ``eval.assertions`` run over that snapshot. Scenarios without a
concrete destination or dates are left to the agent eval (``eval.runner``).

    python -m eval.plan_check                     # all scenario folders
    python -m eval.plan_check --scenario-dir eval/sprint4
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.catalog.store import get_catalog
from app.planning.build import build_bundles
from app.planning.replan import replan
from app.planning.suggest import suggest_destinations
from eval.assertions import CHECKS, run_check
from eval.schema import Scenario, load_scenario

DEFAULT_DIRS = [Path("eval/scenarios"), Path("eval/sprint3"), Path("eval/sprint4")]
UNIVERSAL = ["snapshot_total_matches_items", "offers_exist_in_catalog", "prices_recomputed_from_catalog",
             "party_in_every_offer"]


def _requirements(scenario: Scenario, extra: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    reqs = {r.key: {"key": r.key, "value": r.value, "kind": r.kind, "source": "user_text"}
            for r in scenario.expected.requirements}
    for r in extra or []:
        reqs[r["key"]] = {"source": "user_text", "kind": "hard", **r}
    return list(reqs.values())


def _req_map(reqs: list[dict[str, Any]]) -> dict[str, Any]:
    return {r["key"]: r["value"] for r in reqs if r["kind"] != "unknown"}


def plan_state(scenario: Scenario) -> dict[str, Any] | None:
    """Final TripState-like dict produced by track A alone, or None if the scenario needs the agent."""
    spec = scenario.fixtures.get("track_a") or {}
    reqs = _requirements(scenario, spec.get("requirements"))
    rmap = _req_map(reqs)
    if "destination" not in rmap or "dates" not in rmap or "origin" not in rmap:
        return None
    state: dict[str, Any] = {"session_id": str(uuid4()), "version": 1, "stage": "select", "requirements": reqs,
                             "bundles": [], "selected_bundle_id": None, "proposed_changes": [], "history": []}
    bundles = build_bundles(rmap, state)
    state["bundles"] = bundles
    if not bundles:
        state["proposed_changes"] = suggest_destinations(rmap, state).get("alternatives", [])
        state["stage"] = "inspire" if not state["proposed_changes"] else "select"
        return state
    wanted = spec.get("select", "b1")
    state["selected_bundle_id"] = wanted if any(b["id"] == wanted for b in bundles) else bundles[0]["id"]
    if spec.get("then"):
        new_reqs = _requirements(scenario, spec["then"].get("requirements"))
        result = replan(_req_map(new_reqs), state, state["selected_bundle_id"], changes=spec["then"])
        state["requirements"] = new_reqs
        if result["bundle"] is None:
            state["bundles"], state["proposed_changes"] = [], result.get("alternatives", [])
            return state
        state["bundles"] = [result["bundle"]]
        state["selected_bundle_id"] = result["bundle"]["id"]
        state["history"].append(result["history_entry"])
    state["stage"] = "checkout"
    return state


def check_scenario(scenario: Scenario) -> dict[str, Any]:
    state = plan_state(scenario)
    if state is None:
        return {"scenario_id": scenario.id, "status": "skipped", "reason": "нужен агент (нет точного направления/дат)",
                "checks": []}
    cat = get_catalog()
    names = [n for n in scenario.expected.checks if n in CHECKS]
    if state["bundles"]:
        # Invariants every bought snapshot must satisfy, whatever the scenario asserts on top.
        names += [n for n in UNIVERSAL if n not in names]
    checks = [run_check(n, state, None, cat) for n in names]
    expect = (scenario.fixtures.get("track_a") or {}).get("expect", "bundle")
    if expect == "bundle" and not state["bundles"]:
        # Without a bundle every snapshot assertion is "not applicable": that must not count as a pass.
        checks.append({"name": "bundle_expected", "status": "failed",
                       "detail": "ожидался комплект, а его нет: " + "; ".join(c.get("label", "") for c in state["proposed_changes"])})
    failed = [c for c in checks if c["status"] == "failed"]
    return {
        "scenario_id": scenario.id,
        "status": "failed" if failed else "passed",
        "reason": "; ".join(f"{c['name']}: {c['detail']}" for c in failed) or None,
        "bundles": [(b["id"], b["label"], b["total_rub"]) for b in state["bundles"]],
        "proposed_changes": [c.get("label") for c in state.get("proposed_changes", [])],
        "checks": checks,
    }


def run(dirs: list[Path]) -> dict[str, Any]:
    paths = [p for d in dirs if d.exists() for p in sorted(d.glob("*.yaml"))]
    results = [check_scenario(load_scenario(p)) for p in paths]
    return {
        "summary": {s: sum(r["status"] == s for r in results) for s in ("passed", "failed", "skipped")},
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario-dir", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, default=Path("eval/reports/plan_check.json"))
    args = parser.parse_args()
    report = run(args.scenario_dir or DEFAULT_DIRS)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    for r in report["results"]:
        print(f"{r['status']:8s} {r['scenario_id']}" + (f" — {r['reason']}" if r.get("reason") else ""))
    raise SystemExit(1 if report["summary"]["failed"] else 0)


if __name__ == "__main__":
    main()
