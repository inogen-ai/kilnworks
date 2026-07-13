"""Registry of configured connectors with group-gated access.

Pure `core`: the connector factory (which knows how to construct a real
`MCPStdioConnector`) is injected rather than imported, so this module never
depends on `adapters` or `mcp`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence

from kilnworks.core.models import CONNECTOR_STATUS_NEEDS_LOGIN
from kilnworks.core.ports import Connector

DEFAULT_SEARCH_LIMIT = 5

ConnectorFactory = Callable[[str, list[str], dict[str, str], int], Connector]


class ConnectorRegistry:
    """Holds configured connectors paired with the groups allowed to use them."""

    def __init__(self, entries: Sequence[tuple[Connector, Sequence[str]]]):
        self._entries: list[tuple[Connector, set[str]]] = [
            (connector, set(allowed_groups)) for connector, allowed_groups in entries
        ]

    @classmethod
    def from_config(cls, path: str, factory: ConnectorFactory) -> ConnectorRegistry:
        with open(path) as f:
            config = json.load(f)

        entries: list[tuple[Connector, Sequence[str]]] = []
        for entry in config.get("connectors", []):
            name = entry["name"]
            command = entry["command"]
            env = {key: os.path.expandvars(value) for key, value in entry.get("env", {}).items()}
            search_limit = entry.get("search_limit", DEFAULT_SEARCH_LIMIT)
            allowed_groups = entry["allowed_groups"]
            connector = factory(name, command, env, search_limit)
            entries.append((connector, allowed_groups))

        return cls(entries)

    def allowed_for(self, principals: Sequence[str]) -> list[Connector]:
        principal_set = set(principals)
        return [
            connector
            for connector, allowed_groups in self._entries
            if allowed_groups & principal_set
        ]

    def get(self, name: str) -> Connector | None:
        for connector, _allowed_groups in self._entries:
            if connector.name == name:
                return connector
        return None

    def visible(self, principals: Sequence[str]) -> list[tuple[str, str, bool]]:
        result = []
        for connector in self.allowed_for(principals):
            status = connector.status()
            result.append((connector.name, status, status == CONNECTOR_STATUS_NEEDS_LOGIN))
        return result
