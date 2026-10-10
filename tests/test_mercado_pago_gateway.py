"""O adapter real contra respostas HTTP simuladas no formato que o sandbox devolveu."""

import json
from decimal import Decimal

import httpx
import pytest

from app.shared.exceptions import PaymentProviderError, PaymentProviderRejection
from app.payments.adapters.mercado_pago_gateway import MercadoPagoGateway
from app.payments.domain.entities import ChargeItem
from app.payments.domain.value_objects import ProviderStatus

ORDER = {"id": "ORDTST01", "status": "created", "checkout_url": "https://www.mercadopago.com.br/checkout/v1/redirect?order_id=ORDTST01"}


def gateway(handler) -> MercadoPagoGateway:  # noqa: ANN001
    return MercadoPagoGateway("TEST-token", "https://api.mp.test", 5, "P3D", transport=httpx.MockTransport(handler))


def test_create_order_request() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(path=request.url.path, headers=request.headers, body=json.loads(request.content))
        return httpx.Response(201, json=ORDER)

    order = gateway(handler).create_order(
        idempotency_key="cobranca-saga-1",
        external_reference="os-1",
        amount=Decimal("330.40"),
        items=[ChargeItem("Troca de óleo", 1, Decimal("150.00")), ChargeItem("Óleo", 4, Decimal("45.10"))],
        payer_email="comprador@testuser.com",
        return_url="https://pagamento.test/pagamentos/c1/retorno",
    )

    assert (order.id, order.status, order.checkout_url) == ("ORDTST01", ProviderStatus.PENDING, ORDER["checkout_url"])
    assert seen["path"] == "/v1/orders"
    assert seen["headers"]["Authorization"] == "Bearer TEST-token"
    assert seen["headers"]["X-Idempotency-Key"] == "cobranca-saga-1"
    body = seen["body"]
    assert (body["type"], body["processing_mode"], body["capture_mode"]) == ("online", "manual", "automatic_async")
    assert (body["total_amount"], body["expiration_time"], body["payer"]) == ("330.40", "P3D", {"email": "comprador@testuser.com"})
    assert body["items"] == [{"title": "Troca de óleo", "quantity": 1, "unit_price": "150.00"}, {"title": "Óleo", "quantity": 4, "unit_price": "45.10"}]
    assert body["config"]["online"]["success_url"] == "https://pagamento.test/pagamentos/c1/retorno"


@pytest.mark.parametrize(("raw", "expected"), [("processed", ProviderStatus.PAID), ("expired", ProviderStatus.EXPIRED), ("action_required", ProviderStatus.PENDING), ("charged_back", ProviderStatus.UNKNOWN)])
def test_status_mapping(raw: str, expected: ProviderStatus) -> None:
    assert gateway(lambda r: httpx.Response(200, json={**ORDER, "status": raw})).get_order("ORDTST01").status == expected


def test_unknown_order_is_none() -> None:
    assert gateway(lambda r: httpx.Response(404, json={})).get_order("ORDX") is None


def test_cancel_twice_is_success() -> None:
    error = {"errors": [{"code": "order_already_canceled", "message": "the order is already canceled."}]}
    assert gateway(lambda r: httpx.Response(400, json=error)).cancel_order("ORDTST01", "k").status == ProviderStatus.CANCELLED


def test_business_rejection() -> None:
    error = {"errors": [{"code": "cannot_refund_order", "message": "The order must be in 'processed' status to be refunded."}]}
    with pytest.raises(PaymentProviderRejection) as raised:
        gateway(lambda r: httpx.Response(400, json=error)).refund_order("ORDTST01", "k")
    assert raised.value.code == "cannot_refund_order"
    with pytest.raises(PaymentProviderRejection):
        gateway(lambda r: httpx.Response(400, json=error)).cancel_order("ORDTST01", "k")


@pytest.mark.parametrize("handler", [lambda r: httpx.Response(503), lambda r: httpx.Response(429), lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("timeout"))])
def test_technical_failures(handler) -> None:  # noqa: ANN001
    with pytest.raises(PaymentProviderError):
        gateway(handler).get_order("ORDTST01")
