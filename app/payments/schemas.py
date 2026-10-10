from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.payments.domain.value_objects import ChargeStatus


class ChargeRead(BaseModel):
    id: str
    saga_id: str
    order_id: int
    amount: Decimal
    status: ChargeStatus
    customer_email: str | None
    provider_order_id: str | None
    checkout_url: str | None
    provider_status: str | None
    provider_status_detail: str | None
    created_at: datetime
    updated_at: datetime | None
    paid_at: datetime | None
    refunded_at: datetime | None


class WebhookAck(BaseModel):
    received: bool
