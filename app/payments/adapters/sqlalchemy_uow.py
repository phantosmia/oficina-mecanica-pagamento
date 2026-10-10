from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.shared.events import Envelope
from app.shared.models import Charge as ChargeORM
from app.shared.models import OutboxMessage, PaymentNotification, ProcessedMessage
from app.shared.tracing import current_trace_headers
from app.payments.domain.entities import Charge
from app.payments.domain.ports import IPaymentUnitOfWork
from app.payments.domain.value_objects import ChargeStatus

_FIELDS = [
    "saga_id", "order_id", "amount", "description", "customer_email", "provider_order_id", "checkout_url",
    "provider_status", "provider_status_detail", "created_at", "updated_at", "paid_at", "refunded_at",
]


def _to_entity(orm: ChargeORM) -> Charge:
    return Charge(id=orm.id, status=ChargeStatus(orm.status), **{f: getattr(orm, f) for f in _FIELDS})


class SqlAlchemyPaymentUnitOfWork(IPaymentUnitOfWork):
    """Uma sessão SQLAlchemy = uma transação."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None

    def __enter__(self) -> "SqlAlchemyPaymentUnitOfWork":
        self._session = self._session_factory()
        return self

    def __exit__(self, *args: object) -> None:
        super().__exit__(*args)
        self.session.close()
        self._session = None

    @property
    def session(self) -> Session:
        if self._session is None:
            raise RuntimeError("Unit of work usada fora de um bloco `with`.")
        return self._session

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()

    def _one(self, *criteria, for_update: bool = False) -> Charge | None:  # noqa: ANN002
        query = select(ChargeORM).where(*criteria)
        if for_update:
            query = query.with_for_update()
        orm = self.session.scalar(query)
        return _to_entity(orm) if orm else None

    def get_by_saga(self, saga_id: str, for_update: bool = False) -> Charge | None:
        return self._one(ChargeORM.saga_id == saga_id, for_update=for_update)

    def get_by_order(self, order_id: int) -> Charge | None:
        # Uma OS pode ter, em tese, uma cobrança por saga; a mais recente é a que vale.
        orm = self.session.scalar(select(ChargeORM).where(ChargeORM.order_id == order_id).order_by(ChargeORM.created_at.desc()).limit(1))
        return _to_entity(orm) if orm else None

    def get_by_id(self, charge_id: str, for_update: bool = False) -> Charge | None:
        return self._one(ChargeORM.id == charge_id, for_update=for_update)

    def get_by_provider_order(self, provider_order_id: str, for_update: bool = False) -> Charge | None:
        return self._one(ChargeORM.provider_order_id == provider_order_id, for_update=for_update)

    def list_awaiting(self, created_before: datetime) -> list[Charge]:
        rows = self.session.scalars(
            select(ChargeORM)
            .where(ChargeORM.status == ChargeStatus.AWAITING_PAYMENT.value, ChargeORM.created_at < created_before)
            .order_by(ChargeORM.created_at)
        )
        return [_to_entity(r) for r in rows]

    def save(self, charge: Charge) -> None:
        orm = self.session.get(ChargeORM, charge.id)
        if orm is None:
            orm = ChargeORM(id=charge.id)
            self.session.add(orm)
        orm.status = charge.status.value
        for field in _FIELDS:
            setattr(orm, field, getattr(charge, field))
        self.session.flush()

    def is_processed(self, message_id: str) -> bool:
        return self.session.get(ProcessedMessage, message_id) is not None

    def mark_processed(self, message_id: str, message_type: str) -> bool:
        # RETURNING em vez de rowcount: num INSERT pela sessão ORM, o rowcount
        # não indica de forma confiável se a linha foi inserida (SQLAlchemy 2.1).
        result = self.session.execute(
            insert(ProcessedMessage)
            .values(message_id=message_id, message_type=message_type)
            .on_conflict_do_nothing(index_elements=["message_id"])
            .returning(ProcessedMessage.message_id)
        )
        return result.scalar() is not None

    def publish(self, envelope: Envelope) -> None:
        self.session.add(
            OutboxMessage(
                message_id=envelope.message_id,
                message_type=envelope.type,
                envelope=envelope.to_dict(),
                trace_headers=current_trace_headers(),
            )
        )

    def record_notification(self, provider_order_id: str | None, request_id: str | None, signature_valid: bool | None, payload: dict) -> None:
        self.session.add(
            PaymentNotification(provider_order_id=provider_order_id, request_id=request_id, signature_valid=signature_valid, payload=payload)
        )
