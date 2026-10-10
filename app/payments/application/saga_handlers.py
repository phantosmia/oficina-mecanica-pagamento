"""Participação do Pagamento na saga da OS (docs/saga.md, no repositório
oficina-mecanica-fiap): `CriarCobranca` e as compensações `CancelarCobranca`
e `EstornarPagamento`.

Diferença em relação aos outros participantes: aqui há uma chamada a um
sistema externo (Mercado Pago) que não participa da transação do banco. Por
isso a chamada é feita **fora** da transação e é idempotente do lado do
provedor: a chave de idempotência é derivada da saga
(`cobranca-<saga_id>`). Se o serviço cair depois de criar a cobrança no
Mercado Pago e antes de gravar, o reprocessamento do comando recebe de volta
a **mesma** cobrança, não uma segunda.
"""

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from app.shared.email import IEmailNotifier, NullEmailNotifier
from app.shared.events import Envelope
from app.shared.exceptions import DomainError, PaymentProviderRejection
from app.shared.settings import settings
from app.payments.application.notifications import send_payment_link
from app.payments.domain.entities import Charge, ChargeItem, money
from app.payments.domain.ports import IPaymentGateway, IPaymentUnitOfWork
from app.payments.domain.value_objects import ChargeStatus, ProviderStatus

logger = logging.getLogger(__name__)


def _reply(command: Envelope, event_type: str, payload: dict) -> Envelope:
    return Envelope(type=event_type, payload=payload, saga_id=command.saga_id, order_id=command.order_id)


def _created_payload(charge: Charge) -> dict:
    return {
        "charge_id": charge.id,
        "provider_order_id": charge.provider_order_id,
        "checkout_url": charge.checkout_url,
        "amount": float(charge.amount),
    }


# Namespace fixo do serviço para os IDs de cobrança derivados da saga.
_CHARGE_NAMESPACE = uuid.UUID("0b6f3c1e-8f2a-4d6b-9c3e-7a1d5e2f4b80")


def charge_id_for(saga_id: str) -> str:
    """ID da cobrança derivado da saga (UUID v5), e não aleatório: ele entra
    na URL de retorno enviada ao Mercado Pago. Num reprocessamento, o provedor
    devolve a cobrança original (mesma chave de idempotência), com a URL
    original, e o ID precisa ser o mesmo para essa URL funcionar."""
    return str(uuid.uuid5(_CHARGE_NAMESPACE, saga_id))


def return_url(charge_id: str) -> str:
    return f"{settings.public_base_url.rstrip('/')}/pagamentos/{charge_id}/retorno"


def _items(payload: dict) -> list[ChargeItem]:
    entries = [*payload.get("services", []), *payload.get("parts", [])]
    return [ChargeItem(str(e["name"])[:256], int(e["quantity"]), money(e["unit_price"])) for e in entries]


def _reply_only(uow_factory: Callable[[], IPaymentUnitOfWork], command: Envelope, reply: Envelope) -> None:
    with uow_factory() as uow:
        if uow.mark_processed(command.message_id, command.type):
            uow.publish(reply)
            uow.commit()


class CreateChargeUseCase:
    """`CriarCobranca`: cria a cobrança no Mercado Pago e manda o link ao cliente."""

    def __init__(self, uow_factory: Callable[[], IPaymentUnitOfWork], gateway: IPaymentGateway, notifier: IEmailNotifier | None = None) -> None:
        self._uow_factory = uow_factory
        self._gateway = gateway
        self._notifier = notifier or NullEmailNotifier()

    def execute(self, command: Envelope) -> None:
        with self._uow_factory() as uow:
            if uow.is_processed(command.message_id):
                return
            existing = uow.get_by_saga(command.saga_id) if command.saga_id else None
        if existing is not None:
            reply = (
                _reply(command, "CobrancaFalhou", {"reason": "saga_ja_compensada"})
                if existing.provider_order_id is None and existing.status == ChargeStatus.CANCELLED
                else _reply(command, "CobrancaCriada", _created_payload(existing))
            )
            _reply_only(self._uow_factory, command, reply)
            return

        try:
            items, charge = self._build(command)
        except (DomainError, KeyError, TypeError, ValueError, ArithmeticError) as error:
            logger.warning("CriarCobranca inválido order_id=%s: %s", command.order_id, error)
            _reply_only(self._uow_factory, command, _reply(command, "CobrancaFalhou", {"reason": "payload_invalido"}))
            return

        try:
            order = self._gateway.create_order(
                idempotency_key=f"cobranca-{command.saga_id}",
                external_reference=f"os-{command.order_id}",
                amount=charge.amount,
                items=items,
                payer_email=settings.mp_payer_email_override or charge.customer_email or "",
                return_url=return_url(charge.id),
            )
        except PaymentProviderRejection as rejection:
            logger.warning("Mercado Pago recusou a cobrança da OS %s: %s", command.order_id, rejection)
            _reply_only(self._uow_factory, command, _reply(command, "CobrancaFalhou", {"reason": "provedor_recusou", "code": rejection.code}))
            return
        # PaymentProviderError (falha técnica) sobe: a mensagem volta para a fila.

        charge.provider_order_id, charge.checkout_url = order.id, order.checkout_url
        charge.provider_status, charge.provider_status_detail = order.raw_status, order.raw_status_detail
        with self._uow_factory() as uow:
            if not uow.mark_processed(command.message_id, command.type):
                return
            uow.save(charge)
            uow.publish(_reply(command, "CobrancaCriada", _created_payload(charge)))
            uow.commit()
        send_payment_link(self._notifier, charge)

    @staticmethod
    def _build(command: Envelope) -> tuple[list[ChargeItem], Charge]:
        if command.order_id is None or not command.saga_id:
            raise DomainError("Comando sem order_id ou saga_id.")
        items = _items(command.payload)
        if not items:
            raise DomainError("Cobrança sem itens.")
        # Valor recalculado a partir dos itens: o Mercado Pago exige que o
        # total seja exatamente a soma, e não aceitamos um total pronto.
        amount = money(sum(i.subtotal for i in items))
        customer = command.payload.get("customer") or {}
        charge = Charge(
            id=charge_id_for(command.saga_id),
            saga_id=command.saga_id,
            order_id=command.order_id,
            amount=amount,
            description=f"Ordem de serviço #{command.order_id}",
            customer_email=customer.get("email"),
            status=ChargeStatus.AWAITING_PAYMENT,
            created_at=datetime.now(UTC),
        )
        return items, charge


