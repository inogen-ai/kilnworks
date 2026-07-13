"""Per-spawn stdio MCP client connector.

Spawns a connector's stdio MCP server fresh for each `search()`/`status()` call,
invokes its search tool, and adapts the plain-text result into `ConnectorResult`s.
This is the only module that imports `mcp`; it is imported only when a connector is
actually built, so the base install (and all of `core/`) stays free of the dependency.

`search()` and `status()` are sync (the `Connector` protocol) and bridge to async via
`asyncio.run`, which opens a fresh stdio session and closes it. Callers run these from
a threadpool thread (no running loop), so `asyncio.run` is safe.
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import timedelta

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from kilnworks.core.models import (
    CONNECTOR_STATUS_DOWN,
    CONNECTOR_STATUS_READY,
    ConnectorResult,
)

_URL_RE = re.compile(r"https?://\S+")
_BLANK_LINE_RE = re.compile(r"\n\s*\n")


class MCPStdioConnector:
    """Connector backed by a connector-specific stdio MCP server, spawned per call."""

    def __init__(
        self,
        name: str,
        command: list[str],
        env: dict[str, str],
        search_limit: int = 5,
        search_tool: str = "search",
        query_arg: str = "query",
        extra_args: dict | None = None,
        timeout: float = 30.0,
    ):
        self.name = name
        self._command = command
        self._env = env
        self._search_limit = search_limit
        self._search_tool = search_tool
        self._query_arg = query_arg
        self._extra_args = extra_args or {}
        self._timeout = timeout

    def search(self, query: str, limit: int) -> list[ConnectorResult]:
        effective = min(limit, self._search_limit)
        return asyncio.run(self._search_async(query, effective))

    def status(self) -> str:
        try:
            return asyncio.run(self._status_async())
        except Exception:
            # Can't spawn/connect/initialize -> the connector is down.
            return CONNECTOR_STATUS_DOWN

    # -- async internals ---------------------------------------------------

    def _params(self) -> StdioServerParameters:
        merged_env = {**os.environ, **self._env}
        return StdioServerParameters(
            command=self._command[0],
            args=self._command[1:],
            env=merged_env,
        )

    async def _search_async(self, query: str, limit: int) -> list[ConnectorResult]:
        async with stdio_client(self._params()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    self._search_tool,
                    {self._query_arg: query, "limit": limit, **self._extra_args},
                    read_timeout_seconds=timedelta(seconds=self._timeout),
                )
        return self._adapt(self._text_of(result), limit)

    async def _status_async(self) -> str:
        async with stdio_client(self._params()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
        names = {tool.name for tool in tools.tools}
        return CONNECTOR_STATUS_READY if self._search_tool in names else CONNECTOR_STATUS_DOWN

    # -- text adaptation ---------------------------------------------------

    @staticmethod
    def _text_of(result) -> str:
        return "".join(
            block.text for block in result.content if getattr(block, "text", None) is not None
        )

    def _adapt(self, text: str, limit: int) -> list[ConnectorResult]:
        stripped = text.strip()
        if not stripped or stripped.lower() == "no results":
            return []

        blocks = [b.strip() for b in _BLANK_LINE_RE.split(stripped) if b.strip()]
        results = []
        for block in blocks[:limit]:
            title = block.splitlines()[0].strip()
            url_match = _URL_RE.search(block)
            results.append(
                ConnectorResult(
                    title=title,
                    text=block,
                    link=url_match.group(0) if url_match else None,
                    connector=self.name,
                )
            )
        return results
