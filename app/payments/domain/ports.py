from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal
from types import TracebackType
from typing import Self

from app.shared.events import Envelope
from app.payments.domain.entities import Charge, ChargeItem, ProviderOrder


class IPaymentGateway(ABC):
    """Provedor de pagamento (hoje, Mercado Pago). Levanta
    `PaymentProviderError` em falha técnica e `PaymentProviderRejection`
    quando o provedor recusa por regra de negócio."""

    @abstractmethod
    def create_order(
        self,
        *,
        idempotency_key: str,
        external_reference: str,
        amount: Decimal,
        items: list[ChargeItem],
        payer_email: str,
        return_url: str,
    ) -> ProviderOrder: ...

    @abstractmethod
    def get_order(self, provider_order_id: str) -> ProviderOrder | None:
        """None se o provedor não conhece a cobrança."""
        ...

    @abstractmethod
    def cancel_order(self, provider_order_id: str, idempotency_key: str) -> ProviderOrder: ...

    @abstractmethod
    def refund_order(self, provider_order_id: str, idempotency_key: str) -> ProviderOrder: ...


class IPaymentUnitOfWork(ABC):
    """Tudo o que um caso de uso grava só é confirmado junto, em `commit()`."""

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None) -> None:
        self.rollback()

    @abstractmethod
    def commit(self) -> None: ...

    @abstractmethod
    def rollback(self) -> None: ...

    @abstractmethod
    def get_by_saga(self, saga_id: str, for_update: bool = False) -> Charge | None: ...

    @abstractmethod
    def get_by_order(self, order_id: int) -> Charge | None: ...

    @abstractmethod
    def get_by_id(self, charge_id: str, for_update: bool = False) -> Charge | None: ...

    @abstractmethod
    def get_by_provider_order(self, provider_order_id: str, for_update: bool = False) -> Charge | None: ...

    @abstractmethod
    def list_awaiting(self, created_before: datetime) -> list[Charge]: ...

    @abstractmethod
    def save(self, charge: Charge) -> None: ...

    @abstractmethod
    def is_processed(self, message_id: str) -> bool: ...

    @abstractmethod
    def mark_processed(self, message_id: str, message_type: str) -> bool:
        """False se a mensagem já tinha sido processada."""
        ...

    @abstractmethod
    def publish(self, envelope: Envelope) -> None: ...

    @abstractmethod
    def record_notification(self, provider_order_id: str | None, request_id: str | None, signature_valid: bool | None, payload: dict) -> None: ...
