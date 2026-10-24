"""GET /api/metrics (JSON) and /metrics (page) — track A, sprint 5."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.metrics.service import metrics

router = APIRouter()
PAGE = Path(__file__).with_name("page.html")


@router.get("/api/metrics")
def get_metrics() -> dict:
    # Sync on purpose: the first call runs the scenarios (a few seconds) in FastAPI's thread pool.
    return metrics()


@router.get("/metrics", include_in_schema=False)
def metrics_page() -> HTMLResponse:
    return HTMLResponse(PAGE.read_text(encoding="utf-8"))
