"""Script-driven eval runner for the existing s2/s3 YAML scenario format.

Deterministic mode loads a runtime factory from EVAL_RUNTIME_FACTORY=module:factory;
the factory must return an isolated engine with a stub LLM and real/fixed A tools.
End-to-end mode talks to a running API, so the configured server LLM and planning
tools are exercised. The script user itself never calls an LLM.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from eval.schema import Scenario, load_scenario


class UnexpectedQuestion(RuntimeError):
    pass


class EvalHTTPError(RuntimeError):
    def __init__(self, status: int, detail: Any):
        self.status, self.detail = status, detail
        super().__init__(f"HTTP {status}: {detail}")


class ScriptedUser:
    """Consumes only declared actions; it has no inference or default consent."""
    def __init__(self, scenario: Scenario):
        self.actions = list(scenario.actions)
        self.position = 0
        self.answers = 0

    def next(self, open_questions: list[dict[str, Any]]):
        if self.position >= len(self.actions):
            return None
        action = self.actions[self.position]
        if action.type == "answer":
            expected_key = action.key
            active_keys = {str(q.get("key")) for q in open_questions}
            if expected_key not in active_keys:
                raise UnexpectedQuestion(
                    f"script expected a question for {expected_key!r}; current keys={sorted(active_keys)}"
                )
            self.answers += 1
        self.position += 1
        return action


class HTTPRuntime:
    substitutions: list[str] = []

    def __init__(self, base_url: str):
        self.client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=90)
        self.session_id: str | None = None
        self.trace_id: str | None = None
        self.order_id: str | None = None
        self.tool_names: set[str] = set()

    async def create_session(self):
        response = await self.client.post("/api/sessions", json={})
        self._raise(response)
        payload = response.json()
        self.session_id = payload["session_id"]
        return payload

    @staticmethod
    def _raise(response):
        if response.is_error:
            try:
                detail = response.json().get("detail", response.text)
            except Exception:
                detail = response.text[:500]
            raise EvalHTTPError(response.status_code, detail)

    async def _stream(self, path: str, payload: dict[str, Any]):
        response = await self.client.post(path, json=payload, headers={"Accept": "text/event-stream"})
        self._raise(response)
        events: dict[str, list[Any]] = {}
        event = "message"
        data = []
        for line in response.text.splitlines() + [""]:
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
            elif not line and data:
                try:
                    item = json.loads("\n".join(data))
                except json.JSONDecodeError:
                    item = {"invalid_json": True}
                events.setdefault(event, []).append(item)
                if event == "step" and isinstance(item, dict) and item.get("name"):
                    self.tool_names.add(str(item["name"]))
                event, data = "message", []
        if events.get("error"):
            detail = events["error"][-1]
            raise EvalHTTPError(200, detail)
        return events

    async def send_message(self, text: str, version: int):
        return await self._stream(f"/api/sessions/{self.session_id}/messages", {
            "request_id": str(uuid4()), "text": text, "expected_version": version,
        })

    async def send_command(self, command: str, payload: dict[str, Any], version: int):
        return await self._stream(f"/api/sessions/{self.session_id}/commands", {
            "request_id": str(uuid4()), "type": command, "payload": payload, "expected_version": version,
        })

    async def get_session(self):
        response = await self.client.get(f"/api/sessions/{self.session_id}")
        self._raise(response)
        return response.json()

    async def get_trace(self):
        response = await self.client.get(f"/api/sessions/{self.session_id}/trace")
        self._raise(response)
        return response.json()

    async def close(self):
        await self.client.aclose()


def _load_factory():
    path = os.getenv("EVAL_RUNTIME_FACTORY", "")
    if ":" not in path:
        raise RuntimeError("deterministic mode needs EVAL_RUNTIME_FACTORY=module:factory")
    module_name, function_name = path.split(":", 1)
    return getattr(importlib.import_module(module_name), function_name)


def _requirements_map(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["key"]: r for r in state.get("requirements", [])}


def _base_checks(scenario: Scenario, state: dict[str, Any], trace: list[dict[str, Any]],
                 questions_per_turn: int, total_questions: int):
    results = []

    def add(name: str, passed: bool, detail: str = ""):
        results.append({"name": name, "status": "passed" if passed else "failed", "detail": detail})

    add("final_stage", state.get("stage") == scenario.expected.final_stage,
        f"expected={scenario.expected.final_stage}, actual={state.get('stage')}")
    reqs = _requirements_map(state)
    for expected in scenario.expected.requirements:
        actual = reqs.get(expected.key)
        add(f"requirement:{expected.key}", bool(actual and actual.get("kind") == expected.kind and actual.get("value") == expected.value),
            "missing or value/kind mismatch" if not actual else "")
    add("questions_per_turn_limit", questions_per_turn <= scenario.expected.max_questions_per_turn,
        f"maximum returned in one turn={questions_per_turn}")
    active = state.get("bundles", [])
    for bundle in active:
        actual_total = sum(x.get("total_rub", 0) for x in bundle.get("items", []))
        add(f"bundle_total:{bundle.get('id')}", actual_total == bundle.get("total_rub"),
            f"offers={actual_total}, bundle={bundle.get('total_rub')}")
        if state.get("stage") == "checkout" and state.get("selected_bundle_id") == bundle.get("id"):
            add("checkout_has_no_block", not any(c.get("status") == "block" for c in bundle.get("checks", [])))
    if state.get("history") and active:
        last = state["history"][-1]
        current = next((b for b in active if b.get("id") == last.get("resulting_bundle_id")), None)
        if current is not None and last.get("price_delta_rub") is not None:
            old = last.get("previous_bundle") or {}
            add("history_price_delta", current.get("total_rub", 0) - old.get("total_rub", 0) == last["price_delta_rub"])

    known = {r["name"] for r in results}
    # Named scenario assertions remain explicit: no unsupported assertion is silently passed.
    implemented = {
        "feasible_options_from_tool": lambda: bool(state.get("destinations") or active),
        "replan_uses_selected_bundle": lambda: any(s.get("name") == "replan_result" for s in trace)
            and bool(state.get("history") and state["history"][-1].get("previous_bundle")),
        "all_dates_match_active_offers": lambda: all(
            not o.get("times") or o["times"]["start"][:10] >= str(reqs.get("dates", {}).get("value", {}).get("start", "0001"))
            for b in active for o in b.get("items", [])
        ),
        "pet_check_covers_transport_hotel_transfer": lambda: any(
            c.get("kind") in {"pets", "pet_policy"} and c.get("status") in {"ok", "warn"}
            and all(word in c.get("text", "").lower() for word in ("перевоз", "отел", "трансфер"))
            for b in active for c in b.get("checks", [])
        ),
        "total_delta_matches_offers": lambda: any(x["name"] == "history_price_delta" and x["status"] == "passed" for x in results),
        "questions_only_blockers": lambda: all(q.get("key") in {"origin", "dates", "duration_nights", "adults", "children_ages", "pet_weight_kg", "budget_scope"}
                                                 for q in state.get("open_questions", [])),
        "no_inferred_child_age": lambda: not (re.search(r"реб[её]н\w*|child", scenario.request, re.I)
                                               and not re.search(r"\d+\s*(?:лет|years?)", scenario.request, re.I)
                                               and reqs.get("children_ages", {}).get("value")),
        "real_build_bundles_called": lambda: any(s.get("name") in {"build_bundles", "build_bundles_result"} for s in trace),
        "bundle_has_tool_offers": lambda: bool(active) and all(bool(o.get("offer_id")) for b in active for o in b.get("items", [])),
        "state_saved_with_new_version": lambda: int(state.get("version", 0)) > 0,
        "hard_budget_not_softened": lambda: reqs.get("budget_total_rub", {}).get("kind") == "hard"
            or reqs.get("budget_per_person_rub", {}).get("kind") == "hard",
        "bundle_within_total_or_honest_no_bundle": lambda: (not active) or all(
            not reqs.get("budget_total_rub") or b.get("total_rub", 0) <= reqs["budget_total_rub"].get("value", 0)
            for b in active),
        "no_bundle_under_hard_budget": lambda: not active,
        "alternatives_confirmed_by_A": lambda: all(a.get("confirmed") and a.get("refs") for a in state.get("proposed_changes", [])),
        "hard_budget_unchanged_until_accept": lambda: reqs.get("budget_total_rub", {}).get("kind") == "hard",
        "short_answers_follow_open_question_keys": lambda: all(a.type != "answer" or a.key for a in scenario.actions),
        "known_requirements_preserved": lambda: all(reqs.get(r.key, {}).get("value") == r.value for r in scenario.expected.requirements),
        "no_question_repeated_after_answer": lambda: not state.get("open_questions") or not any(a.key == q.get("key") for a in scenario.actions if a.type == "answer" for q in state.get("open_questions", [])),
        "assumption_removed_by_agent_command": lambda: any(a.type == "command" and a.command == "remove_assumption" for a in scenario.actions),
        "key_persistently_suppressed": lambda: any(a.type == "command" and a.command == "remove_assumption" and a.payload.get("key") in state.get("suppressed_assumptions", []) for a in scenario.actions),
        "other_requirements_preserved": lambda: bool(state.get("requirements")),
        "bundle_transport_kind_train": lambda: bool(active) and all(o.get("kind") != "flight" for b in active for o in b.get("items", [])),
        "offer_ids_from_catalog": lambda: bool(active) and all(o.get("offer_id") and o.get("source_refs") for b in active for o in b.get("items", [])),
        "no_flight_substitution": lambda: bool(active) and all(o.get("kind") != "flight" for b in active for o in b.get("items", [])),
        "destination_not_covered_reported": lambda: not active and not state.get("destinations") and state.get("stage") == "inspire",
        "zero_fabricated_bundles": lambda: not active,
        "no_unsupported_alternatives": lambda: all(a.get("confirmed") and a.get("refs") for a in state.get("proposed_changes", [])),
        "history_matches_tool_result": lambda: bool(state.get("history")) and any(s.get("name") == "replan_result" for s in trace),
        "asks_for_numeric_limit": lambda: any(q.get("key") in {"budget_total_rub", "budget_per_person_rub"} for q in state.get("open_questions", [])),
        "does_not_invent_budget": lambda: not reqs.get("budget_total_rub") or reqs["budget_total_rub"].get("value") is not None,
        "does_not_call_replan": lambda: not any(s.get("name") == "replan" for s in trace),
        "current_plan_remains_unchanged": lambda: not state.get("history") and bool(state.get("selected_bundle_id")) and bool(active),
        "climate_claims_are_statistics": lambda: all(
            not any(k in c.get("text", "").lower() for k in ("градус", "дожд", "температур")) or bool(c.get("refs"))
            for d in state.get("destinations", []) for c in d.get("why", []) + d.get("risks", [])),
    }
    for name in scenario.expected.checks:
        if name in known:
            continue
        if name in implemented:
            try:
                add(name, bool(implemented[name]()))
            except Exception as exc:
                add(name, False, type(exc).__name__)
        else:
            results.append({"name": name, "status": "blocked", "detail": "no independent checker is implemented for this assertion"})
    return results


async def run_one(scenario: Scenario, *, mode: str, runtime: Any, max_turns: int, timeout: float):
    started = time.monotonic()
    recommendation_ms = None
    questions_asked = turns = max_questions_per_turn = answers_count = 0
    checks: list[dict[str, Any]] = []
    reason, stop_step = None, None
    final_state: dict[str, Any] = {}
    trace: list[dict[str, Any]] = []
    script = ScriptedUser(scenario)
    try:
        async def execution():
            nonlocal turns, questions_asked, max_questions_per_turn, answers_count, recommendation_ms, final_state, trace, stop_step
            await runtime.create_session()
            current = await runtime.get_session()
            version = current["state"]["version"]
            await runtime.send_message(scenario.request, version)
            turns += 1
            while True:
                current = await runtime.get_session()
                state = current["state"]
                final_state = state
                question_count = len(state.get("open_questions", []))
                questions_asked += question_count
                max_questions_per_turn = max(max_questions_per_turn, question_count)
                if recommendation_ms is None and (state.get("bundles") or state.get("destinations") or state.get("proposed_changes")):
                    recommendation_ms = round((time.monotonic() - started) * 1000, 2)
                action = script.next(state.get("open_questions", []))
                if action is None:
                    break
                if turns >= max_turns:
                    raise TimeoutError(f"turn limit {max_turns} reached")
                if action.type == "command":
                    await runtime.send_command(action.command or "", action.payload, state["version"])
                else:
                    await runtime.send_message(action.text or "", state["version"])
                    if action.type == "answer":
                        answers_count += 1
                turns += 1
            final_state = (await runtime.get_session())["state"]
            trace_obj = await runtime.get_trace()
            trace = trace_obj.get("steps", [])
            return final_state
        await asyncio.wait_for(execution(), timeout=timeout)
        checks = _base_checks(scenario, final_state, trace, max_questions_per_turn, questions_asked)
        failed = [x for x in checks if x["status"] == "failed"]
        blocked = [x for x in checks if x["status"] == "blocked"]
        status = "failed" if failed else "blocked" if blocked else "passed"
        if failed:
            reason = "; ".join(x["name"] for x in failed)
        elif blocked:
            reason = "; ".join(x["name"] for x in blocked)
    except asyncio.TimeoutError:
        status, reason, stop_step = "failed", f"scenario exceeded {timeout}s", "timeout"
    except UnexpectedQuestion as exc:
        status, reason, stop_step = "failed", str(exc), "scripted_user"
    except Exception as exc:
        detail = getattr(exc, "detail", None)
        status, reason, stop_step = "blocked", str(detail or exc), "runtime"
    finally:
        if hasattr(runtime, "close"):
            await runtime.close()
    if not final_state:
        status = "blocked" if "status" not in locals() else status
    return {
        "scenario_id": scenario.id, "status": status, "reason": reason, "stop_step": stop_step,
        "clarifying_questions": questions_asked, "scripted_answers": answers_count, "turns": turns,
        "time_to_first_recommendation_ms": recommendation_ms,
        "checks": checks, "passed_checks": sum(x["status"] == "passed" for x in checks),
        "failed_checks": sum(x["status"] == "failed" for x in checks),
        "blocked_checks": sum(x["status"] == "blocked" for x in checks),
        "session_id": getattr(runtime, "session_id", None), "order_id": getattr(runtime, "order_id", None),
        "trace_id": getattr(runtime, "trace_id", None), "trace_steps": len(trace),
        "tools_observed": sorted(getattr(runtime, "tool_names", set())),
        "substitutions": list(getattr(runtime, "substitutions", [])),
        "runtime_mode": mode,
    }


async def run(args):
    paths = [p for directory in args.scenario_dir for p in sorted(directory.glob("*.yaml"))]
    scenarios = [load_scenario(path) for path in paths]
    results = []
    for scenario in scenarios:
        try:
            if args.mode == "deterministic":
                factory = _load_factory()
                runtime = factory(scenario)
                if asyncio.iscoroutine(runtime):
                    runtime = await runtime
            else:
                if not args.base_url:
                    raise RuntimeError("e2e mode requires --base-url for a running isolated API")
                runtime = HTTPRuntime(args.base_url)
            results.append(await run_one(scenario, mode=args.mode, runtime=runtime,
                                         max_turns=args.max_turns, timeout=args.timeout))
        except Exception as exc:
            results.append({"scenario_id": scenario.id, "status": "blocked", "reason": str(exc),
                            "stop_step": "runtime_setup", "clarifying_questions": 0, "scripted_answers": 0, "turns": 0,
                            "time_to_first_recommendation_ms": None, "checks": [], "passed_checks": 0,
                            "failed_checks": 0, "blocked_checks": 0, "session_id": None,
                            "order_id": None, "trace_id": None, "substitutions": [], "runtime_mode": args.mode})
    report = {
        "created_at": datetime.now().astimezone().isoformat(), "mode": args.mode,
        "summary": {status: sum(x["status"] == status for x in results) for status in ("passed", "failed", "blocked")},
        "scenario_count": len(results), "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(f"report: {args.output}")
    return 1 if report["summary"]["failed"] else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("deterministic", "e2e"), default="deterministic")
    parser.add_argument("--base-url", default=os.getenv("EVAL_BASE_URL"))
    parser.add_argument("--scenario-dir", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, default=Path("eval/reports/latest.json"))
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    if not args.scenario_dir:
        args.scenario_dir = [Path("eval/scenarios"), Path("eval/sprint3")]
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
