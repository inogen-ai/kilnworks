"""Registry of configured connectors with group-gated access.

Pure `core`: the connector factory (which knows how to construct a real
`MCPStdioConnector`) is injected rather than imported, so this module never
depends on `adapters` or `mcp`.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
from collections.abc import Callable, Sequence

from kilnworks.core.models import CONNECTOR_STATUS_DOWN, CONNECTOR_STATUS_NEEDS_LOGIN
from kilnworks.core.ports import Connector

logger = logging.getLogger(__name__)

# The factory receives the full parsed config entry (minus `allowed_groups`, which the
# registry keeps), with `env` values expanded. It knows how to build a real connector
# (e.g. MCPStdioConnector) and is injected so this module never imports `adapters`/`mcp`.
ConnectorFactory = Callable[[dict], Connector]


class ConnectorRegistry:
    """Holds configured connectors paired with the groups allowed to use them."""

    def __init__(
        self,
        entries: Sequence[tuple[Connector, Sequence[str]]],
        connector_timeout: float = 8.0,
    ):
        self._entries: list[tuple[Connector, set[str]]] = [
            (connector, set(allowed_groups)) for connector, allowed_groups in entries
        ]
        self._connector_timeout = connector_timeout

    @classmethod
    def from_config(
        cls, path: str, factory: ConnectorFactory, connector_timeout: float = 8.0
    ) -> ConnectorRegistry:
        with open(path) as f:
            config = json.load(f)

        entries: list[tuple[Connector, Sequence[str]]] = []
        seen_names: set[str] = set()
        for raw in config.get("connectors", []):
            entry = dict(raw)
            name = entry.get("name")
            missing = []
            if not entry.get("name"):
                missing.append("name")
            if not entry.get("command"):
                missing.append("command")
            if "allowed_groups" not in entry:
                missing.append("allowed_groups")
            if missing:
                logger.warning(
                    "skipping malformed connector entry (missing %s): %r", missing, raw
                )
                continue
            if name in seen_names:
                logger.warning("skipping connector entry with duplicate name %r", name)
                continue

            allowed_groups = entry.pop("allowed_groups")
            if "env" in entry:
                entry["env"] = {
                    key: os.path.expandvars(value) for key, value in entry["env"].items()
                }
            try:
                connector = factory(entry)
            except Exception:
                logger.warning(
                    "skipping connector %r: failed to build from config", name, exc_info=True
                )
                continue

            seen_names.add(name)
            entries.append((connector, allowed_groups))

        return cls(entries, connector_timeout=connector_timeout)

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
        # Probe every allowed connector's status() in PARALLEL, bounded by one shared
        # deadline (connector_timeout) -- the same as_completed-with-timeout pattern
        # QueryService._federated_results uses for search(). status() spawns a fresh
        # subprocess per connector; probing sequentially would let N hung/slow
        # connectors serialize (up to timeout+~2s teardown grace *each*), starving
        # the threadpool behind a single GET /connectors call. A connector that
        # doesn't respond in time is reported down rather than waited on.
        connectors = self.allowed_for(principals)
        if not connectors:
            return []

        statuses: dict[str, str] = {}
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(connectors)))
        try:
            futures = {executor.submit(c.status): c for c in connectors}
            try:
                for future in concurrent.futures.as_completed(
                    futures, timeout=self._connector_timeout
                ):
                    connector = futures[future]
                    try:
                        statuses[connector.name] = future.result()
                    except Exception:
                        statuses[connector.name] = CONNECTOR_STATUS_DOWN
                        logger.warning(
                            "connector %r status probe failed; marking down",
                            connector.name,
                            exc_info=True,
                        )
            except concurrent.futures.TimeoutError:
                # Any futures not yet done have blown the shared deadline; report them
                # down without waiting for their threads to finish.
                for future, connector in futures.items():
                    if not future.done():
                        statuses[connector.name] = CONNECTOR_STATUS_DOWN
                        logger.warning(
                            "connector %r status probe timed out; marking down", connector.name
                        )
        finally:
            # Don't block visible() on connector threads still running past the
            # shared deadline; let them finish (or not) in the background.
            executor.shutdown(wait=False, cancel_futures=True)

        return [
            (
                connector.name,
                statuses[connector.name],
                statuses[connector.name] == CONNECTOR_STATUS_NEEDS_LOGIN,
            )
            for connector in connectors
        ]
