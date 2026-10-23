"""Sprint 1 and 2 agent state machine: clarify -> inspire -> select."""
from __future__ import annotations

import importlib
import inspect
import re
from datetime import date, datetime, time
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from app.agent.destination_search import (
    AlternativeChange, ConfirmedAlternative, DestinationIntegrationUnavailable,
    SuggestionResult, suggest_destinations as track_a_suggest_destinations,
)
from app.agent.explain import _numbers, explain_bundle_claims, explain_destination, resolve_evidence
from app.agent.llm import LLMClient
from app.agent.requirements import (
    ParsedRequest, REQUIREMENT_KEYS, merge_requirement_changes, missing_concrete_inputs, next_questions,
    normalize_contextual_changes, validate_extraction,
)
from app.agent.replanning import track_a_replan, track_a_validate_bundle
from app.state.models import Bundle as StateBundle
from app.state.models import Check, Claim, HistoryEntry, OpenQuestion, PendingReplan, Requirement, TripState

# Re-export kept for the sprint 1 import contract and existing tests.
__all__ = ["Orchestrator", "ParsedRequest", "CatalogIntegrationUnavailable", "OrchestrationLimitError"]


Builder = Callable[[dict[str, Any], TripState], Any | Awaitable[Any]]
Suggester = Callable[[dict[str, Any], TripState], Any | Awaitable[Any]]


class OrchestrationLimitError(RuntimeError):
    """The per-turn operation budget was reached before a result was ready."""


class InvalidAgentCommand(ValueError):
    pass


# Backward-compatible name used by the sprint 1 API error mapper.
CatalogIntegrationUnavailable = DestinationIntegrationUnavailable


def track_a_build_bundles(requirements: dict[str, Any], state: TripState) -> list[StateBundle]:
    """Call the real deterministic A tool; this layer never calculates offers."""
    try:
        module = importlib.import_module("app.planning.build")
        build = getattr(module, "build_bundles")
    except (ImportError, AttributeError) as exc:
        raise CatalogIntegrationUnavailable("Track A build_bundles is unavailable") from exc
    return [StateBundle.model_validate(item) for item in build(requirements=requirements, state=state)]


