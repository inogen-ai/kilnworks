"""Hermetic stub MCP stdio server used by test_mcp_stdio.

Spawned as a subprocess (via sys.executable) by the connector tests. It exposes a
single `search` tool returning canned multi-block text — no network, no credentials.
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("stub")


@mcp.tool()
def search(query: str, limit: int = 5) -> str:
    if query == "__empty__":
        return "No results"
    return (
        f"First Result for {query}\n"
        "Some detail about the first result.\n"
        "See https://example.com/first for more.\n"
        "\n"
        "Second Result\n"
        "More detail here with no link."
    )


if __name__ == "__main__":
    mcp.run()
