from fastapi import Depends

from kilnworks.adapters.connectors.fake import FakeConnector
from kilnworks.api.deps import get_conn, get_services, get_settings
from kilnworks.core.connectors import ConnectorRegistry
from kilnworks.core.models import CONNECTOR_STATUS_NEEDS_LOGIN, CONNECTOR_STATUS_READY
from kilnworks.wiring import build_services_prepared

from tests.api.test_ask_endpoints import _token
from tests.api.test_auth_endpoints import _register


def _override_with_registry(client, registry: ConnectorRegistry) -> None:
    """Swap in a fake connector registry via FastAPI dependency override, leaving
    the rest of `get_services`'s wiring (real ingestion/query/media) untouched."""

    def override_get_services(
        settings=Depends(get_settings), conn=Depends(get_conn)
    ):
        services = build_services_prepared(settings, conn)
        services.connectors = registry
        return services

    client.app.dependency_overrides[get_services] = override_get_services


def test_list_connectors_filters_by_group(client, api_settings):
    _register(api_settings, email="pub@example.com", principals=("public",))
    _register(api_settings, email="hr@example.com", principals=("public", "hr"))

    registry = ConnectorRegistry(
        [
            (FakeConnector("public-search", status=CONNECTOR_STATUS_READY), ["public"]),
            (FakeConnector("hr-search", status=CONNECTOR_STATUS_NEEDS_LOGIN), ["hr"]),
        ]
    )
    _override_with_registry(client, registry)

    pub_headers = {"Authorization": f"Bearer {_token(client, email='pub@example.com')}"}
    hr_headers = {"Authorization": f"Bearer {_token(client, email='hr@example.com')}"}

    pub_response = client.get("/connectors", headers=pub_headers)
    assert pub_response.status_code == 200
    assert pub_response.json() == [
        {"name": "public-search", "status": "ready", "needs_login": False}
    ]

    hr_response = client.get("/connectors", headers=hr_headers)
    assert hr_response.status_code == 200
    hr_connectors = hr_response.json()
    assert {c["name"] for c in hr_connectors} == {"public-search", "hr-search"}
    hr_entry = next(c for c in hr_connectors if c["name"] == "hr-search")
    assert hr_entry["status"] == "needs_login"
    assert hr_entry["needs_login"] is True
