import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from kilnworks.adapters.connectors.mcp_stdio import MCPStdioConnector  # noqa: E402
from kilnworks.core.models import (  # noqa: E402
    CONNECTOR_STATUS_DOWN,
    CONNECTOR_STATUS_READY,
)

STUB = str(Path(__file__).parent / "stub_mcp_server.py")
HANG_STUB = str(Path(__file__).parent / "hang_mcp_server.py")


def _connector(**kwargs) -> MCPStdioConnector:
    kwargs.setdefault("name", "stub")
    kwargs.setdefault("command", [sys.executable, STUB])
    kwargs.setdefault("env", {})
    return MCPStdioConnector(**kwargs)


def test_search_spawns_server_and_adapts_results():
    connector = _connector()

    results = connector.search("hello", 5)

    assert len(results) == 2
    first, second = results

    assert first.title == "First Result for hello"
    assert "Some detail about the first result." in first.text
    assert first.link == "https://example.com/first"
    assert first.connector == "stub"

    assert second.title == "Second Result"
    assert "More detail here with no link." in second.text
    assert second.link is None
    assert second.connector == "stub"


def test_search_caps_at_search_limit():
    connector = _connector(search_limit=1)

    results = connector.search("hello", 5)

    assert len(results) == 1
    assert results[0].title == "First Result for hello"


def test_search_empty_returns_no_results():
    connector = _connector()

    assert connector.search("__empty__", 5) == []


def test_status_ready_when_search_tool_present():
    connector = _connector()

    assert connector.status() == CONNECTOR_STATUS_READY


def test_status_down_when_tool_missing():
    connector = _connector(search_tool="does_not_exist")

    assert connector.status() == CONNECTOR_STATUS_DOWN


def test_status_down_when_command_unspawnable():
    connector = MCPStdioConnector(
        name="stub", command=["definitely-not-a-real-binary-xyz"], env={}
    )

    assert connector.status() == CONNECTOR_STATUS_DOWN


def test_status_down_within_timeout_when_server_hangs_on_initialize():
    """A server that spawns but stalls during initialize() is not an exception --
    it must be bounded by an overall timeout, not hang forever."""
    connector = MCPStdioConnector(
        name="hang", command=[sys.executable, HANG_STUB], env={}, timeout=0.3
    )

    start = time.monotonic()
    status = connector.status()
    elapsed = time.monotonic() - start

    assert status == CONNECTOR_STATUS_DOWN
    # mcp's stdio_client teardown SIGTERMs the child and grants it up to a further
    # ~2s grace period to exit before SIGKILL, on top of our own timeout -- so the
    # bound here is our timeout plus that grace period, not just our timeout, but
    # it must still land well under the server's 10s stall.
    assert elapsed < 5.0, f"status() took {elapsed:.3f}s, expected well under the 10s hang"


def test_search_raises_within_timeout_when_server_hangs_on_initialize():
    """search() against a stalled server must not hang forever either -- it should
    time out (and let the caller, e.g. query.py's connector selection, handle it)
    well before the server's 10s stall would resolve."""
    connector = MCPStdioConnector(
        name="hang", command=[sys.executable, HANG_STUB], env={}, timeout=0.3
    )

    start = time.monotonic()
    with pytest.raises(TimeoutError):
        connector.search("hello", 5)
    elapsed = time.monotonic() - start

    assert elapsed < 5.0, f"search() took {elapsed:.3f}s, expected well under the 10s hang"
