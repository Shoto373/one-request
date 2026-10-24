"""Sprint 5, track B: the same agent call works on different OpenAI-compatible providers; no-key mode."""

from __future__ import annotations

import json
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agent.llm import NO_KEY_MESSAGE, LLMClient, LLMConfigurationError, extract_json_text, normalize_base_url
from app.agent.orchestrator import Orchestrator
from app.state.models import TripState

PARSED = {"requirements": [
    {"key": "origin", "value": "Москва", "kind": "hard", "source": "user_text", "quote": "Из Москвы"},
    {"key": "adults", "value": 2, "kind": "hard", "source": "user_text", "quote": "двое взрослых"},
], "remove_keys": []}
TEXT = "Из Москвы в Сочи, двое взрослых"


def openai_like(seen: list[dict]):
    """Strict hosted provider: needs the bearer key, supports json_object, plain string content."""
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append({"auth": request.headers.get("authorization"), "body": body, "url": str(request.url)})
        if request.headers.get("authorization") != "Bearer sk-test":
            return httpx.Response(401, json={"error": {"message": "invalid api key"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(PARSED, ensure_ascii=False)}}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
    return handler


def ollama_like(seen: list[dict]):
    """Local server: no key, rejects response_format, reasoning preamble, fenced JSON in content parts."""
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append({"auth": request.headers.get("authorization"), "body": body, "url": str(request.url)})
        if "response_format" in body:
            return httpx.Response(400, json={"error": {"message": "response_format json_object is not supported"}})
        text = "<think>пользователь едет из Москвы</think>\n```json\n" + json.dumps(PARSED, ensure_ascii=False) + "\n```"
        return httpx.Response(200, json={"choices": [{"message": {"content": [{"type": "text", "text": text}]}}]})
    return handler


def reasoning_like(seen: list[dict]):
    """Reasoning model: rejects any temperature, answers with prose around the JSON object."""
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append({"body": body})
        if "temperature" in body:
            return httpx.Response(400, json={"error": {"message": "Unsupported value: 'temperature' does not support 0"}})
        return httpx.Response(200, json={"choices": [{"message": {
            "content": "Вот результат: " + json.dumps(PARSED, ensure_ascii=False) + " — готово."}}]})
    return handler


async def _parse_with(handler, **client_kw) -> tuple[list, LLMClient]:
    seen: list[dict] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler(seen))) as http:
        llm = LLMClient(http_client=http, model="m", **client_kw)
        parsed = await Orchestrator(llm).parse_request(TEXT, TripState(session_id=uuid4()))
    return parsed.requirements, llm, seen


@pytest.mark.asyncio
@pytest.mark.parametrize("handler, kw", [
    (openai_like, {"base_url": "https://api.openai.com/v1", "api_key": "sk-test"}),
    (ollama_like, {"base_url": "http://host.docker.internal:11434/v1", "api_key": ""}),
    (reasoning_like, {"base_url": "https://openrouter.ai/api/v1/chat/completions", "api_key": "sk-test"}),
], ids=["openai_like", "ollama_like", "reasoning_like"])
async def test_same_agent_turn_on_different_providers(handler, kw) -> None:
    requirements, llm, seen = await _parse_with(handler, **kw)
    assert {r.key: r.value for r in requirements} == {"origin": "Москва", "adults": 2}
    assert all(s.get("url", "").endswith("/chat/completions") and "/chat/completions/chat" not in s.get("url", "")
               for s in seen if "url" in s)


@pytest.mark.asyncio
async def test_capabilities_are_learned_once() -> None:
    _, llm, seen = await _parse_with(ollama_like, base_url="http://localhost:11434/v1", api_key="")
    assert llm.json_mode is False
    assert seen[0]["auth"] is None  # no bearer header for a keyless local server
    assert "response_format" in seen[0]["body"] and "response_format" not in seen[-1]["body"]
    _, llm, seen = await _parse_with(reasoning_like, base_url="https://x.example/v1", api_key="k")
    assert llm.temperature_supported is False and "temperature" not in seen[-1]["body"]


@pytest.mark.asyncio
async def test_json_mode_can_be_disabled_up_front(monkeypatch) -> None:
    monkeypatch.setenv("LLM_JSON_MODE", "off")
    _, llm, seen = await _parse_with(ollama_like, base_url="http://localhost:11434/v1", api_key="")
    assert len(seen) == 1 and "response_format" not in seen[0]["body"]


def test_helpers() -> None:
    assert normalize_base_url("https://openrouter.ai/api/v1/chat/completions/") == "https://openrouter.ai/api/v1"
    assert json.loads(extract_json_text('<think>x</think> Ответ: {"a": 1} спасибо')) == {"a": 1}


@pytest.mark.asyncio
async def test_no_key_message_is_clear() -> None:
    llm = LLMClient(base_url="https://api.openai.com/v1", api_key="", model="m")
    assert llm.configured is False and llm.describe()["key_required"] is True
    with pytest.raises(LLMConfigurationError) as exc:
        await llm.chat([{"role": "user", "content": "x"}])
    assert str(exc.value) == NO_KEY_MESSAGE and "LLM_API_KEY" in NO_KEY_MESSAGE and "DEMO_MODE" not in NO_KEY_MESSAGE
    assert LLMClient(base_url="http://localhost:11434/v1", api_key="", model="m").configured


def test_status_endpoint_without_key(monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from app.main import app

    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        status = client.get("/api/status").json()
    # без ключа агент работает на правилах, а не отвечает ошибкой
    assert status["llm"]["configured"] is False and status["agent_mode"] == "rules"
    assert "офлайн-режиме" in status["message"]


def test_status_never_returns_the_key(monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-secret-value-123")
    monkeypatch.setenv("LLM_MODEL", "some-model")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from app.main import app

    with TestClient(app) as client:
        body = client.get("/api/status").text
    assert "sk-secret-value-123" not in body and '"configured":true' in body
