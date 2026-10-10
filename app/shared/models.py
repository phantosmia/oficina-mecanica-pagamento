from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Index, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Charge(Base):
    """Cobrança de uma saga (uma por saga). Valores em `Numeric`, nunca float."""

    __tablename__ = "charges"
    __table_args__ = (
        Index("idx_charges_pending", "status", "created_at", postgresql_where="status = 'aguardando_pagamento'"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    saga_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    order_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False)
    customer_email: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    # Mercado Pago. UNIQUE: duas cobranças nunca apontam para a mesma order.
    provider_order_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    checkout_url: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_status: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_status_detail: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PaymentNotification(Base):
    """Toda notificação recebida do Mercado Pago, como chegou (auditoria)."""

    __tablename__ = "payment_notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_order_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    request_id: Mapped[str | None] = mapped_column(String, nullable=True)
    signature_valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ProcessedMessage(Base):
    """Idempotência (docs/saga.md)."""

    __tablename__ = "processed_messages"

    message_id: Mapped[str] = mapped_column(String, primary_key=True)
    message_type: Mapped[str] = mapped_column(String, nullable=False)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class OutboxMessage(Base):
    """*Transactional outbox* (RFC-0007)."""

    __tablename__ = "outbox_messages"
    __table_args__ = (Index("idx_outbox_messages_pending", "id", postgresql_where="published_at IS NULL"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    message_type: Mapped[str] = mapped_column(String, nullable=False)
    envelope: Mapped[dict] = mapped_column(JSONB, nullable=False)
    trace_headers: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
