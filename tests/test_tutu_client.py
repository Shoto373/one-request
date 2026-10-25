from types import SimpleNamespace

import pytest

from app.providers.tutu.client import TutuMCPClient, TutuMCPError


class FakeSession:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    async def call_tool(self, tool, args):
        self.calls.append((tool, args))
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def result(data=None, *, error=False, text=""):
    return SimpleNamespace(isError=error, structuredContent=data,
                           content=[SimpleNamespace(text=text)] if text else [])


@pytest.mark.asyncio
async def test_only_discovered_read_only_operations_and_arguments_are_allowed():
    client = TutuMCPClient()
    client._session = FakeSession(result({"offers": []}))
    client._schemas = {"search_rail": {"origin"}}
    with pytest.raises(TutuMCPError, match="not allowlisted"):
        await client.call("create_checkout_link", {})
    with pytest.raises(TutuMCPError, match="do not match"):
        await client.call("search_rail", {"flight_numbers": ["x"]})
    assert client._session.calls == []


@pytest.mark.asyncio
async def test_transient_rate_limit_gets_one_bounded_retry():
    session = FakeSession(result(error=True, text="429 rate limit"), result({"offers": []}))
    client = TutuMCPClient(max_calls=2)
    client._session = session
    client._schemas = {"search_rail": {"origin"}}
    response = await client.call("search_rail", {"origin": "Москва"})
    assert response.data == {"offers": []}
    assert client.calls_used == 2
    assert len(session.calls) == 2


@pytest.mark.asyncio
async def test_request_budget_stops_nested_retry_loop():
    session = FakeSession(result(error=True, text="503 temporarily unavailable"),
                          result(error=True, text="503 temporarily unavailable"))
    client = TutuMCPClient(max_calls=1)
    client._session = session
    client._schemas = {"search_rail": {"origin"}}
    with pytest.raises(TutuMCPError, match="budget"):
        await client.call("search_rail", {"origin": "Москва"})
    assert client.calls_used == 1
    assert len(session.calls) == 1
