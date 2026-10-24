from pathlib import Path

import pytest

from eval.runner import ScriptedUser, UnexpectedQuestion
from eval.schema import Scenario, ScenarioAction, ScenarioExpected, validate_scenarios


def test_all_eight_eval_scenarios_match_schema():
    scenarios = validate_scenarios(Path(__file__).resolve().parents[1] / "eval" / "scenarios")
    assert len(scenarios) == 8
    assert {scenario.id for scenario in scenarios} == {
        "s2_01_family_warm_sea", "s2_02_sochi_regression", "s2_03_hard_budget",
        "s2_04_dubai_impossible", "s2_05_short_answers", "s2_06_remove_assumption",
        "s2_07_train_kazan", "s2_08_outside_catalog",
    }


def test_sprint4_scenario_schema_and_scripted_user_only_answers_declared_question():
    scenario = Scenario(
        id="s4_01_retry_guard", description="Scripted short clarification answer.",
        now="2026-10-23T12:00:00+03:00", timezone="Europe/Moscow", request="I want a trip",
        actions=[ScenarioAction(type="answer", key="pet_weight_kg", text="8 kg")],
        expected=ScenarioExpected(final_stage="select", checks=["placeholder"]),
    )
    user = ScriptedUser(scenario)
    with pytest.raises(UnexpectedQuestion):
        user.next([{"key": "adults"}])
    action = user.next([{"key": "pet_weight_kg"}])
    assert action.text == "8 kg" and user.answers == 1
