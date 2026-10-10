"""Confirmação do pagamento: consulta o provedor e aplica a regra de
`Charge.apply_provider_state`.

Três caminhos chegam aqui, e todos tratam o aviso como um simples "vá
olhar", nunca como prova de pagamento:

1. webhook do Mercado Pago (`POST /webhooks/mercado-pago`);
2. retorno do comprador depois do checkout (`/pagamentos/{charge_id}/retorno`);
3. reconciliação periódica no worker, que também pega cobranças expiradas e
   funciona sem nenhuma URL pública (AWS Academy Lab, ambiente local).
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.shared.email import IEmailNotifier, NullEmailNotifier
from app.shared.events import Envelope
from app.payments.application.notifications import send_payment_confirmed
from app.payments.domain.entities import Charge
from app.payments.domain.ports import IPaymentGateway, IPaymentUnitOfWork

logger = logging.getLogger(__name__)


def charge_event(charge: Charge, event_type: str) -> Envelope:
    payload = {
        "charge_id": charge.id,
        "provider_order_id": charge.provider_order_id,
        "amount": float(charge.amount),
    }
    if event_type == "PagamentoRecusado":
        payload |= {"provider_status": charge.provider_status, "provider_status_detail": charge.provider_status_detail}
    return Envelope(type=event_type, payload=payload, saga_id=charge.saga_id, order_id=charge.order_id)


class SyncChargeUseCase:
    def __init__(self, uow_factory: Callable[[], IPaymentUnitOfWork], gateway: IPaymentGateway, notifier: IEmailNotifier | None = None) -> None:
        self._uow_factory = uow_factory
        self._gateway = gateway
        self._notifier = notifier or NullEmailNotifier()

    def by_charge_id(self, charge_id: str) -> Charge | None:
        with self._uow_factory() as uow:
            charge = uow.get_by_id(charge_id)
        return self._sync(charge) if charge else None

    def by_order_id(self, order_id: int) -> Charge | None:
        with self._uow_factory() as uow:
            charge = uow.get_by_order(order_id)
        return self._sync(charge) if charge else None

    def by_provider_order(self, provider_order_id: str) -> Charge | None:
        with self._uow_factory() as uow:
            charge = uow.get_by_provider_order(provider_order_id)
        return self._sync(charge) if charge else None

    def _sync(self, charge: Charge) -> Charge:
        if not charge.provider_order_id:
            return charge
        # Consulta externa fora da transação: não segura lock no banco
        # enquanto espera o Mercado Pago.
        order = self._gateway.get_order(charge.provider_order_id)
        if order is None:
            logger.warning("cobrança %s não encontrada no provedor (%s)", charge.id, charge.provider_order_id)
            return charge
        with self._uow_factory() as uow:
            locked = uow.get_by_id(charge.id, for_update=True)
            event_type = locked.apply_provider_state(order, datetime.now(UTC))
            uow.save(locked)
            if event_type:
                uow.publish(charge_event(locked, event_type))
            uow.commit()
        if event_type:
            logger.info("cobrança %s: %s (provedor: %s)", locked.id, event_type, order.raw_status)
            if event_type == "PagamentoConfirmado":
                send_payment_confirmed(self._notifier, locked)
        return locked


class ReconcilePendingChargesUseCase:
    """Rede de segurança: consulta no provedor as cobranças ainda pendentes."""

    def __init__(self, uow_factory: Callable[[], IPaymentUnitOfWork], sync: SyncChargeUseCase, min_age_seconds: float = 10) -> None:
        self._uow_factory = uow_factory
        self._sync = sync
        self._min_age = timedelta(seconds=min_age_seconds)

    def execute(self) -> int:
        with self._uow_factory() as uow:
            pending = uow.list_awaiting(created_before=datetime.now(UTC) - self._min_age)
        changed = 0
        for charge in pending:
            try:
                if self._sync.by_charge_id(charge.id).status != charge.status:
                    changed += 1
            except Exception:
                logger.exception("falha ao reconciliar a cobrança %s", charge.id)
        return changed
