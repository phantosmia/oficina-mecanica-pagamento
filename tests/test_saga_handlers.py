from decimal import Decimal

import pytest

from app.messaging.dispatcher import Dispatcher
from app.shared.exceptions import PaymentProviderError, PaymentProviderRejection
from app.payments.application.saga_handlers import charge_id_for
from app.payments.domain.value_objects import ChargeStatus, ProviderStatus
from tests.conftest import FakeNotifier, charge_of, charge_payload, command_msg, create_charge, outbox
from tests.fake_gateway import FakeMercadoPago


def test_create_charge(dispatcher: Dispatcher, mp: FakeMercadoPago, notifier: FakeNotifier) -> None:
    command = create_charge(dispatcher)

    charge = charge_of()
    assert (charge.id, charge.status, charge.amount) == (charge_id_for("saga-42"), ChargeStatus.AWAITING_PAYMENT, Decimal("330.40"))
    [sent_to_mp] = mp.created
    assert sent_to_mp["external_reference"] == "os-42"
    assert sent_to_mp["amount"] == Decimal("330.40")
    assert [(i.title, i.quantity, i.unit_price) for i in sent_to_mp["items"]] == [("Troca de óleo", 1, Decimal("150.00")), ("Óleo 5W30", 4, Decimal("45.10"))]
    assert sent_to_mp["return_url"] == f"https://pagamento.test/pagamentos/{charge.id}/retorno"
    assert mp.calls[0] == ("create", "cobranca-saga-42")

    [reply] = outbox()
    assert (reply["type"], reply["saga_id"], reply["order_id"]) == ("CobrancaCriada", command.saga_id, 42)
    assert reply["payload"] == {"charge_id": charge.id, "provider_order_id": charge.provider_order_id, "checkout_url": charge.checkout_url, "amount": 330.4}
    [email] = notifier.sent
    assert email.to == "maria@example.com" and charge.checkout_url in email.body


def test_amount_comes_from_items(dispatcher: Dispatcher) -> None:
    create_charge(dispatcher, total=1.0)
    assert charge_of().amount == Decimal("330.40")


def test_sandbox_payer_email_override(dispatcher: Dispatcher, mp: FakeMercadoPago, monkeypatch: pytest.MonkeyPatch) -> None:
    from dataclasses import replace

    import app.payments.application.saga_handlers as handlers

    monkeypatch.setattr(handlers, "settings", replace(handlers.settings, mp_payer_email_override="comprador@testuser.com"))
    create_charge(dispatcher)
    assert mp.created[0]["payer_email"] == "comprador@testuser.com"


def test_redelivery_and_resend(dispatcher: Dispatcher, mp: FakeMercadoPago, notifier: FakeNotifier) -> None:
    command = create_charge(dispatcher)
    dispatcher.handle(command)  # reentrega: nada
    create_charge(dispatcher)  # reenvio: mesma resposta, sem nova cobrança nem e-mail

    assert len(mp.created) == 1 and len(notifier.sent) == 1
    assert [m["type"] for m in outbox()] == ["CobrancaCriada", "CobrancaCriada"]


def test_crash_after_provider_call_does_not_duplicate_charge(dispatcher: Dispatcher, mp: FakeMercadoPago, monkeypatch: pytest.MonkeyPatch) -> None:
    """O serviço cria a cobrança no Mercado Pago e cai antes de gravar: ao
    reprocessar, a chave de idempotência derivada da saga devolve a mesma order."""
    from app.payments.adapters import sqlalchemy_uow

    original = sqlalchemy_uow.SqlAlchemyPaymentUnitOfWork.save
    monkeypatch.setattr(sqlalchemy_uow.SqlAlchemyPaymentUnitOfWork, "save", lambda self, charge: (_ for _ in ()).throw(ConnectionError("banco caiu")))
    command = command_msg("CriarCobranca", **charge_payload())
    with pytest.raises(ConnectionError):
        dispatcher.handle(command)
    assert charge_of() is None and outbox() == []

    monkeypatch.setattr(sqlalchemy_uow.SqlAlchemyPaymentUnitOfWork, "save", original)
    dispatcher.handle(command)  # a mensagem volta da fila

    assert len(mp.orders) == 1
    assert charge_of().provider_order_id == next(iter(mp.orders))


def test_provider_technical_failure_retries(dispatcher: Dispatcher, mp: FakeMercadoPago) -> None:
    mp.technical_failure()
    with pytest.raises(PaymentProviderError):
        create_charge(dispatcher)
    assert charge_of() is None and outbox() == []


