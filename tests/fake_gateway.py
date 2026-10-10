"""Mercado Pago em memória, com os comportamentos conferidos no sandbox
(ver o docstring de app/payments/adapters/mercado_pago_gateway.py)."""

from decimal import Decimal

from app.shared.exceptions import PaymentProviderError, PaymentProviderRejection
from app.payments.domain.entities import ChargeItem, ProviderOrder
from app.payments.domain.ports import IPaymentGateway
from app.payments.domain.value_objects import ProviderStatus

RAW = {
    ProviderStatus.PENDING: "created",
    ProviderStatus.PAID: "processed",
    ProviderStatus.FAILED: "failed",
    ProviderStatus.EXPIRED: "expired",
    ProviderStatus.CANCELLED: "canceled",
    ProviderStatus.REFUNDED: "refunded",
}


class FakeMercadoPago(IPaymentGateway):
    def __init__(self) -> None:
        self.orders: dict[str, ProviderStatus] = {}
        self.by_idempotency_key: dict[str, str] = {}
        self.created: list[dict] = []
        self.calls: list[tuple[str, str]] = []
        self.fail_next: Exception | None = None
        self.pay_on_cancel = False  # simula pagamento entrando no meio da compensação

    def _order(self, order_id: str) -> ProviderOrder:
        status = self.orders[order_id]
        return ProviderOrder(order_id, status, RAW[status], None, f"https://mp.test/checkout?order_id={order_id}")

    def _maybe_fail(self) -> None:
        if self.fail_next:
            error, self.fail_next = self.fail_next, None
            raise error

    def create_order(self, *, idempotency_key: str, external_reference: str, amount: Decimal, items: list[ChargeItem], payer_email: str, return_url: str) -> ProviderOrder:
        self.calls.append(("create", idempotency_key))
        self._maybe_fail()
        if idempotency_key in self.by_idempotency_key:
            return self._order(self.by_idempotency_key[idempotency_key])
        order_id = f"ORDTST{len(self.orders) + 1:04d}"
        self.orders[order_id] = ProviderStatus.PENDING
        self.by_idempotency_key[idempotency_key] = order_id
        self.created.append({"external_reference": external_reference, "amount": amount, "items": items, "payer_email": payer_email, "return_url": return_url})
        return self._order(order_id)

    def get_order(self, provider_order_id: str) -> ProviderOrder | None:
        self.calls.append(("get", provider_order_id))
        self._maybe_fail()
        return self._order(provider_order_id) if provider_order_id in self.orders else None

    def cancel_order(self, provider_order_id: str, idempotency_key: str) -> ProviderOrder:
        self.calls.append(("cancel", provider_order_id))
        if self.pay_on_cancel:
            self.orders[provider_order_id], self.pay_on_cancel = ProviderStatus.PAID, False
        status = self.orders[provider_order_id]
        if status == ProviderStatus.CANCELLED:
            return self._order(provider_order_id)  # o adapter real traduz order_already_canceled em sucesso
        if status != ProviderStatus.PENDING:
            raise PaymentProviderRejection("cannot_cancel_order", f"status {RAW[status]}")
        self.orders[provider_order_id] = ProviderStatus.CANCELLED
        return self._order(provider_order_id)

    def refund_order(self, provider_order_id: str, idempotency_key: str) -> ProviderOrder:
        self.calls.append(("refund", provider_order_id))
        if self.orders[provider_order_id] != ProviderStatus.PAID:
            raise PaymentProviderRejection("cannot_refund_order", "The order must be in 'processed' status to be refunded.")
        self.orders[provider_order_id] = ProviderStatus.REFUNDED
        return self._order(provider_order_id)

    # helpers dos testes
    def set_status(self, provider_order_id: str, status: ProviderStatus) -> None:
        self.orders[provider_order_id] = status

    def technical_failure(self) -> None:
        self.fail_next = PaymentProviderError("timeout")
