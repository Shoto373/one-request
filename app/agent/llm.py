"""Small async client for OpenAI-compatible chat completions APIs."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class _AttemptBudget:
    def __init__(self, remaining: int):
        self.remaining = remaining


_TURN_BUDGET: ContextVar[_AttemptBudget | None] = ContextVar("llm_turn_attempt_budget", default=None)


class LLMConfigurationError(RuntimeError):
    pass


class LLMRequestError(RuntimeError):
    pass


class InvalidLLMJSONError(ValueError):
    pass


class LLMClient:
    def __init__(
        self, *, base_url: str | None = None, api_key: str | None = None,
        model: str | None = None, model_fast: str | None = None,
        timeout_seconds: float | None = None, http_client: httpx.AsyncClient | None = None,
        retries: int = 2, format_retries: int = 1,
        turn_attempt_budget: int = 12,
    ):
        self.base_url = (base_url or os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.api_key = api_key if api_key is not None else os.getenv("LLM_API_KEY", "")
        self.model = model or os.getenv("LLM_MODEL", "")
        self.model_fast = model_fast or os.getenv("LLM_MODEL_FAST", self.model)
        self.timeout_seconds = timeout_seconds or float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
        self.client = http_client
        self.retries = max(0, min(retries, 3))
        self.format_retries = max(0, min(format_retries, 1))
        self.turn_attempt_budget = max(1, int(turn_attempt_budget))

    @contextmanager
    def request_budget(self, *, max_attempts: int | None = None):
        """Share one bounded HTTP-attempt budget across nested calls in one turn."""
        token = _TURN_BUDGET.set(_AttemptBudget(max_attempts or self.turn_attempt_budget))
        try:
            yield
        finally:
            _TURN_BUDGET.reset(token)

    def _require_config(self, model: str) -> None:
        if not self.api_key:
            raise LLMConfigurationError("LLM_API_KEY is not configured; set it or enable explicit DEMO_MODE")
        if not model:
            raise LLMConfigurationError("LLM_MODEL is not configured")

    async def _request(self, body: dict[str, Any], model: str, budget: _AttemptBudget | None = None) -> dict[str, Any]:
        self._require_config(model)
        own_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=self.timeout_seconds)
        started = time.monotonic()
        budget = budget or _TURN_BUDGET.get() or _AttemptBudget(self.retries + 1)
        if budget.remaining <= 0:
            raise LLMRequestError("LLM turn request budget exhausted")
        try:
            for attempt in range(min(self.retries + 1, budget.remaining)):
                budget.remaining -= 1
                try:
                    response = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                        json={"model": model, **body}, timeout=self.timeout_seconds,
                    )
                    if response.status_code in (408, 429) or response.status_code >= 500:
                        if attempt < self.retries:
                            await asyncio.sleep(min(0.25 * (2**attempt), 1.0))
                            continue
                    response.raise_for_status()
                    data = response.json()
                    usage = data.get("usage")
                    log.info("llm request model=%s duration_ms=%d usage=%s", model, int((time.monotonic()-started)*1000), usage if usage is not None else "unreported")
                    return data
                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    if attempt < self.retries:
                        await asyncio.sleep(min(0.25 * (2**attempt), 1.0))
                        continue
                    raise LLMRequestError(f"LLM request failed after {attempt + 1} attempt(s): {type(exc).__name__}") from exc
                except httpx.HTTPStatusError as exc:
                    raise LLMRequestError(f"LLM provider returned HTTP {exc.response.status_code}") from exc
                except (ValueError, KeyError) as exc:
                    raise LLMRequestError("LLM provider returned malformed response") from exc
        finally:
            if own_client:
                await client.aclose()
        raise LLMRequestError("LLM request failed")

    @staticmethod
    def _content(data: dict[str, Any]) -> str:
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMRequestError("LLM response has no assistant content") from exc
        if not isinstance(content, str):
            raise LLMRequestError("LLM returned non-text assistant content")
        return content

    async def chat(self, messages: list[dict[str, Any]], *, fast: bool = False, temperature: float = 0.2) -> str:
        model = self.model_fast if fast else self.model
        result = await self._request({"messages": messages, "temperature": temperature}, model)
        return self._content(result)

    async def chat_json(
        self, messages: list[dict[str, Any]], schema: type[T], *, fast: bool = False,
    ) -> T:
        model = self.model_fast if fast else self.model
        budget = _TURN_BUDGET.get() or _AttemptBudget(self.retries + 1 + self.format_retries)
        if budget.remaining <= 0:
            raise LLMRequestError("LLM turn request budget exhausted")
        prompt = messages
        repair_count = 0
        while budget.remaining > 0:
            result = await self._request(
                {"messages": prompt, "temperature": 0, "response_format": {"type": "json_object"}}, model, budget,
            )
            content = self._content(result).strip()
            content = re.sub(r"\A```(?:json)?\s*|\s*```\Z", "", content, flags=re.I)
            try:
                parsed = json.loads(content)
                return schema.model_validate(parsed)
            except json.JSONDecodeError as exc:
                error = InvalidLLMJSONError(f"LLM returned invalid JSON at character {exc.pos}")
            except ValidationError as exc:
                error = InvalidLLMJSONError(f"LLM JSON does not match {schema.__name__}: {exc.errors()[0]['msg']}")
            if repair_count >= self.format_retries or budget.remaining <= 0:
                raise error
            repair_count += 1
            prompt = [
                *messages,
                {"role": "assistant", "content": content},
                {"role": "user", "content": "Correct your previous response. It failed validation: "
                 + str(error) + ". Return only JSON matching this schema: "
                 + json.dumps(schema.model_json_schema(), ensure_ascii=False)},
            ]
        raise InvalidLLMJSONError("LLM JSON attempt budget exhausted")

    async def chat_tools(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], *, fast: bool = False,
    ) -> dict[str, Any]:
        model = self.model_fast if fast else self.model
        result = await self._request({"messages": messages, "tools": tools, "tool_choice": "auto"}, model)
        try:
            return result["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMRequestError("LLM response has no tool-capable assistant message") from exc