def test_provider_rejection_fails_the_step(dispatcher: Dispatcher, mp: FakeMercadoPago) -> None:
    mp.fail_next = PaymentProviderRejection("invalid_total_amount", "valor inválido")
    create_charge(dispatcher)
    assert outbox()[-1]["type"] == "CobrancaFalhou"
    assert outbox()[-1]["payload"] == {"reason": "provedor_recusou", "code": "invalid_total_amount"}


def test_invalid_payload(dispatcher: Dispatcher, mp: FakeMercadoPago) -> None:
    create_charge(dispatcher, services=[], parts=[])
    create_charge(dispatcher, order_id=43, parts=[{"name": "x", "quantity": "dois", "unit_price": 1}])
    assert [m["payload"] for m in outbox()] == [{"reason": "payload_invalido"}, {"reason": "payload_invalido"}]
    assert mp.created == []


# ── Compensações ─────────────────────────────────────────────────────────────


def test_cancel_unpaid_charge(dispatcher: Dispatcher, mp: FakeMercadoPago) -> None:
    create_charge(dispatcher)
    dispatcher.handle(command_msg("CancelarCobranca"))
    dispatcher.handle(command_msg("CancelarCobranca"))  # repetida: confirma de novo, sem nova chamada

    charge = charge_of()
    assert charge.status == ChargeStatus.CANCELLED
    assert mp.orders[charge.provider_order_id] == ProviderStatus.CANCELLED
    assert [c for c, _ in mp.calls].count("cancel") == 1
    assert [(m["type"], m["payload"]["refunded"]) for m in outbox()[1:]] == [("CobrancaCancelada", False), ("CobrancaCancelada", False)]


def test_cancel_refunds_if_payment_arrived(dispatcher: Dispatcher, mp: FakeMercadoPago) -> None:
    create_charge(dispatcher)
    mp.set_status(charge_of().provider_order_id, ProviderStatus.PAID)

    dispatcher.handle(command_msg("CancelarCobranca"))

    assert charge_of().status == ChargeStatus.REFUNDED
    assert outbox()[-1]["payload"]["refunded"] is True


def test_payment_arriving_during_cancel_is_refunded(dispatcher: Dispatcher, mp: FakeMercadoPago) -> None:
    create_charge(dispatcher)
    mp.pay_on_cancel = True

    dispatcher.handle(command_msg("CancelarCobranca"))

    assert charge_of().status == ChargeStatus.REFUNDED
    assert mp.orders[charge_of().provider_order_id] == ProviderStatus.REFUNDED


def test_refund_paid_charge(dispatcher: Dispatcher, mp: FakeMercadoPago) -> None:
    create_charge(dispatcher)
    mp.set_status(charge_of().provider_order_id, ProviderStatus.PAID)

    dispatcher.handle(command_msg("EstornarPagamento"))
    dispatcher.handle(command_msg("EstornarPagamento"))

    assert charge_of().status == ChargeStatus.REFUNDED and charge_of().refunded_at is not None
    assert [c for c, _ in mp.calls].count("refund") == 1
    assert [(m["type"], m["payload"]["refunded"]) for m in outbox()[-2:]] == [("PagamentoEstornado", True), ("PagamentoEstornado", True)]


def test_refund_of_unpaid_charge_cancels_it(dispatcher: Dispatcher, mp: FakeMercadoPago) -> None:
    create_charge(dispatcher)
    dispatcher.handle(command_msg("EstornarPagamento"))
    assert charge_of().status == ChargeStatus.CANCELLED
    assert outbox()[-1]["payload"]["refunded"] is False


def test_compensation_before_charge_blocks_late_creation(dispatcher: Dispatcher, mp: FakeMercadoPago, notifier: FakeNotifier) -> None:
    """Fora de ordem: o cliente nunca pode ser cobrado numa saga já compensada."""
    dispatcher.handle(command_msg("CancelarCobranca"))
    create_charge(dispatcher)

    assert mp.created == [] and notifier.sent == []
    assert [(m["type"], m["payload"].get("reason")) for m in outbox()] == [("CobrancaCancelada", None), ("CobrancaFalhou", "saga_ja_compensada")]


def test_unknown_type(dispatcher: Dispatcher) -> None:
    assert dispatcher.handle(command_msg("Desconhecido")) is False
