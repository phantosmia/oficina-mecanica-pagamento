"""Adapter do Mercado Pago: Checkout Pro via API de Orders.

Comportamentos conferidos contra o sandbox (2026-10-10), não só na documentação:

- criar: `type=online`, `processing_mode=manual`, `capture_mode=automatic_async`
  (com `processing_mode=automatic` a API exige dados de cartão: é o modo do
  Checkout Transparente). Resposta com status `created` e o `checkout_url`.
- cancelar duas vezes: a segunda responde erro `order_already_canceled`
  (tratado aqui como sucesso: a compensação é idempotente).
- estornar só é aceito com a order em `processed`
  (`cannot_refund_order` em qualquer outro status).
- consultar uma order inexistente: HTTP 404.
- `X-Idempotency-Key` é obrigatório nas operações de escrita.
- no sandbox, `payer.email` precisa terminar em `@testuser.com`.
"""

import logging
from decimal import Decimal
from typing import Any

import httpx

from app.shared.exceptions import PaymentProviderError, PaymentProviderRejection
from app.payments.domain.entities import ChargeItem, ProviderOrder
from app.payments.domain.ports import IPaymentGateway
from app.payments.domain.value_objects import ProviderStatus

logger = logging.getLogger(__name__)

STATUS_MAP = {
    "created": ProviderStatus.PENDING,
    "action_required": ProviderStatus.PENDING,
    "processing": ProviderStatus.PENDING,
    "processed": ProviderStatus.PAID,
    "failed": ProviderStatus.FAILED,
    "expired": ProviderStatus.EXPIRED,
    "canceled": ProviderStatus.CANCELLED,
    "refunded": ProviderStatus.REFUNDED,
}


def _amount(value: Decimal) -> str:
    return f"{value:.2f}"


def to_provider_order(body: dict[str, Any]) -> ProviderOrder:
    raw = str(body.get("status") or "")
    status = STATUS_MAP.get(raw, ProviderStatus.UNKNOWN)
    if status == ProviderStatus.UNKNOWN:
        logger.warning("status desconhecido do Mercado Pago: %s (order %s)", raw, body.get("id"))
    return ProviderOrder(
        id=str(body["id"]),
        status=status,
        raw_status=raw,
        raw_status_detail=body.get("status_detail"),
        checkout_url=body.get("checkout_url"),
    )


class MercadoPagoGateway(IPaymentGateway):
    def __init__(
        self,
        access_token: str,
        base_url: str = "https://api.mercadopago.com",
        timeout_seconds: float = 10,
        expiration: str = "P3D",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout_seconds,
            headers={"Authorization": f"Bearer {access_token}"},
            transport=transport,
        )
        self._expiration = expiration

    def create_order(
        self,
        *,
        idempotency_key: str,
        external_reference: str,
        amount: Decimal,
        items: list[ChargeItem],
        payer_email: str,
        return_url: str,
    ) -> ProviderOrder:
        body = {
            "type": "online",
            "processing_mode": "manual",
            "capture_mode": "automatic_async",
            "total_amount": _amount(amount),
            "external_reference": external_reference,
            "expiration_time": self._expiration,
            "payer": {"email": payer_email},
            "items": [{"title": i.title, "quantity": i.quantity, "unit_price": _amount(i.unit_price)} for i in items],
            "config": {
                "online": {"success_url": return_url, "failure_url": return_url, "pending_url": return_url, "auto_return": "all"},
            },
        }
        return to_provider_order(self._request("POST", "/v1/orders", idempotency_key, json=body))

    def get_order(self, provider_order_id: str) -> ProviderOrder | None:
        try:
            return to_provider_order(self._request("GET", f"/v1/orders/{provider_order_id}"))
        except _NotFound:
            return None

    def cancel_order(self, provider_order_id: str, idempotency_key: str) -> ProviderOrder:
        try:
            return to_provider_order(self._request("POST", f"/v1/orders/{provider_order_id}/cancel", idempotency_key))
        except PaymentProviderRejection as rejection:
            if rejection.code == "order_already_canceled":
                return ProviderOrder(id=provider_order_id, status=ProviderStatus.CANCELLED, raw_status="canceled")
            raise

    def refund_order(self, provider_order_id: str, idempotency_key: str) -> ProviderOrder:
        return to_provider_order(self._request("POST", f"/v1/orders/{provider_order_id}/refund", idempotency_key))

    def _request(self, method: str, path: str, idempotency_key: str | None = None, json: dict | None = None) -> dict[str, Any]:
        headers = {"X-Idempotency-Key": idempotency_key} if idempotency_key else {}
        try:
            response = self._client.request(method, path, json=json, headers=headers)
        except httpx.HTTPError as error:
            raise PaymentProviderError(f"Mercado Pago indisponível: {error}") from error
        if response.status_code == 404:
            raise _NotFound(path)
        if response.status_code >= 500 or response.status_code == 429:
            raise PaymentProviderError(f"Mercado Pago respondeu {response.status_code} em {method} {path}")
        body = response.json() if response.content else {}
        if response.status_code >= 400:
            error = (body.get("errors") or [{}])[0]
            raise PaymentProviderRejection(str(error.get("code") or response.status_code), str(error.get("message") or body))
        return body


class _NotFound(Exception):
    pass
