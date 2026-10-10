from abc import ABC, abstractmethod
from decimal import Decimal


class IEmailNotifier(ABC):
    """Port — define o contrato de envio de notificações por e-mail."""

    @abstractmethod
    def send(self, *, to: str, subject: str, body: str) -> None: ...


class NullEmailNotifier(IEmailNotifier):
    """Implementação no-op usada em testes e quando SMTP está desabilitado."""

    def send(self, *, to: str, subject: str, body: str) -> None:
        pass


def payment_link_message(order_id: int, amount: Decimal, checkout_url: str) -> tuple[str, str]:
    subject = f"[OS #{order_id}] Link de pagamento"
    body = (
        "Olá!\n\n"
        f"Recebemos a aprovação do orçamento da Ordem de Serviço #{order_id}.\n"
        f"Valor: R$ {amount:.2f}\n\n"
        f"Para pagar (cartão, Pix ou boleto), acesse:\n{checkout_url}\n\n"
        "Assim que o pagamento for confirmado, o serviço entra na fila de reparo.\n\n"
        "Atenciosamente,\nOficina Mecânica FIAP"
    )
    return subject, body


def payment_confirmed_message(order_id: int, amount: Decimal) -> tuple[str, str]:
    subject = f"[OS #{order_id}] Pagamento confirmado"
    body = (
        "Olá!\n\n"
        f"Confirmamos o pagamento de R$ {amount:.2f} da Ordem de Serviço #{order_id}.\n"
        "O serviço entrou na fila de reparo.\n\n"
        "Atenciosamente,\nOficina Mecânica FIAP"
    )
    return subject, body
