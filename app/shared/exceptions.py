class DomainError(Exception):
    """Base class for all domain errors."""


class NotFoundError(DomainError):
    """Raised when an entity is not found."""

    def __init__(self, entity: str, identifier: object) -> None:
        super().__init__(f"{entity} não encontrado(a).")
        self.entity = entity
        self.identifier = identifier


class InvalidTransitionError(DomainError):
    """Raised when an invalid status transition is attempted."""


class ConcurrencyError(DomainError):
    """Raised when the record changed between reading and saving (another request won)."""


class PaymentProviderError(Exception):
    """Falha técnica ao falar com o provedor de pagamento (timeout, 5xx).
    Não é erro de domínio: a mensagem volta para a fila e é tentada de novo."""


class PaymentProviderRejection(Exception):
    """O provedor recusou a operação por regra de negócio (4xx com código de
    erro). Vira evento de falha na saga, não nova tentativa."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
