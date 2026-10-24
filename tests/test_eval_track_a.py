"""Track A, sprint 4: fixed scenarios through the real planner + independent snapshot assertions."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from eval.assertions import CHECKS, Snapshot, run_check
from eval.plan_check import check_scenario, plan_state
from eval.runner import _base_checks
from eval.schema import load_scenario

ROOT = Path(__file__).resolve().parents[1] / "eval"
ALL = sorted(p for d in ("scenarios", "sprint3", "sprint4") for p in (ROOT / d).glob("*.yaml"))
SPRINT4 = sorted((ROOT / "sprint4").glob("*.yaml"))


def test_fifteen_to_twenty_fixed_scenarios_with_impossible_ones() -> None:
    scenarios = [load_scenario(p) for p in ALL]
    assert 15 <= len(scenarios) <= 20
    assert len({s.id for s in scenarios}) == len(scenarios)
    impossible = [s for s in scenarios if "honest_no_bundle" in s.expected.checks
                  or "no_bundle_under_hard_budget" in s.expected.checks]
    assert len(impossible) >= 3


@pytest.mark.parametrize("path", SPRINT4, ids=lambda p: p.stem)
def test_sprint4_scenarios_pass_track_a(path: Path) -> None:
    result = check_scenario(load_scenario(path))
    assert result["status"] == "passed", result["reason"]
    applicable = [c for c in result["checks"] if c["status"] != "skipped"]
    assert applicable  # every scenario asserts something real


@pytest.fixture(scope="module")
def sochi_state() -> dict:
    return plan_state(load_scenario(ROOT / "sprint4" / "s4_01_sochi_purchase.yaml"))


def _bundle(state: dict) -> dict:
    return next(b for b in state["bundles"] if b["id"] == state["selected_bundle_id"])


def _status(name: str, state: dict, order: dict | None = None) -> str:
    return run_check(name, state, order)["status"]


def test_total_tampering_is_caught(sochi_state: dict) -> None:
    state = copy.deepcopy(sochi_state)
    _bundle(state)["total_rub"] -= 1
    assert _status("snapshot_total_matches_items", state) == "failed"


def test_price_tampering_is_caught(sochi_state: dict) -> None:
    state = copy.deepcopy(sochi_state)
    hotel = next(o for o in _bundle(state)["items"] if o["kind"] == "hotel")
    hotel["unit_price_rub"] -= 500
    hotel["total_rub"] = hotel["unit_price_rub"] * hotel["units"] * hotel["quantity"]
    _bundle(state)["total_rub"] = sum(o["total_rub"] for o in _bundle(state)["items"])
    assert _status("snapshot_total_matches_items", state) == "passed"  # arithmetic is consistent...
    assert _status("prices_recomputed_from_catalog", state) == "failed"  # ...but not the catalog price


def test_party_change_without_repricing_is_caught(sochi_state: dict) -> None:
    state = copy.deepcopy(sochi_state)
    for r in state["requirements"]:
        if r["key"] == "adults":
            r["value"] = 3
    assert _status("party_in_every_offer", state) == "failed"


def test_stale_dates_are_caught(sochi_state: dict) -> None:
    state = copy.deepcopy(sochi_state)
    for r in state["requirements"]:
        if r["key"] == "dates":
            r["value"] = {"start": "2026-11-12", "end": "2026-11-19"}
    assert _status("no_stale_offers", state) == "failed"
    assert _status("dates_match_requirements", state) == "failed"


def test_pet_rules_are_rechecked_from_catalog(sochi_state: dict) -> None:
    from app.catalog.store import get_catalog

    state = copy.deepcopy(sochi_state)
    state["requirements"].append({"key": "pets", "value": {"kind": "dog"}, "kind": "hard"})
    state["requirements"].append({"key": "pet_weight_kg", "value": 8, "kind": "hard"})
    cat = get_catalog()
    hotel = next(o for o in _bundle(state)["items"] if o["kind"] == "hotel")
    # Point the snapshot at a Sochi hotel that does not take animals: the check must read the catalog rule.
    hotel["item_id"] = next(h.id for h in cat.hotels_by_city["AER"] if not h.pets.allowed)
    result = run_check("pet_allowed_everywhere", state)
    assert result["status"] == "failed" and "отель" in result["detail"]


def test_hard_budget_is_checked(sochi_state: dict) -> None:
    state = copy.deepcopy(sochi_state)
    state["requirements"].append({"key": "budget_total_rub", "value": 10_000, "kind": "hard"})
    assert _status("snapshot_within_hard_budget", state) == "failed"
    state["requirements"][-1]["kind"] = "soft"
    assert _status("snapshot_within_hard_budget", state) == "skipped"


def test_order_snapshot_is_used_when_present(sochi_state: dict) -> None:
    bundle = copy.deepcopy(_bundle(sochi_state))
    order = {"status": "paid", "total_rub": bundle["total_rub"],
             "snapshot": {"bundle": bundle, "requirements": sochi_state["requirements"]}}
    assert _status("order_snapshot_frozen", {}, order) == "passed"
    order["total_rub"] += 1
    assert _status("order_snapshot_frozen", {}, order) == "failed"
    snap = Snapshot.from_state({}, order)
    assert snap.items and snap.adults == 2


def test_runner_uses_track_a_checks_instead_of_blocking(sochi_state: dict) -> None:
    scenario = load_scenario(ROOT / "sprint4" / "s4_01_sochi_purchase.yaml")
    results = _base_checks(scenario, sochi_state, [], 0, 0, None)
    by_name = {r["name"]: r["status"] for r in results}
    for name in scenario.expected.checks:
        assert by_name[name] != "blocked", name
    assert by_name["prices_recomputed_from_catalog"] == "passed"
    assert by_name["order_snapshot_frozen"] == "skipped"  # no order without the e2e pay step


def test_every_registered_check_handles_an_empty_state() -> None:
    for name in CHECKS:
        assert run_check(name, {"bundles": [], "requirements": []})["status"] in ("skipped", "passed", "failed")
