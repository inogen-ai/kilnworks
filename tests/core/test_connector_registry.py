import json

from kilnworks.adapters.connectors.fake import FakeConnector
from kilnworks.core.connectors import ConnectorRegistry
from kilnworks.core.models import CONNECTOR_STATUS_NEEDS_LOGIN, CONNECTOR_STATUS_READY


def _fake_factory(**calls_log):
    """Returns a factory that records every call and builds FakeConnectors."""
    calls = calls_log.setdefault("calls", [])

    def factory(name, command, env, search_limit):
        calls.append(
            {"name": name, "command": command, "env": env, "search_limit": search_limit}
        )
        return FakeConnector(name=name)

    factory.calls = calls
    return factory


def test_allowed_for_filters_by_group():
    sales_connector = FakeConnector(name="salesforce")
    registry = ConnectorRegistry([(sales_connector, ["sales"])])

    assert registry.allowed_for(["sales"]) == [sales_connector]
    assert registry.allowed_for(["public"]) == []


def test_public_group_is_honored():
    # Principals always include "public" (added by the caller before reaching the
    # registry), so a connector allowing "public" is usable regardless of a user's
    # other group memberships.
    public_connector = FakeConnector(name="wiki")
    registry = ConnectorRegistry([(public_connector, ["public"])])

    assert registry.allowed_for(["public", "engineering"]) == [public_connector]
    assert registry.allowed_for(["public"]) == [public_connector]
    assert registry.allowed_for([]) == []
    assert registry.allowed_for(["engineering"]) == []


def test_get_unknown_name_returns_none():
    registry = ConnectorRegistry([(FakeConnector(name="salesforce"), ["sales"])])

    assert registry.get("salesforce") is not None
    assert registry.get("nonexistent") is None


def test_from_config_builds_connectors_via_factory(tmp_path):
    config_path = tmp_path / "connectors.json"
    config_path.write_text(
        json.dumps(
            {
                "connectors": [
                    {
                        "name": "salesforce",
                        "command": ["sfdc-mcp-server"],
                        "env": {"SFDC_MCP_AUTH": "device_code"},
                        "allowed_groups": ["sales", "admin"],
                        "search_limit": 3,
                    },
                    {
                        "name": "wiki",
                        "command": ["wiki-mcp-server", "--flag"],
                        "env": {},
                        "allowed_groups": ["public"],
                    },
                ]
            }
        )
    )
    factory = _fake_factory()

    registry = ConnectorRegistry.from_config(str(config_path), factory)

    assert len(factory.calls) == 2
    assert factory.calls[0] == {
        "name": "salesforce",
        "command": ["sfdc-mcp-server"],
        "env": {"SFDC_MCP_AUTH": "device_code"},
        "search_limit": 3,
    }
    assert factory.calls[1] == {
        "name": "wiki",
        "command": ["wiki-mcp-server", "--flag"],
        "env": {},
        "search_limit": 5,
    }
    assert registry.get("salesforce") is not None
    assert registry.get("wiki") is not None


def test_env_vars_are_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("FOO", "resolved-value")
    config_path = tmp_path / "connectors.json"
    config_path.write_text(
        json.dumps(
            {
                "connectors": [
                    {
                        "name": "salesforce",
                        "command": ["sfdc-mcp-server"],
                        "env": {"SFDC_MCP_CLIENT_ID": "${FOO}"},
                        "allowed_groups": ["sales"],
                    }
                ]
            }
        )
    )
    factory = _fake_factory()

    ConnectorRegistry.from_config(str(config_path), factory)

    assert factory.calls[0]["env"] == {"SFDC_MCP_CLIENT_ID": "resolved-value"}


def test_visible_reports_status_and_needs_login():
    ready = FakeConnector(name="salesforce", status=CONNECTOR_STATUS_READY)
    needs_login = FakeConnector(name="hubspot", status=CONNECTOR_STATUS_NEEDS_LOGIN)
    registry = ConnectorRegistry(
        [(ready, ["sales"]), (needs_login, ["sales"])]
    )

    visible = registry.visible(["sales"])

    assert ("salesforce", CONNECTOR_STATUS_READY, False) in visible
    assert ("hubspot", CONNECTOR_STATUS_NEEDS_LOGIN, True) in visible


def test_visible_excludes_disallowed_connectors():
    connector = FakeConnector(name="salesforce")
    registry = ConnectorRegistry([(connector, ["sales"])])

    assert registry.visible(["public"]) == []
