"""Processo do Pagamento que não atende HTTP (`python -m app.worker`):

- consome a fila de comandos da saga (`pagamento-comandos`);
- reconcilia periodicamente as cobranças pendentes com o Mercado Pago (rede
  de segurança para webhook perdido, ambiente sem URL pública e cobranças
  que expiram sem pagamento).
"""

import logging
import threading
import time

from app.messaging.dispatcher import Dispatcher
from app.messaging.sqs_consumer import SqsConsumer
from app.shared.database import get_session_factory
from app.shared.logging_config import configure_logging
from app.shared.settings import settings
from app.shared.smtp_notifier import SmtpEmailNotifier
from app.payments.adapters.mercado_pago_gateway import MercadoPagoGateway
from app.payments.adapters.sqlalchemy_uow import SqlAlchemyPaymentUnitOfWork
from app.payments.application.sync import ReconcilePendingChargesUseCase, SyncChargeUseCase

logger = logging.getLogger("app.worker")


def _reconcile_forever(reconcile: ReconcilePendingChargesUseCase) -> None:  # pragma: no cover
    while True:
        try:
            changed = reconcile.execute()
            if changed:
                logger.info("reconciliação: %s cobranças mudaram de status", changed)
        except Exception:
            logger.exception("falha na reconciliação; tentando de novo")
        time.sleep(settings.reconcile_interval_seconds)


def main() -> None:  # pragma: no cover - laços infinitos; a lógica testada fica nos casos de uso
    configure_logging(settings.log_level)

    def uow_factory() -> SqlAlchemyPaymentUnitOfWork:
        return SqlAlchemyPaymentUnitOfWork(get_session_factory())

    gateway = MercadoPagoGateway(settings.mp_access_token, settings.mp_base_url, settings.mp_timeout_seconds, settings.payment_expiration)
    notifier = SmtpEmailNotifier()
    reconcile = ReconcilePendingChargesUseCase(uow_factory, SyncChargeUseCase(uow_factory, gateway, notifier))
    threading.Thread(target=_reconcile_forever, args=(reconcile,), name="reconciliacao", daemon=True).start()

    consumer = SqsConsumer([settings.commands_queue_url], Dispatcher(uow_factory, gateway, notifier), settings.worker_wait_seconds)
    logger.info("worker iniciado fila=%s reconciliação a cada %ss", settings.commands_queue_url, settings.reconcile_interval_seconds)
    while True:
        try:
            consumer.poll_queue(settings.commands_queue_url)
        except Exception:
            logger.exception("falha ao ler a fila; tentando de novo")


if __name__ == "__main__":  # pragma: no cover
    main()
