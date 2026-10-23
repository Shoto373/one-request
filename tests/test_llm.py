import httpx
import pytest
from pydantic import BaseModel

from app.agent.llm import InvalidLLMJSONError, LLMClient, LLMConfigurationError, LLMRequestError


class Result(BaseModel):
    value: int


@pytest.mark.asyncio
async def test_chat_json_validates_response():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
        "choices": [{"message": {"content": '{"value": 7}'}}],
        "usage": {"prompt_tokens": 4, "completion_tokens": 3},
    }))
    async with httpx.AsyncClient(transport=transport) as client:
        llm = LLMClient(api_key="test", model="test-model", http_client=client)
        assert (await llm.chat_json([], Result)).value == 7


@pytest.mark.asyncio
async def test_chat_and_chat_tools_return_provider_content():
    def responder(request):
        body = __import__("json").loads(request.content)
        if "tools" in body:
            return httpx.Response(200, json={"choices": [{"message": {
                "role": "assistant", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "search", "arguments": "{}"}}],
            }}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}]})

    transport = httpx.MockTransport(responder)
    async with httpx.AsyncClient(transport=transport) as client:
        llm = LLMClient(api_key="test", model="test-model", http_client=client)
        assert await llm.chat([]) == "hello"
        tool_reply = await llm.chat_tools([], [{"type": "function"}])
        assert tool_reply["tool_calls"][0]["function"]["name"] == "search"


@pytest.mark.asyncio
async def test_invalid_json_is_clear_and_finite():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]}))
    async with httpx.AsyncClient(transport=transport) as client:
        llm = LLMClient(api_key="test", model="test-model", http_client=client, retries=0)
        with pytest.raises(InvalidLLMJSONError, match="invalid JSON"):
            await llm.chat_json([], Result)


@pytest.mark.asyncio
async def test_missing_key_does_not_make_network_request():
    llm = LLMClient(api_key="", model="test-model", retries=0)
    with pytest.raises(LLMConfigurationError, match="LLM_API_KEY"):
        await llm.chat([], fast=False)


@pytest.mark.asyncio
async def test_timeout_is_bounded_and_reported():
    calls = 0

    def timeout(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("slow", request=request)

    transport = httpx.MockTransport(timeout)
    async with httpx.AsyncClient(transport=transport) as client:
        llm = LLMClient(api_key="test", model="test-model", http_client=client, retries=1)
        with pytest.raises(LLMRequestError, match="after 2 attempt"):
            await llm.chat([])
    assert calls == 2
