import logging

from app.shared.email import IEmailNotifier, payment_confirmed_message, payment_link_message
from app.payments.domain.entities import Charge

logger = logging.getLogger(__name__)


def _send(notifier: IEmailNotifier, charge: Charge, subject: str, body: str) -> None:
    """Tentativa única, depois do commit: falha de e-mail não desfaz a
    cobrança nem trava a saga (o link fica em `GET /charges/{order_id}`)."""
    if not charge.customer_email:
        return
    try:
        notifier.send(to=charge.customer_email, subject=subject, body=body)
    except Exception:
        logger.exception("falha ao enviar e-mail da cobrança da OS %s", charge.order_id)


def send_payment_link(notifier: IEmailNotifier, charge: Charge) -> None:
    if charge.checkout_url:
        _send(notifier, charge, *payment_link_message(charge.order_id, charge.amount, charge.checkout_url))


def send_payment_confirmed(notifier: IEmailNotifier, charge: Charge) -> None:
    _send(notifier, charge, *payment_confirmed_message(charge.order_id, charge.amount))
