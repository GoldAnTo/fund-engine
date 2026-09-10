"""Private Gateway responses must prohibit storage, including rejected reads."""

import pytest

from tests.test_research_gateway_api import BASE, _start
from tests.test_research_gateway_api import (
    gateway_client as gateway_client,  # noqa: PLC0414 -- pytest fixture re-export
)


@pytest.mark.parametrize("path", [BASE, "/api/v1/research-session"])
def test_private_listing_and_identity_prohibit_storage(gateway_client, path):
    client, _ = gateway_client
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers.get("Cache-Control") == "no-store"


@pytest.mark.parametrize("resource", ["receipt", "snapshot"])
def test_private_creation_and_snapshot_prohibit_storage(gateway_client, resource):
    client, _ = gateway_client
    receipt = _start(client)
    assert receipt.status_code == 201
    response = receipt
    if resource == "snapshot":
        response = client.get(f"{BASE}/{receipt.json()['conversation_id']}/snapshot")
        assert response.status_code == 200
    assert response.headers.get("Cache-Control") == "no-store"


@pytest.mark.parametrize("status", [401, 404, 422])
def test_private_rejected_reads_prohibit_storage(gateway_client, status):
    client, _ = gateway_client
    if status == 401:
        client.headers.pop("Authorization")
        path = BASE
    elif status == 404:
        path = f"{BASE}/00000000-0000-0000-0000-000000000000"
    else:
        path = f"{BASE}/invalid-uuid"
    response = client.get(path)
    assert response.status_code == status
    assert response.headers.get("Cache-Control") == "no-store"
