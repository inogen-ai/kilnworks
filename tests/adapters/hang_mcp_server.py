"""Hermetic stub MCP stdio server that STALLS before ever completing the MCP
initialize handshake, used by test_mcp_stdio to exercise the connector's
overall timeout.

`Server.run()` enters the lifespan context manager before it starts reading
any incoming messages (including `initialize`), so a lifespan that blocks
never lets the client's `session.initialize()` complete -- it hangs exactly
like a real server that spawns but then stalls during startup.
"""

import time
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP


@asynccontextmanager
async def _hanging_lifespan(_server):
    time.sleep(10)  # simulate a server that spawns but never finishes starting up
    yield {}


mcp = FastMCP("hang-stub", lifespan=_hanging_lifespan)


@mcp.tool()
def search(query: str, limit: int = 5) -> str:
    return "unreachable"  # never reached; the server never finishes initializing


if __name__ == "__main__":
    mcp.run()
