from pathlib import Path

from eval.schema import load_scenario


def test_sprint3_fixed_replan_scenarios_match_schema():
    root = Path(__file__).resolve().parents[1] / "eval" / "sprint3"
    scenarios = [load_scenario(path) for path in sorted(root.glob("*.yaml"))]
    assert {scenario.id for scenario in scenarios} == {
        "s3_01_dates_and_dog", "s3_02_budget_clarification",
    }
    assert scenarios[0].expected.checks
