from dataclasses import dataclass
from urllib.parse import quote
import os


def _build_default_database_url() -> str:
    """URL do PostgreSQL a partir de POSTGRES_* (mesma regra dos outros
    serviços: usuário e senha passam por `quote()`). `DATABASE_URL`, se
    definida, tem precedência."""
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    user = quote(os.getenv("POSTGRES_USER", "pagamento"), safe="")
    password = quote(os.getenv("POSTGRES_PASSWORD", "pagamento"), safe="")
    db = os.getenv("POSTGRES_DB", "oficina_pagamento")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}"


@dataclass(frozen=True)
class Settings:
    app_name: str
    log_level: str
    database_url: str
    # JWT de admin: emitido pelo OS Service; este serviço só valida.
    jwt_secret_key: str
    jwt_algorithm: str
    admin_username: str
    # AWS (localmente, AWS_ENDPOINT_URL aponta pro LocalStack)
    aws_region: str
    events_topic_arn: str
    commands_queue_url: str
    worker_wait_seconds: int
    outbox_poll_interval_seconds: float
    # Mercado Pago (Checkout Pro via API de Orders)
    mp_access_token: str
    mp_base_url: str
    mp_timeout_seconds: float
    mp_webhook_secret: str           # chave do painel (Webhooks); vazia = assinatura não validada
    mp_payer_email_override: str     # sandbox: o pagador precisa terminar em @testuser.com
    payment_expiration: str          # duração ISO 8601 da cobrança no Mercado Pago
    reconcile_interval_seconds: float
    # Base pública deste serviço (URL de retorno do comprador)
    public_base_url: str
    # SMTP / e-mail (mesmas variáveis dos outros serviços)
    smtp_enabled: bool
    smtp_host: str
    smtp_port: int
    smtp_from: str
    smtp_username: str
    smtp_password: str


settings = Settings(
    app_name="Oficina Mecânica — Pagamento",
    log_level=os.getenv("LOG_LEVEL", "INFO"),
    database_url=os.getenv("DATABASE_URL", _build_default_database_url()),
    jwt_secret_key=os.getenv("JWT_SECRET_KEY", "change-me-in-production"),
    jwt_algorithm=os.getenv("JWT_ALGORITHM", "HS256"),
    admin_username=os.getenv("ADMIN_USERNAME", "admin"),
    aws_region=os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1")),
    events_topic_arn=os.getenv("PAGAMENTO_EVENTS_TOPIC_ARN", ""),
    commands_queue_url=os.getenv("PAGAMENTO_COMMANDS_QUEUE_URL", ""),
    worker_wait_seconds=int(os.getenv("WORKER_WAIT_SECONDS", "10")),
    outbox_poll_interval_seconds=float(os.getenv("OUTBOX_POLL_INTERVAL_SECONDS", "2")),
    mp_access_token=os.getenv("MERCADO_PAGO_ACCESS_TOKEN", ""),
    mp_base_url=os.getenv("MERCADO_PAGO_BASE_URL", "https://api.mercadopago.com"),
    mp_timeout_seconds=float(os.getenv("MERCADO_PAGO_TIMEOUT_SECONDS", "10")),
    mp_webhook_secret=os.getenv("MERCADO_PAGO_WEBHOOK_SECRET", ""),
    mp_payer_email_override=os.getenv("MERCADO_PAGO_PAYER_EMAIL_OVERRIDE", ""),
    payment_expiration=os.getenv("PAYMENT_EXPIRATION", "P3D"),
    reconcile_interval_seconds=float(os.getenv("RECONCILE_INTERVAL_SECONDS", "30")),
    public_base_url=os.getenv("PUBLIC_BASE_URL", "http://localhost:8005"),
    smtp_enabled=os.getenv("SMTP_ENABLED", "false").lower() == "true",
    smtp_host=os.getenv("SMTP_HOST", "localhost"),
    smtp_port=int(os.getenv("SMTP_PORT", "587")),
    smtp_from=os.getenv("SMTP_FROM", "noreply@oficina.local"),
    smtp_username=os.getenv("SMTP_USERNAME", ""),
    smtp_password=os.getenv("SMTP_PASSWORD", ""),
)
