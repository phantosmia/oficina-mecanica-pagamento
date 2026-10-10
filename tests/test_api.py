import pytest
from fastapi.testclient import TestClient

from app.messaging.dispatcher import Dispatcher
from app.payments.domain.value_objects import ProviderStatus
from tests.conftest import charge_of, create_charge, make_token
from tests.fake_gateway import FakeMercadoPago


@pytest.mark.parametrize(("method", "path"), [("get", "/charges/42"), ("post", "/charges/42/sync")])
def test_admin_routes_require_admin(client: TestClient, method: str, path: str) -> None:
    assert getattr(client, method)(path).status_code == 401
    assert getattr(client, method)(path, headers={"Authorization": f"Bearer {make_token('12345678909')}"}).status_code == 401


def test_admin_sees_and_syncs_charge(client: TestClient, admin_headers: dict[str, str], dispatcher: Dispatcher, mp: FakeMercadoPago) -> None:
    create_charge(dispatcher)
    body = client.get("/charges/42", headers=admin_headers).json()
    assert (body["status"], body["amount"], body["provider_status"]) == ("aguardando_pagamento", "330.40", "created")
    assert body["checkout_url"].startswith("https://mp.test/checkout")

    mp.set_status(charge_of().provider_order_id, ProviderStatus.PAID)
    assert client.post("/charges/42/sync", headers=admin_headers).json()["status"] == "pago"


def test_admin_not_found(client: TestClient, admin_headers: dict[str, str], mp: FakeMercadoPago) -> None:
    assert client.get("/charges/999", headers=admin_headers).status_code == 404
    assert client.post("/charges/999/sync", headers=admin_headers).status_code == 404
