from pathlib import Path

from eval.schema import validate_scenarios


def test_all_eight_eval_scenarios_match_schema():
    scenarios = validate_scenarios(Path(__file__).resolve().parents[1] / "eval" / "scenarios")
    assert len(scenarios) == 8
    assert {scenario.id for scenario in scenarios} == {
        "s2_01_family_warm_sea", "s2_02_sochi_regression", "s2_03_hard_budget",
        "s2_04_dubai_impossible", "s2_05_short_answers", "s2_06_remove_assumption",
        "s2_07_train_kazan", "s2_08_outside_catalog",
    }
