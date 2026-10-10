from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.shared.exceptions import DomainError
from app.payments.domain.entities import Charge, ChargeItem, ProviderOrder, money
from app.payments.domain.value_objects import ChargeStatus, ProviderStatus

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def charge(status: ChargeStatus = ChargeStatus.AWAITING_PAYMENT) -> Charge:
    return Charge("c", "s", 1, money(100), "OS #1", None, status, NOW, provider_order_id="ORD1")


def order(status: ProviderStatus) -> ProviderOrder:
    return ProviderOrder("ORD1", status, status.value, "detalhe")


def test_money_avoids_float_errors() -> None:
    assert money(0.1) + money(0.2) == Decimal("0.30")
    assert ChargeItem("Óleo", 3, money(45.1)).subtotal == Decimal("135.30")


def test_items_must_be_positive() -> None:
    with pytest.raises(DomainError):
        ChargeItem("x", 0, money(1))
    with pytest.raises(DomainError):
        ChargeItem("x", 1, money(0))


@pytest.mark.parametrize(
    ("provider", "expected_event", "expected_status"),
    [
        (ProviderStatus.PAID, "PagamentoConfirmado", ChargeStatus.PAID),
        (ProviderStatus.EXPIRED, "PagamentoRecusado", ChargeStatus.DECLINED),
        (ProviderStatus.FAILED, "PagamentoRecusado", ChargeStatus.DECLINED),
        (ProviderStatus.CANCELLED, "PagamentoRecusado", ChargeStatus.DECLINED),
        (ProviderStatus.PENDING, None, ChargeStatus.AWAITING_PAYMENT),
        (ProviderStatus.UNKNOWN, None, ChargeStatus.AWAITING_PAYMENT),
    ],
)
def test_provider_state_on_pending_charge(provider: ProviderStatus, expected_event: str | None, expected_status: ChargeStatus) -> None:
    c = charge()
    assert c.apply_provider_state(order(provider), NOW) == expected_event
    assert c.status == expected_status
    assert c.provider_status == provider.value


@pytest.mark.parametrize("status", [ChargeStatus.PAID, ChargeStatus.CANCELLED, ChargeStatus.REFUNDED, ChargeStatus.DECLINED])
def test_settled_charge_never_emits_again(status: ChargeStatus) -> None:
    c = charge(status)
    assert c.apply_provider_state(order(ProviderStatus.PAID), NOW) is None
    assert c.status == status
