"""Small async client for OpenAI-compatible chat completions APIs.

Provider differences handled here, so any ``LLM_BASE_URL`` works the same way:
- ``response_format: json_object`` unsupported (Ollama, some proxies) -> retried once without it,
  then remembered for the client; ``LLM_JSON_MODE=off`` disables it up front;
- ``temperature`` rejected (reasoning models) -> retried once without it, then remembered;
- assistant ``content`` as a list of parts, ``<think>…</think>`` preambles, code fences or prose
  around the JSON object;
- a base URL pasted with ``/chat/completions``;
- local servers (localhost, host.docker.internal, ollama) need no API key.
"""
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
from urllib.parse import urlparse

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


class _UnsupportedParameter(Exception):
    """HTTP 400 from the provider that names a request parameter it does not accept."""

    def __init__(self, parameter: str):
        self.parameter = parameter


LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal", "ollama"}
NO_KEY_MESSAGE = ("Агенту не задан ключ LLM: впишите LLM_API_KEY (и при необходимости LLM_BASE_URL, LLM_MODEL) "
                  "в .env и перезапустите app. Интерфейс, каталог и метрики работают без ключа.")
_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def normalize_base_url(url: str) -> str:
    url = url.strip().rstrip("/")
    return re.sub(r"/chat/completions$", "", url)


def is_local(url: str) -> bool:
    return (urlparse(url).hostname or "") in LOCAL_HOSTS


def extract_json_text(content: str) -> str:
    """JSON object text from a reply that may carry think-tags, code fences or prose around it."""
    content = _THINK.sub("", content).strip()
    content = re.sub(r"\A```(?:json)?\s*|\s*```\Z", "", content, flags=re.I).strip()
    if content.startswith("{") or content.startswith("["):
        return content
    start, end = content.find("{"), content.rfind("}")
    return content[start:end + 1] if 0 <= start < end else content


class LLMClient:
    def __init__(
        self, *, base_url: str | None = None, api_key: str | None = None,
        model: str | None = None, model_fast: str | None = None,
        timeout_seconds: float | None = None, http_client: httpx.AsyncClient | None = None,
        retries: int = 2, format_retries: int = 1,
        turn_attempt_budget: int = 12,
    ):
        self.base_url = normalize_base_url(base_url or os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"))
        self.api_key = api_key if api_key is not None else os.getenv("LLM_API_KEY", "")
        self.model = model or os.getenv("LLM_MODEL", "")
        self.model_fast = model_fast or os.getenv("LLM_MODEL_FAST", self.model)
        self.timeout_seconds = timeout_seconds or float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
        self.client = http_client
        self.retries = max(0, min(retries, 3))
        self.format_retries = max(0, min(format_retries, 1))
        self.turn_attempt_budget = max(1, int(turn_attempt_budget))
        # Learned provider capabilities; LLM_JSON_MODE=off skips response_format from the start.
        self.json_mode = os.getenv("LLM_JSON_MODE", "auto").lower() != "off"
        self.temperature_supported = True

    @property
    def configured(self) -> bool:
        return bool(self.model) and (bool(self.api_key) or is_local(self.base_url))

    def describe(self) -> dict[str, Any]:
        """Safe status for logs and /api/status: never includes the key."""
        return {"configured": self.configured, "provider_host": urlparse(self.base_url).hostname,
                "model": self.model or None, "model_fast": self.model_fast or None,
                "json_mode": self.json_mode, "key_required": not is_local(self.base_url)}

    @contextmanager
    def request_budget(self, *, max_attempts: int | None = None):
        """Share one bounded HTTP-attempt budget across nested calls in one turn."""
        token = _TURN_BUDGET.set(_AttemptBudget(max_attempts or self.turn_attempt_budget))
        try:
            yield
        finally:
            _TURN_BUDGET.reset(token)

    def _require_config(self, model: str) -> None:
        if not self.api_key and not is_local(self.base_url):
            raise LLMConfigurationError(NO_KEY_MESSAGE)
        if not model:
            raise LLMConfigurationError("Не задана модель LLM: впишите LLM_MODEL в .env и перезапустите app.")

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
                    headers = {"Content-Type": "application/json"}
                    if self.api_key:
                        headers["Authorization"] = f"Bearer {self.api_key}"
                    response = await client.post(
                        f"{self.base_url}/chat/completions", headers=headers,
                        json={"model": model, **body}, timeout=self.timeout_seconds,
                    )
                    if response.status_code in (400, 422):
                        unsupported = self._unsupported_parameter(response, body)
                        if unsupported:
                            raise _UnsupportedParameter(unsupported)
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
                    detail = exc.response.text[:200].replace("\n", " ")
                    log.warning("llm provider error model=%s status=%d body=%s", model, exc.response.status_code, detail)
                    raise LLMRequestError(f"LLM provider returned HTTP {exc.response.status_code}") from exc
                except (ValueError, KeyError) as exc:
                    raise LLMRequestError("LLM provider returned malformed response") from exc
        finally:
            if own_client:
                await client.aclose()
        raise LLMRequestError("LLM request failed")

    @staticmethod
    def _unsupported_parameter(response: httpx.Response, body: dict[str, Any]) -> str | None:
        text = response.text.lower()
        if "response_format" in body and re.search(r"response_format|json_object|json mode|response format", text):
            return "response_format"
        if "temperature" in body and "temperature" in text:
            return "temperature"
        return None

    async def _adaptive_request(self, body: dict[str, Any], model: str, budget: _AttemptBudget | None = None) -> dict[str, Any]:
        """Send; on a named unsupported parameter drop it, remember that and retry (each at most once)."""
        body = dict(body)
        if not self.json_mode:
            body.pop("response_format", None)
        if not self.temperature_supported:
            body.pop("temperature", None)
        for _ in range(3):
            try:
                return await self._request(body, model, budget)
            except _UnsupportedParameter as exc:
                log.warning("llm provider rejected %s; retrying without it (host=%s)", exc.parameter,
                            urlparse(self.base_url).hostname)
                if exc.parameter == "response_format":
                    self.json_mode = False
                else:
                    self.temperature_supported = False
                body.pop(exc.parameter, None)
        raise LLMRequestError("LLM provider rejected the request parameters")

    @staticmethod
    def _content(data: dict[str, Any]) -> str:
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMRequestError("LLM response has no assistant content") from exc
        if isinstance(content, list):  # content parts: [{"type": "text", "text": "..."}]
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        if not isinstance(content, str):
            raise LLMRequestError("LLM returned non-text assistant content")
        return content

    async def chat(self, messages: list[dict[str, Any]], *, fast: bool = False, temperature: float = 0.2) -> str:
        model = self.model_fast if fast else self.model
        result = await self._adaptive_request({"messages": messages, "temperature": temperature}, model)
        return _THINK.sub("", self._content(result)).strip()

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
            result = await self._adaptive_request(
                {"messages": prompt, "temperature": 0, "response_format": {"type": "json_object"}}, model, budget,
            )
            content = extract_json_text(self._content(result))
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
        result = await self._adaptive_request({"messages": messages, "tools": tools, "tool_choice": "auto"}, model)
        try:
            return result["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMRequestError("LLM response has no tool-capable assistant message") from exc
