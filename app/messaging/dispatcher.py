"""Roteia cada mensagem recebida para o caso de uso correspondente."""

import json
import logging
from collections.abc import Callable
from typing import Any

from app.shared.email import IEmailNotifier
from app.shared.events import Envelope
from app.payments.application.saga_handlers import CancelChargeUseCase, CreateChargeUseCase, RefundPaymentUseCase
from app.payments.domain.ports import IPaymentGateway, IPaymentUnitOfWork

logger = logging.getLogger(__name__)


def parse_body(body: str) -> Envelope:
    """Corpo da mensagem SQS → envelope (aceita também o embrulho do SNS
    quando `RawMessageDelivery` está desligado)."""
    data: dict[str, Any] = json.loads(body)
    if data.get("Type") == "Notification" and "Message" in data:
        data = json.loads(data["Message"])
    return Envelope(
        type=data["type"],
        payload=data.get("payload") or {},
        saga_id=data.get("saga_id"),
        order_id=data.get("order_id"),
        message_id=data["message_id"],
        occurred_at=data["occurred_at"],
    )


class Dispatcher:
    def __init__(self, uow_factory: Callable[[], IPaymentUnitOfWork], gateway: IPaymentGateway, notifier: IEmailNotifier) -> None:
        # Comandos do orquestrador (fila pagamento-comandos)
        self._handlers: dict[str, Callable[[], Any]] = {
            "CriarCobranca": lambda: CreateChargeUseCase(uow_factory, gateway, notifier),
            "CancelarCobranca": lambda: CancelChargeUseCase(uow_factory, gateway),
            "EstornarPagamento": lambda: RefundPaymentUseCase(uow_factory, gateway),
        }

    def handle(self, envelope: Envelope) -> bool:
        factory = self._handlers.get(envelope.type)
        if factory is None:
            logger.warning("mensagem de tipo desconhecido ignorada type=%s message_id=%s", envelope.type, envelope.message_id)
            return False
        factory().execute(envelope)
        logger.info(
            "mensagem processada type=%s message_id=%s saga_id=%s order_id=%s",
            envelope.type, envelope.message_id, envelope.saga_id, envelope.order_id,
        )
        return True