class _UndoChargeUseCase:
    """Base das compensações: desfaz a cobrança de acordo com o estado **real**
    no provedor. Se ainda não foi paga, cancela; se foi paga (inclusive se o
    pagamento entrou agora, no meio da compensação), estorna. Sempre confirma,
    mesmo sem nada a desfazer (docs/saga.md)."""

    reply_type = ""

    def __init__(self, uow_factory: Callable[[], IPaymentUnitOfWork], gateway: IPaymentGateway) -> None:
        self._uow_factory = uow_factory
        self._gateway = gateway

    def execute(self, command: Envelope) -> None:
        with self._uow_factory() as uow:
            if uow.is_processed(command.message_id):
                return
            charge = uow.get_by_saga(command.saga_id) if command.saga_id else None

        if charge is None:
            self._save_tombstone(command)
            return

        refunded = False
        if charge.status in (ChargeStatus.AWAITING_PAYMENT, ChargeStatus.PAID) and charge.provider_order_id:
            refunded = self._undo_at_provider(charge)

        with self._uow_factory() as uow:
            if not uow.mark_processed(command.message_id, command.type):
                return
            locked = uow.get_by_id(charge.id, for_update=True)
            now = datetime.now(UTC)
            if refunded:
                locked.mark_refunded(now)
            elif locked.status == ChargeStatus.AWAITING_PAYMENT:
                locked.mark_cancelled(now)
            uow.save(locked)
            uow.publish(_reply(command, self.reply_type, {"charge_id": locked.id, "refunded": locked.status == ChargeStatus.REFUNDED}))
            uow.commit()

    def _undo_at_provider(self, charge: Charge) -> bool:
        """Retorna True se houve estorno."""
        order = self._gateway.get_order(charge.provider_order_id)
        if order is None or order.status in (ProviderStatus.CANCELLED, ProviderStatus.EXPIRED, ProviderStatus.FAILED):
            return False
        if order.status == ProviderStatus.REFUNDED:
            return True
        if order.status == ProviderStatus.PAID:
            self._gateway.refund_order(charge.provider_order_id, f"estorno-{charge.saga_id}")
            return True
        try:
            self._gateway.cancel_order(charge.provider_order_id, f"cancelamento-{charge.saga_id}")
            return False
        except PaymentProviderRejection:
            # O pagamento pode ter entrado entre a consulta e o cancelamento.
            latest = self._gateway.get_order(charge.provider_order_id)
            if latest is not None and latest.status == ProviderStatus.PAID:
                self._gateway.refund_order(charge.provider_order_id, f"estorno-{charge.saga_id}")
                return True
            raise

    def _save_tombstone(self, command: Envelope) -> None:
        """Compensação chegou antes da cobrança existir (fila sem ordem
        garantida): registra a saga como compensada, para um `CriarCobranca`
        atrasado ser recusado em vez de cobrar o cliente."""
        with self._uow_factory() as uow:
            if not uow.mark_processed(command.message_id, command.type):
                return
            if command.saga_id and command.order_id is not None and uow.get_by_saga(command.saga_id) is None:
                uow.save(
                    Charge(
                        id=charge_id_for(command.saga_id),
                        saga_id=command.saga_id,
                        order_id=command.order_id,
                        amount=money(0),
                        description="(saga compensada antes da cobrança)",
                        customer_email=None,
                        status=ChargeStatus.CANCELLED,
                        created_at=datetime.now(UTC),
                    )
                )
            uow.publish(_reply(command, self.reply_type, {"charge_id": None, "refunded": False}))
            uow.commit()


class CancelChargeUseCase(_UndoChargeUseCase):
    """`CancelarCobranca`: cobrança criada e não paga (pagamento recusado ou expirado)."""

    reply_type = "CobrancaCancelada"


class RefundPaymentUseCase(_UndoChargeUseCase):
    """`EstornarPagamento`: falha depois do pagamento (baixa ou enfileiramento do reparo)."""

    reply_type = "PagamentoEstornado"
