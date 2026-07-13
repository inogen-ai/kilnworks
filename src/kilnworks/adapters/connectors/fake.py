import time

from kilnworks.core.models import CONNECTOR_STATUS_READY, ConnectorResult


class FakeConnector:
    """Canned Connector for tests; records every call."""

    def __init__(
        self,
        name: str,
        results: list[ConnectorResult] | None = None,
        status: str = CONNECTOR_STATUS_READY,
        raises: Exception | None = None,
        delay: float = 0.0,
    ):
        self.name = name
        self.results = results or []
        self._status = status
        self.raises = raises
        self.delay = delay
        self.calls: list[tuple[str, int]] = []
        self.status_calls = 0

    def search(self, query: str, limit: int) -> list[ConnectorResult]:
        self.calls.append((query, limit))
        if self.delay > 0:
            time.sleep(self.delay)
        if self.raises is not None:
            raise self.raises
        return self.results[:limit]

    def status(self) -> str:
        self.status_calls += 1
        return self._status
