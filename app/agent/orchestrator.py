"""Sprint 1 agent path: parse explicit facts, call A's bundle builder, persist."""
from __future__ import annotations

import importlib
import re
from datetime import date
from typing import Any, Awaitable, Callable
from uuid import UUID

from pydantic import BaseModel, Field

from app.agent.llm import LLMClient
from app.state.models import Bundle, OpenQuestion, Requirement, TripState


class ParsedRequest(BaseModel):
    requirements: list[Requirement] = Field(default_factory=list)


class CatalogIntegrationUnavailable(RuntimeError):
    pass


Builder = Callable[[dict[str, Any], TripState], Any | Awaitable[Any]]


def track_a_build_bundles(requirements: dict[str, Any], state: TripState) -> list[Bundle]:
    """Call A's implementation when present. No offer or price is fabricated here."""
    try:
        module = importlib.import_module("app.planning.build")
        build = getattr(module, "build_bundles")
    except (ImportError, AttributeError) as exc:
        raise CatalogIntegrationUnavailable(
            "Track A integration is missing: expected app.planning.build.build_bundles; no catalog stub is enabled"
        ) from exc
    result = build(requirements=requirements, state=state)
    return [item if isinstance(item, Bundle) else Bundle.model_validate(item) for item in result]


class Orchestrator:
    def __init__(
        self, llm: LLMClient, *, bundle_builder: Builder | None = None,
        today: Callable[[], date] = date.today,
    ):
        self.llm = llm
        self.bundle_builder = bundle_builder or track_a_build_bundles
        self.today = today

    async def parse_request(self, text: str) -> ParsedRequest:
        current_date = self.today().isoformat()
        parsed = await self.llm.chat_json(
            [
                {"role": "system", "content": (
                    "Extract only explicitly stated travel requirements from the user's message. "
                    "Return JSON {requirements:[{key,value,kind,source,quote}]}. "
                    "kind must be hard, soft, assumption, or unknown. source is user_text when explicitly stated. "
                    "quote must be an exact substring for an explicit fact; use null if there is no quote. "
                    "Do not invent dates, people, cities, prices, offer ids, or other facts. "
                    f"Reference date is {current_date}. If a year is omitted, preserve the yearless dates; "
                    "the application will mark its calendar-year resolution as an assumption."
                )},
                {"role": "user", "content": text},
            ],
            ParsedRequest,
            fast=True,
        )
        # Treat the model as an extractor, not a source of new facts: explicit
        # hard/soft claims need a verbatim quote from the user's text.
        verified = [
            item for item in parsed.requirements
            if item.kind not in {"hard", "soft"}
            or (item.source == "user_text" and item.quote is not None and item.quote in text)
        ]
        return ParsedRequest(requirements=verified)

    @staticmethod
    def _required_questions(requirements: list[Requirement]) -> list[OpenQuestion]:
        present = {item.key: item for item in requirements if item.kind != "unknown" and item.value not in (None, "")}
        required = (
            ("origin", "Откуда вы отправляетесь?", "город вылета влияет на доступный транспорт"),
            ("destination", "Куда хотите поехать?", "без направления нельзя подбирать предложения"),
            ("dates", "На какие даты планировать поездку?", "даты определяют доступность и цену"),
            ("adults", "Сколько будет взрослых?", "состав гостей влияет на вместимость и расчёт"),
        )
        questions = []
        for key, question, why in required:
            if key not in present:
                questions.append(OpenQuestion(key=key, question=question, why=why))
            if len(questions) == 2:
                break
        return questions

    def _mark_year_assumption(self, items: list[Requirement]) -> list[Requirement]:
        date_req = next((r for r in items if r.key in {"dates", "date_range"}), None)
        if not date_req:
            return items
        quote = date_req.quote or (date_req.value if isinstance(date_req.value, str) else "")
        month_names = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
                       "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12}
        range_match = re.search(
            r"(?P<start>\d{1,2})\s*[-–—]\s*(?P<end>\d{1,2})\s+(?P<month>[а-яё]+)(?:\s+(?P<year>20\d{2}))?",
            str(quote).lower(),
        )
        if range_match:
            month = month_names.get(range_match.group("month"))
            if month:
                explicit_year = range_match.group("year")
                year = int(explicit_year) if explicit_year else self.today().year
                end_day = int(range_match.group("end"))
                if not explicit_year and (month, end_day) < (self.today().month, self.today().day):
                    year += 1
                # Normalize the explicitly stated days; the separately stored
                # assumption makes the inferred calendar year visible.
                date_req = date_req.model_copy(update={"value": {
                    "start": date(year, month, int(range_match.group("start"))).isoformat(),
                    "end": date(year, month, end_day).isoformat(),
                }})
                items = [date_req if r.key == date_req.key else r for r in items]
                if explicit_year:
                    return [r for r in items if r.key != "travel_year"]
                out = [r for r in items if r.key != "travel_year"]
                out.append(Requirement(key="travel_year", value=year, kind="assumption", source="calendar_resolution", quote=None))
                return out
        if re.search(r"\b20\d{2}\b", str(quote)):
            return items
        year = self.today().year
        out = [r for r in items if r.key != "travel_year"]
        out.append(Requirement(
            key="travel_year", value=year, kind="assumption", source="calendar_resolution",
            quote=None,
        ))
        return out

    async def prepare_turn(self, state: TripState, text: str) -> tuple[TripState, str, list[dict[str, Any]]]:
        parsed = await self.parse_request(text)
        merged = {item.key: item for item in state.requirements}
        for item in parsed.requirements:
            merged[item.key] = item
        requirements = self._mark_year_assumption(list(merged.values()))
        questions = self._required_questions(requirements)
        candidate = state.model_copy(deep=True, update={"requirements": requirements, "open_questions": questions})
        trace: list[dict[str, Any]] = [{
            "step_type": "llm", "name": "parse_request",
            "input": {"text": text, "model": self.llm.model_fast},
            "output": {"requirements": [r.model_dump(mode="json") for r in parsed.requirements]},
            "status": "ok",
        }]
        if questions:
            candidate.stage = "clarify"
            msg = "Чтобы подобрать поездку, уточните: " + "; ".join(q.question for q in questions)
            return candidate, msg, trace

        req_map = {r.key: r.value for r in requirements if r.kind != "unknown"}
        trace.append({
            "step_type": "tool_call", "name": "build_bundles",
            "input": req_map, "output": None, "status": "running",
        })
        result = self.bundle_builder(req_map, candidate)
        if hasattr(result, "__await__"):
            result = await result
        bundles = [x if isinstance(x, Bundle) else Bundle.model_validate(x) for x in result]
        candidate.bundles = bundles
        candidate.stage = "select"
        trace[-1] = {
            "step_type": "tool_result", "name": "build_bundles", "input": req_map,
            "output": [b.model_dump(mode="json") for b in bundles], "status": "ok",
        }
        if not bundles:
            msg = "Под указанными условиями комплектов не найдено. Я не буду показывать вымышленные предложения."
        else:
            msg = f"Нашёл {len(bundles)} комплект(а). Общая стоимость указана инструментом подбора."
        return candidate, msg, trace
