from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.shared.exceptions import DomainError
from app.payments.domain.value_objects import ChargeStatus, ProviderStatus


def money(value: object) -> Decimal:
    """Valor monetário com 2 casas. `str()` antes de `Decimal` para não herdar
    o erro binário de um float (0.1 → 0.1000000000000000055...)."""
    return Decimal(str(value)).quantize(Decimal("0.01"))


@dataclass(frozen=True)
class ChargeItem:
    title: str
    quantity: int
    unit_price: Decimal

    def __post_init__(self) -> None:
        if self.quantity <= 0 or self.unit_price <= 0:
            raise DomainError(f"Item inválido na cobrança: {self.title}.")

    @property
    def subtotal(self) -> Decimal:
        return money(self.unit_price * self.quantity)


@dataclass(frozen=True)
class ProviderOrder:
    """Uma cobrança como o provedor a vê."""

    id: str
    status: ProviderStatus
    raw_status: str
    raw_status_detail: str | None = None
    checkout_url: str | None = None


@dataclass
class Charge:
    id: str
    saga_id: str
    order_id: int
    amount: Decimal
    description: str
    customer_email: str | None
    status: ChargeStatus
    created_at: datetime
    provider_order_id: str | None = None
    checkout_url: str | None = None
    provider_status: str | None = None
    provider_status_detail: str | None = None
    updated_at: datetime | None = None
    paid_at: datetime | None = None
    refunded_at: datetime | None = None

    def apply_provider_state(self, order: ProviderOrder, now: datetime) -> str | None:
        """Atualiza a cobrança com o que o provedor informa. Retorna o tipo do
        evento a publicar para a saga, ou None se nada mudou para ela.

        É a única regra de confirmação do sistema: webhook, retorno do
        comprador, reconciliação e consulta do admin passam todos por aqui,
        sempre com o estado obtido **consultando o provedor**."""
        self.provider_status, self.provider_status_detail = order.raw_status, order.raw_status_detail
        self.updated_at = now
        if self.status != ChargeStatus.AWAITING_PAYMENT:
            return None
        if order.status == ProviderStatus.PAID:
            self.status, self.paid_at = ChargeStatus.PAID, now
            return "PagamentoConfirmado"
        if order.status in (ProviderStatus.FAILED, ProviderStatus.EXPIRED, ProviderStatus.CANCELLED):
            self.status = ChargeStatus.DECLINED
            return "PagamentoRecusado"
        return None

    def mark_cancelled(self, now: datetime) -> None:
        self.status, self.updated_at = ChargeStatus.CANCELLED, now

    def mark_refunded(self, now: datetime) -> None:
        self.status, self.refunded_at, self.updated_at = ChargeStatus.REFUNDED, now, now
