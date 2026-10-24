"""Quality metrics over fixed scenarios (sprint 5, track A, A2).

Success means "the scenario's success conditions hold", not "a purchase happened": for an
impossible request an honest refusal with confirmed changes is a success.

- ``planner``: every scenario with a concrete trip goes through the real planner without an LLM
  (``eval.plan_check``) and the independent snapshot assertions. Computed on demand, cached until a
  scenario file changes.
- ``agent``: the latest report of the scripted-user runner (``python -m eval.runner``), if present:
  clarifying questions, time to the first recommendation, end-to-end success share.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCENARIO_DIRS = [ROOT / "eval" / "scenarios", ROOT / "eval" / "sprint3", ROOT / "eval" / "sprint4"]
# Independent checks that catch broken connections between items (arrival, check-in, return).
CONNECTION_CHECKS = {"dates_match_requirements", "night_arrival_has_reception", "return_transfer_in_time",
                     "no_stale_offers"}

_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "report": None, "at": None}


def _scenario_key() -> tuple:
    files = sorted(p for d in SCENARIO_DIRS if d.exists() for p in d.glob("*.yaml"))
    return tuple((p.name, p.stat().st_mtime_ns) for p in files)


def planner_report() -> tuple[dict[str, Any], str]:
    from eval.plan_check import run

    key = _scenario_key()
    with _lock:
        if _cache["key"] != key:
            _cache.update(key=key, report=run(SCENARIO_DIRS), at=datetime.now(timezone.utc).isoformat())
        return _cache["report"], _cache["at"]


def _share(passed: int, failed: int) -> float | None:
    return round(passed / (passed + failed), 3) if passed + failed else None


def planner_metrics() -> dict[str, Any]:
    report, at = planner_report()
    results = report["results"]
    evaluated = [r for r in results if r["status"] in ("passed", "failed")]
    passed = sum(r["status"] == "passed" for r in evaluated)
    failures: dict[str, int] = {}
    for r in evaluated:
        for c in r["checks"]:
            if c["status"] == "failed":
                failures[c["name"]] = failures.get(c["name"], 0) + 1
    return {
        "computed_at": at,
        "scenarios_total": len(results),
        "scenarios_evaluated": len(evaluated),
        "scenarios_need_agent": sum(r["status"] == "skipped" for r in results),
        "passed": passed,
        "failed": len(evaluated) - passed,
        "success_rate": _share(passed, len(evaluated) - passed),
        "checks_run": sum(c["status"] != "skipped" for r in evaluated for c in r["checks"]),
        "check_failures": failures,
        "connection_errors": sum(n for k, n in failures.items() if k in CONNECTION_CHECKS),
        "scenarios": [
            {"id": r["scenario_id"], "status": r["status"], "reason": r.get("reason"),
             "bundles": r.get("bundles", []), "proposed_changes": r.get("proposed_changes", []),
             "checks": [{"name": c["name"], "status": c["status"]} for c in r["checks"]]}
            for r in results
        ],
    }


def _report_dir() -> Path:
    return Path(os.getenv("EVAL_REPORT_DIR") or ROOT / "eval" / "reports")


def agent_metrics() -> dict[str, Any]:
    path = _report_dir() / "latest.json"
    if not path.exists():
        return {"available": False,
                "how_to": "python -m eval.runner --mode e2e --base-url http://localhost:8080 (нужен LLM_API_KEY)"}
    report = json.loads(path.read_text(encoding="utf-8"))
    results = report.get("results", [])
    passed = sum(r.get("status") == "passed" for r in results)
    failed = sum(r.get("status") == "failed" for r in results)
    times = [r["time_to_first_recommendation_ms"] for r in results if r.get("time_to_first_recommendation_ms")]
    questions = [r.get("clarifying_questions", 0) for r in results]
    connection = sum(1 for r in results for c in r.get("checks", [])
                     if c.get("name") in CONNECTION_CHECKS and c.get("status") == "failed")
    return {
        "available": True,
        "created_at": report.get("created_at"),
        "mode": report.get("mode"),
        "scenarios_total": len(results),
        "passed": passed,
        "failed": failed,
        "blocked": sum(r.get("status") == "blocked" for r in results),
        "success_rate": _share(passed, failed),
        "avg_clarifying_questions": round(fmean(questions), 2) if questions else None,
        "avg_time_to_first_recommendation_ms": round(fmean(times), 1) if times else None,
        "connection_errors": connection,
    }


def metrics() -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "definition": "Успех = выполнены условия успеха сценария; для невозможного запроса успех — честный отказ "
                      "с подтверждёнными изменениями, а не покупка.",
        "planner": planner_metrics(),
        "agent": agent_metrics(),
    }
