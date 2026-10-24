"""Pydantic schema for fixed, script-driven evaluation scenarios."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ScenarioAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["message", "answer", "command"]
    text: str | None = None
    key: str | None = None
    command: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("text")
    @classmethod
    def nonempty_if_present(cls, value):
        if value is not None and not value.strip():
            raise ValueError("action text cannot be blank")
        return value


class ExpectedRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    kind: Literal["hard", "soft", "assumption", "unknown"]
    value: Any = None


class ScenarioExpected(BaseModel):
    model_config = ConfigDict(extra="forbid")
    final_stage: Literal["clarify", "inspire", "select", "compare", "assemble", "checkout"]
    requirements: list[ExpectedRequirement] = Field(default_factory=list)
    max_questions_per_turn: int = Field(default=2, ge=0, le=2)
    checks: list[str] = Field(min_length=1)


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^s[234]_[0-9]{2}_[a-z0-9_]+$")
    description: str = Field(min_length=8)
    now: datetime
    timezone: str = Field(min_length=3)
    request: str = Field(min_length=3)
    actions: list[ScenarioAction] = Field(default_factory=list)
    expected: ScenarioExpected
    fixtures: dict[str, Any] = Field(default_factory=dict)


def load_scenario(path: Path) -> Scenario:
    return Scenario.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def validate_scenarios(directory: Path | None = None) -> list[Scenario]:
    directory = directory or Path(__file__).resolve().parent / "scenarios"
    paths = sorted(directory.glob("*.yaml"))
    if len(paths) != 8:
        raise ValueError(f"expected exactly 8 sprint 2 scenarios, found {len(paths)}")
    scenarios = [load_scenario(path) for path in paths]
    ids = [scenario.id for scenario in scenarios]
    if len(set(ids)) != len(ids):
        raise ValueError("scenario ids must be unique")
    return scenarios


if __name__ == "__main__":
    print(f"validated {len(validate_scenarios())} eval scenarios")
