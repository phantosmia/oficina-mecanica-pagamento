"""Os três caminhos de confirmação: webhook, retorno do comprador e reconciliação."""

import hashlib
import hmac
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.messaging.dispatcher import Dispatcher
from app.shared.database import get_engine
from app.payments.application.sync import ReconcilePendingChargesUseCase, SyncChargeUseCase
from app.payments.domain.value_objects import ChargeStatus, ProviderStatus
from tests.conftest import FakeNotifier, charge_of, create_charge, outbox, uow_factory
from tests.fake_gateway import FakeMercadoPago


def notifications() -> list[tuple]:
    with get_engine().connect() as connection:
        return list(connection.execute(text("SELECT provider_order_id, signature_valid FROM payment_notifications ORDER BY id")))


def test_webhook_confirms_payment_by_querying_provider(client: TestClient, dispatcher: Dispatcher, mp: FakeMercadoPago, notifier: FakeNotifier) -> None:
    create_charge(dispatcher)
    order_id = charge_of().provider_order_id

    # Notificação chega antes do pagamento entrar no provedor: nada muda.
    client.post(f"/webhooks/mercado-pago?data.id={order_id}&type=order", json={"type": "order", "data": {"id": order_id}})
    assert charge_of().status == ChargeStatus.AWAITING_PAYMENT

    mp.set_status(order_id, ProviderStatus.PAID)
    response = client.post(f"/webhooks/mercado-pago?data.id={order_id}&type=order", json={"type": "order", "action": "order.processed", "data": {"id": order_id}})

    assert response.json() == {"received": True}
    assert charge_of().status == ChargeStatus.PAID
    confirmed = outbox()[-1]
    assert (confirmed["type"], confirmed["saga_id"], confirmed["order_id"]) == ("PagamentoConfirmado", "saga-42", 42)
    assert confirmed["payload"]["amount"] == 330.4
    assert notifier.sent[-1].subject == "[OS #42] Pagamento confirmado"
    assert len(notifications()) == 2


def test_duplicate_webhook_emits_once(client: TestClient, dispatcher: Dispatcher, mp: FakeMercadoPago) -> None:
    create_charge(dispatcher)
    order_id = charge_of().provider_order_id
    mp.set_status(order_id, ProviderStatus.PAID)
    for _ in range(3):
        client.post(f"/webhooks/mercado-pago?data.id={order_id}", json={"data": {"id": order_id}})
    assert [m["type"] for m in outbox()].count("PagamentoConfirmado") == 1


def _signature(secret: str, data_id: str, request_id: str, ts: str = "1728561600000") -> str:
    manifest = f"id:{data_id.lower()};request-id:{request_id};ts:{ts};"
    return f"ts={ts},v1={hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()}"


def test_webhook_signature(client: TestClient, dispatcher: Dispatcher, mp: FakeMercadoPago, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.payments.controller as controller

    monkeypatch.setattr(controller, "settings", replace(controller.settings, mp_webhook_secret="segredo-do-painel"))
    create_charge(dispatcher)
    order_id = charge_of().provider_order_id
    mp.set_status(order_id, ProviderStatus.PAID)

    forged = client.post(f"/webhooks/mercado-pago?data.id={order_id}", json={"data": {"id": order_id}}, headers={"x-signature": "ts=1,v1=00", "x-request-id": "r1"})
    assert forged.status_code == 401
    assert charge_of().status == ChargeStatus.AWAITING_PAYMENT

    valid = client.post(
        f"/webhooks/mercado-pago?data.id={order_id}",
        json={"data": {"id": order_id}},
        headers={"x-signature": _signature("segredo-do-painel", order_id, "r2"), "x-request-id": "r2"},
    )
    assert valid.status_code == 200
    assert charge_of().status == ChargeStatus.PAID
    assert [valid_flag for _, valid_flag in notifications()] == [False, True]


def test_webhook_of_unknown_order_is_acknowledged(client: TestClient, mp: FakeMercadoPago) -> None:
    assert client.post("/webhooks/mercado-pago?data.id=ORD-DE-OUTRO", json={}).status_code == 200
    assert client.post("/webhooks/mercado-pago", content=b"isto nao e json").status_code == 200


def test_buyer_return_page_syncs(client: TestClient, dispatcher: Dispatcher, mp: FakeMercadoPago) -> None:
    create_charge(dispatcher)
    charge = charge_of()
    assert "Ainda não recebemos" in client.get(f"/pagamentos/{charge.id}/retorno").text

    mp.set_status(charge.provider_order_id, ProviderStatus.PAID)
    page = client.get(f"/pagamentos/{charge.id}/retorno")
    assert "Pagamento confirmado" in page.text and "R$ 330.40" in page.text
    assert charge_of().status == ChargeStatus.PAID
    assert client.get("/pagamentos/nao-existe/retorno").status_code == 404


def test_reconciliation_catches_payment_and_expiration(dispatcher: Dispatcher, mp: FakeMercadoPago, notifier: FakeNotifier) -> None:
    create_charge(dispatcher, order_id=1)
    create_charge(dispatcher, order_id=2)
    create_charge(dispatcher, order_id=3)
    mp.set_status(charge_of(1).provider_order_id, ProviderStatus.PAID)
    mp.set_status(charge_of(2).provider_order_id, ProviderStatus.EXPIRED)
    mp.technical_failure()  # a primeira consulta falha: as outras seguem

    sync = SyncChargeUseCase(uow_factory, mp, notifier)
    reconcile = ReconcilePendingChargesUseCase(uow_factory, sync, min_age_seconds=0)
    reconcile.execute()
    reconcile.execute()

    assert (charge_of(1).status, charge_of(2).status, charge_of(3).status) == (ChargeStatus.PAID, ChargeStatus.DECLINED, ChargeStatus.AWAITING_PAYMENT)
    recusado = next(m for m in outbox() if m["type"] == "PagamentoRecusado")
    assert (recusado["order_id"], recusado["payload"]["provider_status"]) == (2, "expired")
    assert reconcile.execute() == 0
