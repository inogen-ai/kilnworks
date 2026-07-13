import pytest

from kilnworks.adapters.connectors.fake import FakeConnector
from kilnworks.core.models import CONNECTOR_STATUS_DOWN, ConnectorResult


def test_fake_connector_returns_results_and_status():
    results = [
        ConnectorResult(
            title="Doc 1", text="text 1", link="https://example.com/1", connector="fake"
        ),
        ConnectorResult(title="Doc 2", text="text 2", connector="fake"),
    ]
    connector = FakeConnector(name="fake", results=results, status=CONNECTOR_STATUS_DOWN)

    found = connector.search("query", 1)

    assert found == results[:1]
    assert connector.status() == CONNECTOR_STATUS_DOWN
    assert connector.calls == [("query", 1)]


def test_fake_connector_raises_when_configured():
    connector = FakeConnector(name="fake", raises=RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="boom"):
        connector.search("query", 5)
