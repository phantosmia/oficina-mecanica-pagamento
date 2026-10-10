"""Casos de uso da API (webhook e admin)."""

import logging
from collections.abc import Callable

from app.shared.exceptions import NotFoundError
from app.payments.application.sync import SyncChargeUseCase
from app.payments.domain.entities import Charge
from app.payments.domain.ports import IPaymentUnitOfWork

logger = logging.getLogger(__name__)


class GetChargeByOrderUseCase:
    def __init__(self, uow_factory: Callable[[], IPaymentUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def execute(self, order_id: int) -> Charge:
        with self._uow_factory() as uow:
            charge = uow.get_by_order(order_id)
        if charge is None:
            raise NotFoundError("Cobrança", order_id)
        return charge


class ReceiveWebhookUseCase:
    """Registra a notificação (sempre, para auditoria) e, se ela é de uma
    cobrança nossa, sincroniza com o provedor.

    A notificação nunca é usada como prova de pagamento: o status vem da
    consulta ao Mercado Pago em `SyncChargeUseCase`. Por isso, mesmo sem a
    chave de assinatura configurada, uma notificação forjada só consegue
    provocar uma consulta, nunca confirmar um pagamento que não existe."""

    def __init__(self, uow_factory: Callable[[], IPaymentUnitOfWork], sync: SyncChargeUseCase) -> None:
        self._uow_factory = uow_factory
        self._sync = sync

    def execute(self, provider_order_id: str | None, request_id: str | None, signature_valid: bool | None, payload: dict) -> Charge | None:
        with self._uow_factory() as uow:
            uow.record_notification(provider_order_id, request_id, signature_valid, payload)
            uow.commit()
        if signature_valid is False or not provider_order_id:
            return None
        charge = self._sync.by_provider_order(provider_order_id)
        if charge is None:
            logger.info("notificação de order desconhecida ignorada: %s", provider_order_id)
        return charge