class Orchestrator:
    def __init__(
        self,
        llm: LLMClient,
        *,
        bundle_builder: Builder | None = None,
        destination_suggester: Suggester | None = None,
        no_bundle_explainer: Callable[[dict[str, Any], TripState], list[str]] | None = None,
        replanner: Callable[[TripState, dict[str, Any]], Any | Awaitable[Any]] | None = None,
        bundle_validator: Callable[[dict[str, Any], TripState], Any] | None = None,
        now: datetime | Callable[[], datetime] | None = None,
        today: Callable[[], date] | None = None,
        timezone_name: str = "Europe/Moscow",
        max_steps: int = 12,
    ):
        self.llm = llm
        self.bundle_builder = bundle_builder or track_a_build_bundles
        self.destination_suggester = destination_suggester or track_a_suggest_destinations
        self.no_bundle_explainer = no_bundle_explainer
        self.replanner = replanner or track_a_replan
        self.bundle_validator = bundle_validator or track_a_validate_bundle
        self.now_source = now
        self.today_source = today
        self.timezone = ZoneInfo(timezone_name)
        self.max_steps = max(1, max_steps)

    def local_now(self) -> datetime:
        if self.now_source is not None:
            value = self.now_source() if callable(self.now_source) else self.now_source
            return value.replace(tzinfo=self.timezone) if value.tzinfo is None else value.astimezone(self.timezone)
        if self.today_source is not None:
            return datetime.combine(self.today_source(), time.min, tzinfo=self.timezone)
        return datetime.now(self.timezone)

    async def parse_request(self, text: str, state: TripState) -> ParsedRequest:
        """Extract only changes, using open questions to interpret short answers."""
        known = [r.model_dump(mode="json") for r in state.requirements]
        questions = [q.model_dump(mode="json") for q in state.open_questions]
        suppressed = list(state.suppressed_assumptions)
        system = f"""You extract a typed delta from one travel-chat message. Return JSON:
{{"requirements":[{{"key":"...","value":null,"kind":"hard|soft|assumption|unknown","source":"user_text|agent","quote":null,"label":null}}],"remove_keys":[]}}
Local reference time: {self.local_now().isoformat()} (timezone {self.timezone.key}).
Current requirements: {known}
Open questions, in priority order: {questions}
Assumption keys the user removed; do not restore them: {suppressed}

Supported keys: origin, destination, dates ({{start,end}} ISO dates when explicit), travel_month (1..12), duration_nights,
date_flex_days, adults, children_count, children_ages (list of ages), pets, pet_weight_kg,
budget_total_rub, budget_per_person_rub, budget_scope, transport (flight|train|either), max_stops,
travel_time_preference, max_duration_min, trip_style, lodging_stars, meal_preference, room_preference,
assumption_child_flight_time, assumption_child_menu, budget_preference.

Rules: hard = explicitly required/strict; soft = explicit preference or qualitative wording; assumption = your clearly labeled
inference only (source agent, quote null); unknown = value null. Every hard/soft user_text quote must be an exact substring
of this latest message. Return only changed/answered requirements, never repeat unchanged known fields. An answer to an open
question maps to that question's key: "Москва" -> origin when asked origin, "5 лет" -> children_ages, "собака" -> pets when asked animal kind, "10 кг" -> pet_weight_kg, "двое" -> adults only
when that question is open, "с 10 по 17" is incomplete unless month is known. Do not infer adult count or child age from "с ребёнком".
Qualitative "недорого" is soft budget_preference, never a numeric limit. "Строго до N" is hard. Ask/mark budget_scope unknown
unless the user says whether the amount is for the whole trip or per person. "Без долгих перелётов" is a soft preference and
does not create an exact hour limit. Distinguish month + duration from exact dates. State pet presence only if said; do not
invent weight. Relative date changes are deltas from current dates. If a pet is added, ask its weight before search. An answer
to pet_weight_kg maps to that key. “Бюджет меньше” without an amount is unknown and requires clarification. User corrections
replace old values by the same key. Use remove_keys only for a clearly requested removal.
Never produce offer ids, prices, feasibility, weather, visa, or search results. No reasoning text."""
        parsed = await self.llm.chat_json(
            [{"role": "system", "content": system}, {"role": "user", "content": text}],
            ParsedRequest, fast=True,
        )
        return validate_extraction(parsed, text)

    def _year_resolve(self, requirements: list[Requirement], suppressed: list[str] | tuple[str, ...] = ()) -> list[Requirement]:
        by_key = {r.key: r for r in requirements if not (r.kind == "assumption" and r.key in suppressed)}
        date_req = by_key.get("dates")
        month_names = {
            "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
            "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
            "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
            "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
        }
        quote = str(date_req.quote or "") if date_req else ""
        pattern = re.search(
            r"(?:с\s*)?(?P<start>\d{1,2})\s*(?:[-–—]|по|to)\s*(?P<end>\d{1,2})\s+(?P<month>[а-яё]+|[a-z]+)(?:\s+(?P<year>20\d{2}))?",
            quote.lower(),
        )
        context_month_req = by_key.get("travel_month")
        context_month = None
        if context_month_req:
            context_month = month_names.get(str(context_month_req.value).lower(), context_month_req.value if isinstance(context_month_req.value, int) else None)
        start_day = end_day = month = None
        if pattern:
            start_day, end_day = int(pattern.group("start")), int(pattern.group("end"))
            month = month_names.get(pattern.group("month"))
        elif date_req and context_month:
            day_range = re.search(r"(?:с\s*)?(?P<start>\d{1,2})\s*(?:[-–—]|по|to)\s*(?P<end>\d{1,2})\b", quote.lower())
            if day_range:
                start_day, end_day, month = int(day_range.group("start")), int(day_range.group("end")), int(context_month)
        explicit_year = bool(re.search(r"\b20\d{2}\b", quote))
        if start_day and end_day and month and date_req:
                year = int(pattern.group("year")) if pattern and pattern.group("year") else self.local_now().year
                if not explicit_year and (month, end_day) < (self.local_now().month, self.local_now().day):
                    year += 1
                try:
                    start = date(year, month, start_day)
                    end = date(year, month, end_day)
                except ValueError:
                    return requirements
                by_key["dates"] = date_req.model_copy(update={"value": {"start": start.isoformat(), "end": end.isoformat()}})
                if explicit_year:
                    by_key.pop("travel_year", None)
                elif "travel_year" not in suppressed:
                    by_key["travel_year"] = Requirement(
                        key="travel_year", value=year, kind="assumption", source="calendar_resolution", quote=None,
                        label=f"Предполагаемый год: {year}",
                    )
        else:
            month_req = by_key.get("travel_month")
            if month_req and not explicit_year and "travel_year" not in suppressed:
                month = month_names.get(str(month_req.value).lower(), month_req.value if isinstance(month_req.value, int) else None)
                if isinstance(month, int) and 1 <= month <= 12:
                    year = self.local_now().year
                    if month < self.local_now().month:
                        year += 1
                    by_key["travel_year"] = Requirement(
                        key="travel_year", value=year, kind="assumption", source="calendar_resolution", quote=None,
                        label=f"Предполагаемый год: {year}",
                    )
        return list(by_key.values())

    @staticmethod
    def _destination_requirement(state: TripState) -> Requirement | None:
        return next((r for r in state.requirements if r.key == "destination" and r.value not in (None, "")
                     and r.kind in {"hard", "soft"}), None)

    @staticmethod
    def _requirements_map(state: TripState, *, include_assumptions: bool = True) -> dict[str, Any]:
        return {
            r.key: r.value for r in state.requirements
            if r.kind != "unknown" and (include_assumptions or r.kind != "assumption")
        }

    def _record(self, trace: list[dict[str, Any]], item: dict[str, Any]) -> None:
        if len(trace) >= self.max_steps:
            raise OrchestrationLimitError(f"Ход остановлен: достигнут лимит {self.max_steps} шагов.")
        trace.append(item)

    async def _call(self, tool: Callable[..., Any], requirements: dict[str, Any], state: TripState) -> Any:
        result = tool(requirements, state)
        return await result if inspect.isawaitable(result) else result

    @staticmethod
    def _select_questions(state: TripState) -> list[OpenQuestion]:
        if Orchestrator._destination_requirement(state):
            questions = missing_concrete_inputs(state.requirements)
        else:
            questions = next_questions(
                state.requirements,
                action="suggest_destinations",
                required_inputs=("origin", "adults", "travel_window"),
            )
        # A child needs a real age for fare/capacity search; no catalog default age here.
        count = next((r for r in state.requirements if r.key == "children_count" and r.value is not None), None)
        ages = next((r for r in state.requirements if r.key == "children_ages" and r.value not in (None, [])), None)
        if count and not ages and not any(q.key in {"children_ages", "children_count"} for q in questions):
            questions.append(OpenQuestion(key="children_ages", question="Сколько лет каждому ребёнку?", why="Возраст влияет на тариф и вместимость."))
        pets = next((r for r in state.requirements if r.key == "pets" and r.value not in (None, False, "", [])), None)
        weight = next((r for r in state.requirements if r.key == "pet_weight_kg" and r.value not in (None, "")), None)
        pet_value = pets.value if pets else None
        pet_kind_known = (
            isinstance(pet_value, dict) and pet_value.get("kind") in {"dog", "cat", "other"}
        ) or (isinstance(pet_value, str) and bool(re.search(r"dog|cat|собак|п[её]с|кош|кот", pet_value, re.I)))
        if pets and not pet_kind_known:
            questions.append(OpenQuestion(key="pets", question="Какое животное едет?", why="Правила перевозки зависят от вида животного.", options=["Собака", "Кошка"]))
        elif pets and not weight:
            questions.append(OpenQuestion(key="pet_weight_kg", question="Сколько весит животное?", why="Вес влияет на правила перевозчика и вместимость трансфера."))
        return questions[:2]

    def _merge(self, state: TripState, parsed: ParsedRequest) -> TripState:
        requirements, suppressed = merge_requirement_changes(
            state.requirements, parsed.requirements, remove_keys=parsed.remove_keys,
            suppressed_assumptions=state.suppressed_assumptions,
        )
        requirements = self._year_resolve(requirements, suppressed)
        candidate = state.model_copy(deep=True, update={
            "requirements": requirements,
            "suppressed_assumptions": suppressed,
            "open_questions": [],
        })
        changed = set(parsed.remove_keys) | {r.key for r in parsed.requirements}
        if changed & {"origin", "destination", "dates", "travel_month", "duration_nights", "date_flex_days", "adults", "children_count", "children_ages", "pets", "pet_weight_kg", "budget_total_rub", "budget_per_person_rub", "budget_scope", "transport", "max_stops", "max_duration_min"}:
            selected = next((b for b in state.bundles if b.id == state.selected_bundle_id), None)
            pending = (state.pending_replan.model_copy(deep=True) if state.pending_replan else
                       PendingReplan(source_bundle=selected, previous_requirements=state.requirements) if selected else None)
            if pending:
                labels = [r.quote or r.label or f"{r.key}: {r.value}" for r in parsed.requirements]
                labels.extend(f"удалено: {key}" for key in parsed.remove_keys)
                if labels:
                    pending.change = "; ".join(filter(None, [pending.change, *labels]))
            candidate.pending_replan = pending
            candidate.destinations = []
            candidate.proposed_changes = []
            candidate.bundles = []
            candidate.selected_bundle_id = None
            if "destination" in changed:
                destination = next((r.value for r in requirements if r.key == "destination" and r.value), None)
                candidate.selected_destination_id = str(destination) if destination is not None else None
        candidate.stage = "clarify"
        return candidate

    @staticmethod
    def _requirement_diff(previous: list[Requirement], current: list[Requirement]) -> dict[str, Any]:
        before = {r.key: r for r in previous}
        after = {r.key: r for r in current}
        upserts = [
            {"key": r.key, "value": r.value, "kind": r.kind}
            for key, r in after.items()
            if before.get(key) is None or (before[key].value, before[key].kind) != (r.value, r.kind)
        ]
        return {"upserts": upserts, "remove_keys": sorted(before.keys() - after.keys())}

    @staticmethod
    def _replan_item_ref(item: Any) -> str:
        if isinstance(item, str):
            return item
        if not isinstance(item, dict):
            raise TypeError("Track A kept/replaced entries must be strings or mappings")
        if item.get("ref"):
            return str(item["ref"])
        if item.get("offer_id"):
            return f"offer:{item['offer_id']}"
        if item.get("object_id"):
            return f"object:{item.get('kind', 'catalog')}:{item['object_id']}"
        if item.get("item_id"):
            return f"object:{item.get('kind', 'catalog')}:{item['item_id']}"
        identifier = item.get("id")
        if identifier and str(identifier).startswith(("offer:", "object:")):
            return str(identifier)
        if identifier and item.get("type") == "offer":
            return f"offer:{identifier}"
        if identifier and item.get("kind"):
            return f"object:{item['kind']}:{identifier}"
        if identifier:
            raise ValueError("Track A replan entries with id must identify kind or type")
        raise ValueError("Track A replan entries require ref, offer_id, object_id, item_id or id")

    async def _replan(self, state: TripState, trace: list[dict[str, Any]]) -> tuple[TripState, str]:
        pending = state.pending_replan
        if pending is None:
            return await self._continue(state, trace)
        questions = self._select_questions(state)
        if questions:
            state.open_questions = questions
            state.stage = "clarify"
            return state, "Чтобы перепроверить выбранный комплект, уточните: " + "; ".join(q.question for q in questions)
        diff = self._requirement_diff(pending.previous_requirements, state.requirements)
        source_state = state.model_copy(deep=True, update={
            "bundles": [pending.source_bundle], "selected_bundle_id": pending.source_bundle.id,
        })
        self._record(trace, {"step_type": "state", "name": "requirements_changed",
                             "input": {"previous": [r.model_dump(mode="json") for r in pending.previous_requirements]},
                             "output": diff, "status": "ok"})
        self._record(trace, {"step_type": "tool", "name": "replan", "input": diff, "output": None, "status": "calling"})
        result = dict(await self._call(self.replanner, source_state, diff))
        bundles = []
        for raw_bundle in result.get("bundles") or []:
            bundle = raw_bundle if isinstance(raw_bundle, StateBundle) else StateBundle.model_validate(raw_bundle)
            bundles.append(await self._complete_bundle_explanation(bundle, state, trace))
        for bundle in bundles:
            if sum(item.total_rub for item in bundle.items) != bundle.total_rub:
                raise ValueError(f"Track A replan bundle {bundle.id} total does not match offer totals")
        old_total = pending.source_bundle.total_rub
        selected = next((b for b in bundles if b.id == result.get("selected_bundle_id")), None)
        if selected is None and len(bundles) == 1:
            selected = bundles[0]
        delta = result.get("price_delta_rub")
        if delta is not None:
            if selected is None:
                raise ValueError("Track A replan must identify selected_bundle_id when returning a price delta for multiple bundles")
            if float(delta) != float(selected.total_rub) - float(old_total):
                raise ValueError("Track A replan price_delta_rub does not match the old and new bundle totals")
        kept_entries, replaced_entries = result.get("kept") or [], result.get("replaced") or []
        kept = [self._replan_item_ref(x) for x in kept_entries]
        replaced = [self._replan_item_ref(x) for x in replaced_entries]
        item_reasons: dict[str, str] = {}
        for item in [*kept_entries, *replaced_entries]:
            if isinstance(item, dict) and (item.get("why") or item.get("reason")):
                item_reasons[self._replan_item_ref(item)] = str(item.get("why") or item.get("reason"))
        reasons = []
        for reason in result.get("reasons") or []:
            if isinstance(reason, dict):
                message = reason.get("why") or reason.get("reason")
                if message:
                    if reason.get("ref") or reason.get("offer_id") or reason.get("object_id") or reason.get("item_id") or reason.get("id"):
                        item_reasons[self._replan_item_ref(reason)] = str(message)
                    reasons.append(str(message))
            else:
                reasons.append(str(reason))
        state.history.append(HistoryEntry(
            version=state.version + 1, change=pending.change or ", ".join(r["key"] for r in diff.get("upserts", [])),
            kept=kept, replaced=replaced, price_delta_rub=delta, reasons=reasons, item_reasons=item_reasons,
            previous_bundle=pending.source_bundle.model_dump(mode="json"),
            resulting_bundle_id=selected.id if selected else None,
        ))
        state.bundles, state.selected_bundle_id = bundles, None
        state.pending_replan, state.open_questions = None, []
        blocking = any(any(c.status == "block" for c in b.checks) for b in bundles)
        state.stage = "assemble" if blocking else ("compare" if len(bundles) > 1 else "select" if bundles else "inspire")
        self._record(trace, {"step_type": "tool", "name": "replan_result", "input": {"source_bundle_id": pending.source_bundle.id},
                             "output": {"bundle_ids": [b.id for b in bundles], "kept": kept, "replaced": replaced,
                                        "reasons": reasons, "price_delta_rub": delta}, "status": "ok"})
        if not bundles:
            notes = " ".join(str(x) for x in result.get("notes", [])).strip()
            return state, (notes + " " if notes else "") + "После изменения подходящих комплектов нет. Прежний комплект сохранён только в истории и недоступен для оформления."
        if blocking:
            blocks = [c.text for b in bundles for c in b.checks if c.status == "block"]
            return state, "Пересборка вернула блокирующие проверки: " + "; ".join(blocks) + ". Исправьте условия и пересоберите план."
        delta_text = f"{float(delta):+,.0f} ₽".replace(",", " ") if delta is not None else "не рассчитана инструментом"
        return state, f"Комплект перепроверен инструментом A. Сохранено: {', '.join(kept) if kept else 'нет'}; заменено: {', '.join(replaced) if replaced else 'нет'}. Разница стоимости: {delta_text}. Выберите актуальный вариант."

    async def _continue_changed(self, state: TripState, trace: list[dict[str, Any]]) -> tuple[TripState, str]:
        return await self._replan(state, trace) if state.pending_replan else await self._continue(state, trace)

    @staticmethod
    def _source_facts(raw: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        evidence = raw.get("evidence", raw.get("facts", []))
        facts, sources = resolve_evidence(evidence if isinstance(evidence, list) else [], destination=raw)
        return [f.model_dump(mode="json") for f in facts], sources

    async def _enrich_candidate(self, raw: dict[str, Any], trace: list[dict[str, Any]]) -> dict[str, Any]:
        evidence, sources = self._source_facts(raw)
        raw = dict(raw)
        raw["evidence"] = evidence
        why, risks, source_map, explain_trace = await explain_destination(self.llm, raw)
        self._record(trace, explain_trace)
        raw["why"] = [c.model_dump(mode="json") for c in why]
        raw["risks"] = [c.model_dump(mode="json") for c in risks]
        raw["sources"] = source_map or sources
        return raw

    @staticmethod
    def _explain_bundle(bundle: StateBundle) -> StateBundle:
        """Keep only source-backed A claims and add a deterministic total claim."""
        offer_refs = {f"offer:{item.offer_id}" for item in bundle.items}
        bundle.checks = [
            check.model_copy(update={"id": check.id or f"check:{bundle.id}:{index}",
                                     "refs": check.refs or sorted(offer_refs)})
            for index, check in enumerate(bundle.checks)
        ]
        check_refs = {ref for check in bundle.checks for ref in [*check.refs, check.id] if ref}
        valid_refs = offer_refs | check_refs
        source_numbers: dict[str, set[str]] = {}
        for item in bundle.items:
            item_numbers = (
                _numbers(item.total_rub) | _numbers(item.unit_price_rub) | _numbers(item.units)
                | _numbers(item.quantity) | _numbers(item.variant) | _numbers(item.cancel_policy.model_dump(mode="json") if item.cancel_policy else None)
            )
            duration = item.variant.get("duration_min") if isinstance(item.variant, dict) else None
            if isinstance(duration, (int, float)):
                item_numbers.update(_numbers(int(duration) // 60))
            source_numbers[f"offer:{item.offer_id}"] = item_numbers
        for check in bundle.checks:
            source_numbers.setdefault(check.id, set()).update(_numbers(check.text))
            for ref in check.refs:
                source_numbers.setdefault(ref, set()).update(_numbers(check.text))
        def supported(claim):
            return bool(claim.refs) and all(ref in valid_refs for ref in claim.refs) and _numbers(claim.text).issubset(
                set().union(*(source_numbers.get(ref, set()) for ref in claim.refs))
            )
        bundle.advantages = [c for c in bundle.advantages if supported(c)]
        bundle.tradeoffs = [c for c in bundle.tradeoffs if supported(c)]
        bundle.risks = [c for c in bundle.risks if supported(c)]
        bundle.advantages.extend(
            Claim(text=f"Проверка A «{check.kind}»: пройдена.", refs=[check.id])
            for check in bundle.checks if check.status == "ok" and check.id
        )
        bundle.risks.extend(
            Claim(text=f"Проверка A «{check.kind}»: предупреждение.", refs=[check.id])
            for check in bundle.checks if check.status == "warn" and check.id
        )
        kinds = {item.kind for item in bundle.items}
        composition = ", ".join(x for x in ("дорога" if kinds & {"flight", "train"} else None,
                                                "отель" if "hotel" in kinds else None,
                                                "трансфер" if "transfer" in kinds else None) if x)
        if not bundle.advantages and offer_refs:
            bundle.advantages = [Claim(text=f"Состав комплекта по предложениям: {composition or 'есть предложения каталога'}.", refs=sorted(offer_refs))]
            total = sum(item.total_rub for item in bundle.items)
            if total == bundle.total_rub:
                bundle.advantages.append(Claim(
                    text=f"Общая стоимость по предложениям: {total:,.0f} ₽".replace(",", " "),
                    refs=sorted(offer_refs),
                ))
        return bundle

    async def _complete_bundle_explanation(self, bundle: StateBundle, state: TripState,
                                           trace: list[dict[str, Any]]) -> StateBundle:
        bundle = self._explain_bundle(bundle)
        try:
            explained, step = await explain_bundle_claims(self.llm, bundle, state.requirements)
            self._record(trace, step)
            for field in ("advantages", "tradeoffs", "risks"):
                known = {(c.text, tuple(c.refs)) for c in getattr(bundle, field)}
                additions = [c for c in getattr(explained, field) if (c.text, tuple(c.refs)) not in known]
                setattr(bundle, field, [*getattr(bundle, field), *additions])
        except Exception as exc:
            # The facts and deterministic claims from A are sufficient to keep
            # the plan honest if optional LLM phrasing fails.
            self._record(trace, {"step_type": "llm", "name": "explain_bundle",
                                 "input": {"bundle_id": bundle.id},
                                 "output": {"fallback": "tool-backed claims", "error_type": type(exc).__name__},
                                 "status": "fallback"})
        return bundle

    async def _validated_alternatives(self, raw_alternatives: list[ConfirmedAlternative]) -> list[dict[str, Any]]:
        accepted = []
        for raw in raw_alternatives[:2]:
            data = raw.model_dump(mode="json")
            facts, sources = self._source_facts(data)
            valid_refs = set(sources)
            if not raw.confirmed or not raw.refs or any(ref not in valid_refs for ref in raw.refs):
                continue
            if any(change.key not in {"origin", "destination", "dates", "travel_month", "duration_nights", "budget_total_rub", "budget_per_person_rub", "transport", "max_stops"} for change in raw.changes):
                continue
            data["evidence"] = facts
            data["sources"] = sources
            accepted.append(data)
        return accepted

    @staticmethod
    def _state_diff(before: TripState, after: TripState) -> dict[str, Any]:
        a, b = before.model_dump(mode="json"), after.model_dump(mode="json")
        changed = [key for key in b if a.get(key) != b.get(key)]
        return {"step_type": "state", "name": "state_diff", "input": {"from_version": before.version},
                "output": {"changed_fields": changed, "to_stage": after.stage}, "status": "ok"}

    async def _suggest(self, state: TripState, trace: list[dict[str, Any]], *, explicit_destination: bool = False) -> tuple[TripState, str]:
        questions = self._select_questions(state)
        if questions:
            state.open_questions = questions
            state.stage = "clarify"
            return state, "Чтобы продолжить, уточните: " + "; ".join(q.question for q in questions)
        state.open_questions = []
        old_stage = state.stage
        state.stage = "inspire"
        self._record(trace, {"step_type": "state", "name": "stage_transition", "input": {"from": old_stage},
                             "output": {"to": "inspire"}, "status": "ok"})
        # Tool filters receive only explicit hard/soft facts. A may read agent
        # assumptions from state for ranking, but must not treat them as filters.
        requirements = self._requirements_map(state, include_assumptions=False)
        self._record(trace, {"step_type": "tool", "name": "suggest_destinations", "input": requirements,
                             "output": None, "status": "calling"})
        result = await self._call(self.destination_suggester, requirements, state)
        if isinstance(result, dict):
            result = SuggestionResult.model_validate(result)
        if not isinstance(result, SuggestionResult):
            result = SuggestionResult.model_validate(result)
        raw_destinations = result.destinations
        # A hard destination is never silently replaced by another destination.
        if explicit_destination:
            raw_destinations = []
        candidates = []
        for raw in raw_destinations[:3]:
            if raw.get("feasible") is not True:
                continue
            candidates.append(await self._enrich_candidate(raw, trace))
        alternatives = await self._validated_alternatives(result.alternatives)
        state.destinations = candidates
        state.proposed_changes = alternatives
        state.stage = "select" if candidates or alternatives else "inspire"
        self._record(trace, {"step_type": "tool", "name": "suggest_destinations_result", "input": {"count": len(result.destinations)},
                             "output": {"destination_ids": [c.get("id") for c in candidates],
                                        "alternative_ids": [a.get("id") for a in alternatives],
                                        "notes": result.notes}, "status": "ok"})
        if candidates:
            return state, f"Подобрал {len(candidates)} выполнимых направлени(е/я). Выберите одно, чтобы собрать комплект."
        if alternatives:
            labels = "; ".join(a.get("label", a["id"]) for a in alternatives)
            return state, f"Под все жёсткие условия вариантов нет. Инструмент расчёта подтвердил изменения: {labels}. Ничего не изменено — выберите конкретный вариант."
        notes = " ".join(result.notes).strip()
        return state, (notes + " " if notes else "") + "Подтверждённых выполнимых направлений и изменений не найдено. Жёсткие условия оставлены без изменений."

    async def _build(self, state: TripState, trace: list[dict[str, Any]]) -> tuple[TripState, str]:
        questions = self._select_questions(state)
        if questions:
            state.open_questions = questions
            state.stage = "clarify"
            return state, "Чтобы собрать поездку, уточните: " + "; ".join(q.question for q in questions)
        requirements = self._requirements_map(state, include_assumptions=False)
        search_state = state.model_copy(deep=True, update={
            "requirements": [r for r in state.requirements if r.kind not in {"unknown", "assumption"}],
        })
        self._record(trace, {"step_type": "tool", "name": "build_bundles", "input": requirements, "output": None, "status": "calling"})
        bundles = await self._call(self.bundle_builder, requirements, search_state)
        state.bundles = []
        for raw_bundle in bundles or []:
            bundle = raw_bundle if isinstance(raw_bundle, StateBundle) else StateBundle.model_validate(raw_bundle)
            state.bundles.append(await self._complete_bundle_explanation(bundle, state, trace))
        for bundle in state.bundles:
            if sum(item.total_rub for item in bundle.items) != bundle.total_rub:
                raise ValueError(f"Track A bundle {bundle.id} total does not match offer totals")
        state.open_questions = []
        state.stage = "compare" if len(state.bundles) > 1 else "select"
        self._record(trace, {"step_type": "tool", "name": "build_bundles_result", "input": requirements,
                             "output": {"bundle_ids": [b.get("id") if isinstance(b, dict) else getattr(b, "id", None) for b in state.bundles]},
                             "status": "ok"})
        if state.bundles:
            count = len(state.bundles)
            noun = "комплект" if count == 1 else ("комплекта" if count % 10 in {2, 3, 4} and count % 100 not in {12, 13, 14} else "комплектов")
            verb = "проверенный" if count == 1 else ("проверенных" if noun == "комплектов" else "проверенных")
            return state, f"Нашёл {count} {noun}, {verb} по условиям. Выберите один, чтобы продолжить."
        notes: list[str] = []
        try:
            if self.no_bundle_explainer is not None:
                notes = list(self.no_bundle_explainer(requirements, state))
            else:
                module = importlib.import_module("app.planning.build")
                explain_empty = getattr(module, "explain_no_bundles")
                notes = list(explain_empty(requirements=requirements, state=state))
            self._record(trace, {"step_type": "tool", "name": "explain_no_bundles", "input": requirements,
                                 "output": notes, "status": "ok"})
        except (ImportError, AttributeError):
            notes = []
        try:
            # A owns confirmed alternatives/minimal changes. A's destination tool
            # is called with the fixed destination; this adapter never invents one.
            state, search_text = await self._suggest(state, trace, explicit_destination=True)
            if state.proposed_changes:
                return state, (" ".join(notes) + " " if notes else "") + search_text
        except DestinationIntegrationUnavailable:
            pass
        state.stage = "inspire"
        msg = " ".join(notes) if notes else "Под указанными условиями комплектов не найдено."
        return state, msg + " Подтверждённых альтернатив нет; жёсткие требования сохранены. вымышленные предложения не показываю."

    async def _continue(self, state: TripState, trace: list[dict[str, Any]]) -> tuple[TripState, str]:
        destination = self._destination_requirement(state)
        if destination is not None:
            return await self._build(state, trace)
        return await self._suggest(state, trace)

    @staticmethod
    def _affirmative(text: str) -> bool:
        return bool(re.fullmatch(r"\s*(да|согласен|согласна|принимаю|ок|okay|yes|беру)\s*[.!]?\s*", text.lower()))

    @staticmethod
    def _match_alternative(text: str, alternatives: list[dict[str, Any]], parsed: ParsedRequest | None = None) -> tuple[dict[str, Any] | None, bool]:
        if not alternatives:
            return None, False
        norm = re.sub(r"[^\wа-яё]+", " ", text.lower()).strip()
        matches = []
        for index, alternative in enumerate(alternatives):
            alt_id = str(alternative.get("id", "")).lower()
            label = str(alternative.get("label", "")).lower()
            if alt_id and alt_id in text.lower() or (label and label in text.lower()):
                matches.append(alternative)
            elif re.search(r"\b(?:перв(?:ый|ого|ую)|вариант\s*1)\b", norm) and index == 0:
                matches.append(alternative)
            elif re.search(r"\b(?:втор(?:ой|ого|ую)|вариант\s*2)\b", norm) and index == 1:
                matches.append(alternative)
            elif re.search(r"\b(?:трет(?:ий|ьего|ью)|вариант\s*3)\b", norm) and index == 2:
                matches.append(alternative)
        if parsed and parsed.requirements:
            changes = {(r.key, str(r.value)) for r in parsed.requirements}
            matched = [a for a in alternatives if any((c.get("key"), str(c.get("value"))) in changes for c in a.get("changes", []))]
            if len(matched) == 1:
                return matched[0], False
        if len(matches) == 1:
            return matches[0], False
        if len(matches) > 1:
            return None, True
        if Orchestrator._affirmative(text):
            if len(alternatives) == 1:
                return alternatives[0], False
            return None, True
        return None, False

    def _apply_alternative(self, state: TripState, alternative: dict[str, Any]) -> TripState:
        changes = [Requirement(
            key=change["key"], value=change["value"], kind=change.get("kind", "hard"),
            source="user_edit", quote=None, label=change.get("label") or alternative.get("label"),
        ) for change in alternative.get("changes", [])]
        requirements, suppressed = merge_requirement_changes(
            state.requirements, changes, suppressed_assumptions=state.suppressed_assumptions,
        )
        candidate = state.model_copy(deep=True, update={
            "requirements": self._year_resolve(requirements, suppressed), "suppressed_assumptions": suppressed,
            "proposed_changes": [], "destinations": [], "bundles": [], "open_questions": [], "selected_bundle_id": None,
            "selected_destination_id": None,
        })
        return candidate

    async def prepare_turn(self, state: TripState, text: str) -> tuple[TripState, str, list[dict[str, Any]]]:
        before = state
        parsed = await self.parse_request(text, state)
        parsed, contextual_question = normalize_contextual_changes(parsed, text, state)
        trace: list[dict[str, Any]] = [{
            "step_type": "llm", "name": "parse_request",
            "input": {"text": text, "open_question_keys": [q.key for q in state.open_questions]},
            "output": parsed.model_dump(mode="json"), "status": "ok",
        }]
        accepted, ambiguous = self._match_alternative(text, state.proposed_changes, parsed)
        if ambiguous:
            candidate = state.model_copy(deep=True)
            reply = "Какое из предложенных изменений принять? Назовите вариант или его условие; само слово «да» не выберет один из них."
        elif accepted:
            self._record(trace, {"step_type": "tool", "name": "accept_alternative", "input": {"alternative_id": accepted["id"]},
                                 "output": {"changes": accepted.get("changes", [])}, "status": "ok"})
            candidate = self._apply_alternative(state, accepted)
            candidate, reply = await self._continue(candidate, trace)
        else:
            candidate = self._merge(state, parsed)
            if not parsed.requirements and not parsed.remove_keys and state.proposed_changes:
                candidate = state.model_copy(deep=True)
                reply = "Жёсткие условия не менял. Выберите одно из рассчитанных изменений или уточните запрос."
            elif contextual_question:
                if parsed.requirements or parsed.remove_keys:
                    self._record(trace, {"step_type": "state", "name": "requirements_changed",
                                         "input": {"previous": [r.model_dump(mode="json") for r in state.requirements]},
                                         "output": self._requirement_diff(state.requirements, candidate.requirements),
                                         "status": "ok"})
                candidate.open_questions = [contextual_question]
                candidate.stage = "clarify"
                reply = contextual_question.question
            else:
                candidate, reply = await self._continue_changed(candidate, trace)
        trace.append(self._state_diff(before, candidate))
        return candidate, reply, trace

    async def prepare_command(self, state: TripState, command_type: str, payload: dict[str, Any]) -> tuple[TripState, str, list[dict[str, Any]]]:
        before = state
        trace: list[dict[str, Any]] = []
        if command_type == "edit_requirement":
            key = str(payload.get("key", ""))
            if not key:
                raise InvalidAgentCommand("edit_requirement requires payload.key")
            if key not in REQUIREMENT_KEYS:
                raise InvalidAgentCommand(f"unsupported requirement key: {key}")
            value = payload.get("value")
            kind = payload.get("kind", "hard")
            if kind not in {"hard", "soft"}:
                raise InvalidAgentCommand("edited requirements must be hard or soft")
            changed = Requirement(key=key, value=value, kind=kind, source="user_edit", quote=None, label=payload.get("label"))
            candidate = self._merge(state, ParsedRequest(requirements=[changed]))
            candidate, reply = await self._continue_changed(candidate, trace)
        elif command_type in {"remove_requirement", "remove_assumption"}:
            key = str(payload.get("key", ""))
            if not key:
                raise InvalidAgentCommand(f"{command_type} requires payload.key")
            candidate = self._merge(state, ParsedRequest(remove_keys=[key]))
            candidate, reply = await self._continue_changed(candidate, trace)
        elif command_type == "select_destination":
            destination_id = str(payload.get("destination_id", ""))
            choice = next((x for x in state.destinations if x.get("id") == destination_id and x.get("feasible") is True), None)
            if choice is None:
                raise InvalidAgentCommand("destination_id must identify a currently tool-confirmed feasible destination")
            changed = Requirement(key="destination", value=destination_id, kind="hard", source="user_edit", quote=None,
                                 label=f"Направление: {choice.get('name', destination_id)}")
            requirements, suppressed = merge_requirement_changes(
                state.requirements, [changed], suppressed_assumptions=state.suppressed_assumptions,
            )
            candidate = state.model_copy(deep=True, update={"requirements": requirements, "suppressed_assumptions": suppressed,
                                                             "selected_destination_id": destination_id,
                                                             "open_questions": [], "proposed_changes": [], "bundles": [], "selected_bundle_id": None})
            self._record(trace, {"step_type": "tool", "name": "select_destination", "input": {"destination_id": destination_id},
                                 "output": {"feasible": True}, "status": "ok"})
            candidate, reply = await self._build(candidate, trace)
        elif command_type == "accept_alternative":
            alternative_id = str(payload.get("alternative_id", ""))
            alternative = next((a for a in state.proposed_changes if a.get("id") == alternative_id), None)
            if alternative is None:
                raise InvalidAgentCommand("alternative_id must identify a currently confirmed proposal")
            self._record(trace, {"step_type": "tool", "name": "accept_alternative", "input": {"alternative_id": alternative_id},
                                 "output": {"changes": alternative.get("changes", [])}, "status": "ok"})
            candidate = self._apply_alternative(state, alternative)
            candidate, reply = await self._continue(candidate, trace)
        elif command_type == "select_bundle":
            bundle_id = str(payload.get("bundle_id", ""))
            bundle = next((b for b in state.bundles if b.id == bundle_id), None)
            if bundle is None:
                raise InvalidAgentCommand("bundle_id must identify a bundle in the current state")
            candidate = state.model_copy(deep=True, update={"selected_bundle_id": bundle_id, "stage": "assemble"})
            blocking = [c.text for c in bundle.checks if c.status == "block"]
            reply = ("Комплект выбран для сборки. Блокирующие проверки: " + "; ".join(blocking)
                     if blocking else "Комплект выбран для сборки. Это не подтверждает покупку.")
        elif command_type == "request_checkout":
            bundle = next((b for b in state.bundles if b.id == state.selected_bundle_id), None)
            if bundle is None:
                raise InvalidAgentCommand("select a current bundle before requesting checkout")
            checks = self.bundle_validator(bundle.model_dump(mode="json"), state)
            validated_bundle = bundle.model_copy(update={
                "checks": [c if isinstance(c, Check) else Check.model_validate(c) for c in checks],
            })
            candidate = state.model_copy(deep=True)
            candidate.bundles = [validated_bundle if b.id == bundle.id else b for b in candidate.bundles]
            blocking = [c.text for c in validated_bundle.checks if c.status == "block"]
            if not validated_bundle.checks:
                candidate.stage = "assemble"
                reply = "Инструмент A не вернул подтверждение проверок. Корзину пока нельзя создавать."
            elif blocking:
                candidate.stage = "assemble"
                reply = "План нельзя оформить: " + "; ".join(blocking)
            else:
                candidate.stage = "checkout"
                warnings = [c.text for c in validated_bundle.checks if c.status == "warn"]
                reply = "Проверки не содержат блокирующих проблем. "
                if warnings:
                    reply += "Остаются предупреждения: " + "; ".join(warnings) + ". "
                reply += "Сервер корзины повторно проверит версию и предложения при создании корзины."
            self._record(trace, {"step_type": "tool", "name": "validate_plan", "input": {"bundle_id": bundle.id},
                                 "output": {"checks": [c.model_dump(mode="json") for c in validated_bundle.checks]}, "status": "ok"})
        else:
            raise InvalidAgentCommand(f"Unsupported command type: {command_type}")
        trace.append(self._state_diff(before, candidate))
        return candidate, reply, trace
