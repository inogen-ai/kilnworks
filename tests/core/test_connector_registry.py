import json
import time

from kilnworks.adapters.connectors.fake import FakeConnector
from kilnworks.core.connectors import ConnectorRegistry
from kilnworks.core.models import (
    CONNECTOR_STATUS_DOWN,
    CONNECTOR_STATUS_NEEDS_LOGIN,
    CONNECTOR_STATUS_READY,
)


def _fake_factory(**calls_log):
    """Returns a factory that records every call and builds FakeConnectors."""
    calls = calls_log.setdefault("calls", [])

    def factory(entry):
        calls.append(entry)
        return FakeConnector(name=entry["name"])

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
    # Defaults (e.g. search_limit) are the connector's concern now, not the registry's:
    # the factory receives the raw entry (minus allowed_groups) untouched.
    assert factory.calls[1] == {
        "name": "wiki",
        "command": ["wiki-mcp-server", "--flag"],
        "env": {},
    }
    assert registry.get("salesforce") is not None
    assert registry.get("wiki") is not None


def test_connector_specific_fields_flow_through_to_factory(tmp_path):
    config_path = tmp_path / "connectors.json"
    config_path.write_text(
        json.dumps(
            {
                "connectors": [
                    {
                        "name": "hubspot",
                        "command": ["hubspot-mcp-server"],
                        "env": {},
                        "allowed_groups": ["sales"],
                        "search_tool": "search_records",
                        "query_arg": "term",
                        "extra_args": {"object_type": "contacts"},
                    }
                ]
            }
        )
    )
    factory = _fake_factory()

    ConnectorRegistry.from_config(str(config_path), factory)

    entry = factory.calls[0]
    assert entry["search_tool"] == "search_records"
    assert entry["query_arg"] == "term"
    assert entry["extra_args"] == {"object_type": "contacts"}
    assert "allowed_groups" not in entry


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


def test_visible_probes_statuses_in_parallel_not_sequentially():
    """Two connectors that each hang far past the registry's connector_timeout must
    still return from visible() in ~one timeout, not two -- proving probes run in
    parallel rather than serializing (which would exhaust the threadpool on
    GET /connectors with several hung connectors)."""
    slow_a = FakeConnector(name="slow-a", status=CONNECTOR_STATUS_READY, status_delay=5.0)
    slow_b = FakeConnector(name="slow-b", status=CONNECTOR_STATUS_READY, status_delay=5.0)
    registry = ConnectorRegistry(
        [(slow_a, ["public"]), (slow_b, ["public"])], connector_timeout=0.2
    )

    start = time.monotonic()
    visible = registry.visible(["public"])
    elapsed = time.monotonic() - start

    assert elapsed < 2.0, f"visible() took {elapsed:.3f}s; expected ~timeout, not N*delay"
    assert ("slow-a", CONNECTOR_STATUS_DOWN, False) in visible
    assert ("slow-b", CONNECTOR_STATUS_DOWN, False) in visible


def test_visible_reports_fast_connector_while_another_hangs():
    fast = FakeConnector(name="fast", status=CONNECTOR_STATUS_READY)
    hung = FakeConnector(name="hung", status=CONNECTOR_STATUS_READY, status_delay=5.0)
    registry = ConnectorRegistry([(fast, ["public"]), (hung, ["public"])], connector_timeout=0.2)

    visible = registry.visible(["public"])

    assert ("fast", CONNECTOR_STATUS_READY, False) in visible
    assert ("hung", CONNECTOR_STATUS_DOWN, False) in visible


def test_from_config_skips_malformed_entry_and_keeps_good_ones(tmp_path, caplog):
    config_path = tmp_path / "connectors.json"
    config_path.write_text(
        json.dumps(
            {
                "connectors": [
                    {
                        "name": "good",
                        "command": ["good-mcp"],
                        "env": {},
                        "allowed_groups": ["public"],
                    },
                    {"name": "bad-missing-command", "allowed_groups": ["public"]},
                    {"command": ["also-bad"], "allowed_groups": ["public"]},
                ]
            }
        )
    )
    factory = _fake_factory()

    with caplog.at_level("WARNING"):
        registry = ConnectorRegistry.from_config(str(config_path), factory)

    assert len(factory.calls) == 1
    assert factory.calls[0]["name"] == "good"
    assert registry.get("good") is not None
    assert registry.get("bad-missing-command") is None
    assert any("malformed" in r.message for r in caplog.records)


def test_from_config_skips_duplicate_names(tmp_path, caplog):
    config_path = tmp_path / "connectors.json"
    config_path.write_text(
        json.dumps(
            {
                "connectors": [
                    {
                        "name": "dup",
                        "command": ["first-mcp"],
                        "env": {},
                        "allowed_groups": ["public"],
                    },
                    {
                        "name": "dup",
                        "command": ["second-mcp"],
                        "env": {},
                        "allowed_groups": ["sales"],
                    },
                ]
            }
        )
    )
    factory = _fake_factory()

    with caplog.at_level("WARNING"):
        registry = ConnectorRegistry.from_config(str(config_path), factory)

    assert len(factory.calls) == 1
    assert factory.calls[0]["command"] == ["first-mcp"]
    assert registry.get("dup") is not None
    assert any("duplicate" in r.message for r in caplog.records)


def test_from_config_skips_entry_when_factory_raises_and_keeps_the_rest(tmp_path, caplog):
    config_path = tmp_path / "connectors.json"
    config_path.write_text(
        json.dumps(
            {
                "connectors": [
                    {
                        "name": "boom",
                        "command": ["boom-mcp"],
                        "env": {},
                        "allowed_groups": ["public"],
                    },
                    {
                        "name": "good",
                        "command": ["good-mcp"],
                        "env": {},
                        "allowed_groups": ["public"],
                    },
                ]
            }
        )
    )

    def factory(entry):
        if entry["name"] == "boom":
            raise ValueError("bad config for boom")
        return FakeConnector(name=entry["name"])

    with caplog.at_level("WARNING"):
        registry = ConnectorRegistry.from_config(str(config_path), factory)

    assert registry.get("boom") is None
    assert registry.get("good") is not None
