"""Hermetic stub MCP stdio server used by test_mcp_stdio to verify the connector
can call a search tool that has no `limit` parameter at all. Some real connector
search tools don't accept a limit -- passing one unconditionally would error on
every call (see `limit_arg=None` on `MCPStdioConnector`).
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("no-limit")


@mcp.tool()
def search(query: str) -> str:
    return f"Result for {query}\nqueried without a limit arg."


if __name__ == "__main__":
    mcp.run()
