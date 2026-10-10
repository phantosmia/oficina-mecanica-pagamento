import logging
import smtplib
from email.mime.text import MIMEText

from app.shared.email import IEmailNotifier
from app.shared.settings import settings

logger = logging.getLogger(__name__)


class SmtpEmailNotifier(IEmailNotifier):
    """Adapter SMTP — implementa IEmailNotifier usando smtplib."""

    def send(self, *, to: str, subject: str, body: str) -> None:
        if not settings.smtp_enabled:
            logger.debug("SMTP desabilitado. E-mail não enviado para %s: %s", to, subject)
            return

        if not to:
            logger.warning("Destinatário vazio, e-mail ignorado: %s", subject)
            return

        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = settings.smtp_from
        msg["To"] = to

        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
                server.ehlo()
                if settings.smtp_username:
                    server.starttls()
                    server.login(settings.smtp_username, settings.smtp_password)
                server.sendmail(settings.smtp_from, [to], msg.as_string())
            logger.info("E-mail enviado para %s: %s", to, subject)
        except Exception:
            logger.exception("Falha ao enviar e-mail para %s: %s", to, subject)
