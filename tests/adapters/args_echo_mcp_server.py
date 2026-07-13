"""Hermetic stub MCP stdio server used by test_mcp_stdio to verify exactly which
tool-call argument keys the connector sends. Its `search` tool echoes back the
sorted list of argument names it received (`limit` is optional, so its absence
vs. presence is observable).
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("args-echo")


@mcp.tool()
def search(query: str, limit: int | None = None) -> str:
    received = ["query"]
    if limit is not None:
        received.append("limit")
    return f"keys={','.join(sorted(received))}"


if __name__ == "__main__":
    mcp.run()
