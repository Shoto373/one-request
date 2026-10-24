"""Track A, sprint 5: GET /api/metrics and the /metrics page."""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.metrics import service
from app.metrics.router import router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_metrics_endpoint_reports_scenario_success(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EVAL_REPORT_DIR", str(tmp_path))
    body = _client().get("/api/metrics").json()
    p = body["planner"]
    assert p["scenarios_total"] >= 15
    assert p["scenarios_evaluated"] >= 10
    assert p["success_rate"] == 1.0 and p["failed"] == 0
    assert p["connection_errors"] == 0 and p["checks_run"] > 30
    impossible = [s for s in p["scenarios"] if s["id"] in ("s4_05_dubai_with_dog_impossible", "s4_06_yerevan_sold_out")]
    assert impossible and all(s["status"] == "passed" and not s["bundles"] and s["proposed_changes"] for s in impossible)
    assert body["agent"]["available"] is False and "eval.runner" in body["agent"]["how_to"]
    assert "честный отказ" in body["definition"]


def test_agent_metrics_from_runner_report(tmp_path, monkeypatch) -> None:
    report = {"created_at": "2026-10-24T12:00:00+03:00", "mode": "e2e", "results": [
        {"status": "passed", "clarifying_questions": 2, "time_to_first_recommendation_ms": 4000,
         "checks": [{"name": "dates_match_requirements", "status": "passed"}]},
        {"status": "failed", "clarifying_questions": 0, "time_to_first_recommendation_ms": 2000,
         "checks": [{"name": "return_transfer_in_time", "status": "failed"}]},
        {"status": "blocked", "clarifying_questions": 1, "time_to_first_recommendation_ms": None, "checks": []},
    ]}
    (tmp_path / "latest.json").write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setenv("EVAL_REPORT_DIR", str(tmp_path))
    a = service.agent_metrics()
    assert a["available"] and a["success_rate"] == 0.5 and a["blocked"] == 1
    assert a["avg_clarifying_questions"] == 1.0 and a["avg_time_to_first_recommendation_ms"] == 3000.0
    assert a["connection_errors"] == 1


def test_metrics_page_is_served() -> None:
    r = _client().get("/metrics")
    assert r.status_code == 200 and "/api/metrics" in r.text and "<title>Метрики агента</title>" in r.text


def test_planner_report_is_cached_until_scenarios_change() -> None:
    first, at1 = service.planner_report()
    second, at2 = service.planner_report()
    assert first is second and at1 == at2
