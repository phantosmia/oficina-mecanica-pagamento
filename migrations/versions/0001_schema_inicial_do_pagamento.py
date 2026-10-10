"""schema inicial do pagamento

Revision ID: 0001
Revises: 
Create Date: 2026-10-10 10:22:18.159416
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('charges',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('saga_id', sa.String(), nullable=False),
    sa.Column('order_id', sa.Integer(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('description', sa.String(), nullable=False),
    sa.Column('customer_email', sa.String(), nullable=True),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('provider_order_id', sa.String(), nullable=True),
    sa.Column('checkout_url', sa.String(), nullable=True),
    sa.Column('provider_status', sa.String(), nullable=True),
    sa.Column('provider_status_detail', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('refunded_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider_order_id'),
    sa.UniqueConstraint('saga_id')
    )
    op.create_index('idx_charges_pending', 'charges', ['status', 'created_at'], unique=False, postgresql_where="status = 'aguardando_pagamento'")
    op.create_index(op.f('ix_charges_order_id'), 'charges', ['order_id'], unique=False)
    op.create_table('outbox_messages',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('message_id', sa.String(), nullable=False),
    sa.Column('message_type', sa.String(), nullable=False),
    sa.Column('envelope', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('trace_headers', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('message_id')
    )
    op.create_index('idx_outbox_messages_pending', 'outbox_messages', ['id'], unique=False, postgresql_where='published_at IS NULL')
    op.create_table('payment_notifications',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('provider_order_id', sa.String(), nullable=True),
    sa.Column('request_id', sa.String(), nullable=True),
    sa.Column('signature_valid', sa.Boolean(), nullable=True),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_payment_notifications_provider_order_id'), 'payment_notifications', ['provider_order_id'], unique=False)
    op.create_table('processed_messages',
    sa.Column('message_id', sa.String(), nullable=False),
    sa.Column('message_type', sa.String(), nullable=False),
    sa.Column('processed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('message_id')
    )


def downgrade() -> None:
    op.drop_table('processed_messages')
    op.drop_index(op.f('ix_payment_notifications_provider_order_id'), table_name='payment_notifications')
    op.drop_table('payment_notifications')
    op.drop_index('idx_outbox_messages_pending', table_name='outbox_messages', postgresql_where='published_at IS NULL')
    op.drop_table('outbox_messages')
    op.drop_index(op.f('ix_charges_order_id'), table_name='charges')
    op.drop_index('idx_charges_pending', table_name='charges', postgresql_where="status = 'aguardando_pagamento'")
    op.drop_table('charges')
