"""Hermetic stub MCP stdio server used by test_mcp_stdio to verify environment
variable isolation from the parent process. Its `search` tool treats the query
as an env var name and echoes back that variable's value as seen inside this
child process (or "MISSING" if unset) -- no network, no credentials.
"""

import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("env-echo")


@mcp.tool()
def search(query: str, limit: int = 5) -> str:
    value = os.environ.get(query, "MISSING")
    return f"{query}={value}"


if __name__ == "__main__":
    mcp.run()
