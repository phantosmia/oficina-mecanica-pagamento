"""Relay da outbox (RFC-0007): publica no SNS as mensagens que os casos de
uso gravaram em `outbox_messages`, na mesma transação da mudança de estado.

Entrega *at-least-once*: se o processo cair entre publicar e marcar como
publicada, a mensagem sai de novo; os consumidores descartam repetições pelo
`message_id`.
"""

import logging
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.shared.models import OutboxMessage

logger = logging.getLogger(__name__)


class EventPublisher(Protocol):
    def publish(self, envelope: dict[str, Any], trace_headers: dict[str, str]) -> None: ...


class OutboxRelay:
    def __init__(self, session_factory: sessionmaker[Session], publisher: EventPublisher, batch_size: int = 50) -> None:
        self._session_factory = session_factory
        self._publisher = publisher
        self._batch_size = batch_size

    def run_once(self) -> int:
        """Publica um lote de pendentes, em ordem de gravação. Retorna quantas publicou."""
        with self._session_factory() as session:
            pending = session.scalars(
                select(OutboxMessage)
                .where(OutboxMessage.published_at.is_(None))
                .order_by(OutboxMessage.id)
                .limit(self._batch_size)
                # Se um dia houver mais de um relay, cada um pega linhas diferentes.
                .with_for_update(skip_locked=True)
            ).all()
            for message in pending:
                self._publisher.publish(message.envelope, dict(message.trace_headers or {}))
                message.published_at = datetime.now(UTC)
                session.flush()
                logger.info("evento publicado type=%s message_id=%s", message.message_type, message.message_id)
            session.commit()
            return len(pending)
