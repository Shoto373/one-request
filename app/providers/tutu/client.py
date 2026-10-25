"""Small Python MCP client for Tutu's documented Streamable HTTP endpoint."""
from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import AsyncExitStack, suppress
from datetime import datetime, timezone
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from app.providers.base import ProviderResponseError, ProviderUnavailable, SearchResponse

TUTU_READ_TOOLS = frozenset({
    "search_avia", "search_rail", "search_hotels", "get_offer_details",
})
_TRANSIENT = re.compile(r"(?:\b429\b|\b502\b|\b503\b|\b504\b|timeout|temporar|rate.?limit)", re.I)


class TutuMCPError(ProviderUnavailable):
    def __init__(self, message: str, *, tool: str | None = None, retryable: bool = False):
        super().__init__(message)
        self.tool = tool
        self.retryable = retryable


class TutuMCPClient:
    """One initialized MCP session per planning operation; bounded requests and concurrency.

    ``TUTU_MAX_CALLS`` and ``TUTU_MAX_PARALLEL`` are application safety controls,
    not limits claimed by Tutu.
    """

    def __init__(self, *, endpoint: str | None = None, timeout: float | None = None,
                 max_calls: int | None = None, max_parallel: int | None = None):
        self.endpoint = endpoint or os.getenv("TUTU_MCP_ENDPOINT", "https://mcp.tutu.ru/mcp")
        self.timeout = timeout or float(os.getenv("TUTU_TIMEOUT_SECONDS", "25"))
        self.max_calls = max_calls or int(os.getenv("TUTU_MAX_CALLS", "18"))
        self._semaphore = asyncio.Semaphore(max_parallel or int(os.getenv("TUTU_MAX_PARALLEL", "2")))
        self._calls = 0
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._schemas: dict[str, set[str]] = {}
        self.server_version: str | None = None

    async def __aenter__(self) -> "TutuMCPClient":
        if not self.endpoint.startswith("https://") and os.getenv("TUTU_ALLOW_HTTP", "false").lower() != "true":
            raise TutuMCPError("Tutu MCP endpoint must use HTTPS")
        self._stack = AsyncExitStack()
        try:
            streams = await self._stack.enter_async_context(streamablehttp_client(
                self.endpoint, timeout=self.timeout, sse_read_timeout=max(self.timeout, 30),
            ))
            read, write, *_ = streams
            self._session = await self._stack.enter_async_context(ClientSession(read, write))
            initialized = await self._session.initialize()
            self.server_version = getattr(initialized.serverInfo, "version", None)
            listed = await self._session.list_tools()
            self._schemas = {
                tool.name: set((tool.inputSchema or {}).get("properties", {}))
                for tool in listed.tools
            }
            missing = {"search_avia", "search_rail", "search_hotels", "get_offer_details"} - self._schemas.keys()
            if missing:
                raise TutuMCPError("Tutu MCP does not expose required read-only tools: " + ", ".join(sorted(missing)))
            return self
        except Exception as exc:
            with suppress(BaseException):
                await self._stack.aclose()
            self._stack = None
            if isinstance(exc, TutuMCPError):
                raise
            raise self._wrap(exc, tool=None) from exc

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
            self._session = None

    @staticmethod
    def _wrap(exc: BaseException, *, tool: str | None) -> TutuMCPError:
        text = str(exc)
        transient = isinstance(exc, (TimeoutError, httpx.TimeoutException, httpx.TransportError)) or bool(_TRANSIENT.search(text))
        safe = "Tutu MCP timed out" if isinstance(exc, (TimeoutError, httpx.TimeoutException)) else "Tutu MCP request failed"
        return TutuMCPError(safe, tool=tool, retryable=transient)

    async def call(self, tool: str, args: dict[str, Any]) -> SearchResponse:
        if tool not in TUTU_READ_TOOLS:
            raise TutuMCPError(f"Tutu MCP operation is not allowlisted: {tool}", tool=tool)
        if self._session is None:
            raise TutuMCPError("Tutu MCP session is not initialized", tool=tool)
        if tool not in self._schemas:
            raise TutuMCPError(f"Tutu MCP tool is unavailable: {tool}", tool=tool)
        unknown = set(args) - self._schemas[tool]
        if unknown:
            raise TutuMCPError(f"Local arguments do not match the discovered {tool} schema", tool=tool)

        for attempt in range(2):
            if self._calls >= self.max_calls:
                raise TutuMCPError("Tutu MCP request budget reached", tool=tool)
            self._calls += 1
            try:
                async with self._semaphore:
                    result = await self._session.call_tool(tool, args)
                if result.isError:
                    message = " ".join(getattr(part, "text", "") for part in result.content)
                    if attempt == 0 and _TRANSIENT.search(message):
                        await asyncio.sleep(0.25)
                        continue
                    raise TutuMCPError(f"Tutu MCP {tool} returned an error", tool=tool,
                                       retryable=bool(_TRANSIENT.search(message)))
                data = result.structuredContent
                if data is None:
                    text = "\n".join(getattr(part, "text", "") for part in result.content)
                    try:
                        data = json.loads(text)
                    except (ValueError, TypeError) as exc:
                        raise ProviderResponseError(f"Tutu MCP {tool} returned non-JSON data") from exc
                if not isinstance(data, dict):
                    raise ProviderResponseError(f"Tutu MCP {tool} returned an unexpected shape")
                return SearchResponse(
                    provider="tutu", tool=tool,
                    fetched_at=datetime.now(timezone.utc).isoformat(), args=dict(args), data=data,
                )
            except TutuMCPError:
                raise
            except Exception as exc:
                wrapped = self._wrap(exc, tool=tool)
                if attempt == 0 and wrapped.retryable:
                    await asyncio.sleep(0.25)
                    continue
                raise wrapped from exc
        raise TutuMCPError("Tutu MCP request failed after bounded retry", tool=tool)

    @property
    def calls_used(self) -> int:
        return self._calls
