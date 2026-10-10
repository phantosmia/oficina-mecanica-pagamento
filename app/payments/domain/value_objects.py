from enum import StrEnum


class ChargeStatus(StrEnum):
    AWAITING_PAYMENT = "aguardando_pagamento"
    PAID = "pago"
    DECLINED = "recusado"      # expirou ou falhou no provedor sem pagamento
    CANCELLED = "cancelado"    # compensação antes do pagamento
    REFUNDED = "estornado"     # compensação depois do pagamento


class ProviderStatus(StrEnum):
    """Situação de uma cobrança no provedor, já traduzida para termos do
    domínio (o adapter do Mercado Pago faz o mapeamento dos status dele)."""

    PENDING = "pendente"
    PAID = "pago"
    FAILED = "falhou"
    EXPIRED = "expirado"
    CANCELLED = "cancelado"
    REFUNDED = "estornado"
    UNKNOWN = "desconhecido"
