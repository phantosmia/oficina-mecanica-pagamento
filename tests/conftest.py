import os

# Banco: só usa um existente via TEST_DATABASE_URL explícita (os testes apagam
# os dados a cada caso); sem ela, Testcontainers sobe um PostgreSQL descartável.
_pg_container = None
if os.environ.get("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
else:
    from testcontainers.postgres import PostgresContainer

    _pg_container = PostgresContainer(
        image="postgres:16-alpine", username="pagamento", password="pagamento", dbname="pagamento_test", driver="psycopg"
    )
    _pg_container.start()
    os.environ["DATABASE_URL"] = _pg_container.get_connection_url()

_ACCOUNT = "123456789012"
os.environ.update(
    {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SECURITY_TOKEN": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_REGION": "us-east-1",
        "PAGAMENTO_EVENTS_TOPIC_ARN": f"arn:aws:sns:us-east-1:{_ACCOUNT}:pagamento-eventos",
        "PAGAMENTO_COMMANDS_QUEUE_URL": f"https://sqs.us-east-1.amazonaws.com/{_ACCOUNT}/pagamento-comandos",
        "WORKER_WAIT_SECONDS": "0",
        "JWT_SECRET_KEY": "test-secret-key",
        "ADMIN_USERNAME": "admin",
        "PUBLIC_BASE_URL": "https://pagamento.test",
        "MERCADO_PAGO_ACCESS_TOKEN": "TEST-token",
        "MERCADO_PAGO_WEBHOOK_SECRET": "",
        "MERCADO_PAGO_PAYER_EMAIL_OVERRIDE": "",
        "SMTP_ENABLED": "false",
    }
)
os.environ.pop("AWS_ENDPOINT_URL", None)

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from jose import jwt
from moto import mock_aws
from sqlalchemy import text

from app.main import app
from app.messaging.dispatcher import Dispatcher
from app.payments.adapters.sqlalchemy_uow import SqlAlchemyPaymentUnitOfWork
from app.payments.controller import get_gateway, get_notifier
from app.shared.database import get_engine, get_session_factory
from app.shared.email import IEmailNotifier
from app.shared.events import Envelope
from tests.fake_gateway import FakeMercadoPago

TABLES = ["charges", "payment_notifications", "processed_messages", "outbox_messages"]


def pytest_sessionstart(session) -> None:  # noqa: ARG001
    command.upgrade(Config("alembic.ini"), "head")


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ARG001
    if _pg_container is not None:
        _pg_container.stop()


@pytest.fixture(autouse=True)
def clean_database() -> Iterator[None]:
    yield
    with get_engine().begin() as connection:
        connection.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))


@pytest.fixture(autouse=True)
def aws() -> Iterator[None]:
    with mock_aws():
        from scripts.bootstrap_local import bootstrap

        bootstrap()
        yield


@dataclass
class SentEmail:
    to: str
    subject: str
    body: str


class FakeNotifier(IEmailNotifier):
    def __init__(self) -> None:
        self.sent: list[SentEmail] = []

    def send(self, *, to: str, subject: str, body: str) -> None:
        self.sent.append(SentEmail(to, subject, body))


def uow_factory() -> SqlAlchemyPaymentUnitOfWork:
    return SqlAlchemyPaymentUnitOfWork(get_session_factory())


@pytest.fixture
def mp() -> Iterator[FakeMercadoPago]:
    fake = FakeMercadoPago()
    app.dependency_overrides[get_gateway] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_gateway, None)


@pytest.fixture
def notifier() -> Iterator[FakeNotifier]:
    fake = FakeNotifier()
    app.dependency_overrides[get_notifier] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_notifier, None)


@pytest.fixture
def dispatcher(mp: FakeMercadoPago, notifier: FakeNotifier) -> Dispatcher:
    return Dispatcher(uow_factory, mp, notifier)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def make_token(subject: str = "admin") -> str:
    return jwt.encode({"sub": subject, "exp": datetime.now(UTC) + timedelta(minutes=5)}, "test-secret-key", algorithm="HS256")


@pytest.fixture
def admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token()}"}


def command_msg(message_type: str, order_id: int = 42, saga_id: str = "saga-42", **payload: Any) -> Envelope:
    return Envelope(type=message_type, payload=dict(payload), saga_id=saga_id, order_id=order_id)


def charge_payload(**overrides: Any) -> dict[str, Any]:
    """Payload de CriarCobranca: itens do orçamento aprovado + dados do cliente."""
    payload: dict[str, Any] = {
        "quote_id": "q-1",
        "customer": {"name": "Maria", "email": "maria@example.com"},
        "services": [{"service_id": "s1", "name": "Troca de óleo", "quantity": 1, "unit_price": 150.0}],
        "parts": [{"part_id": "p1", "name": "Óleo 5W30", "quantity": 4, "unit_price": 45.1}],
        "total": 330.4,
    }
    payload.update(overrides)
    return payload


def create_charge(dispatcher: Dispatcher, order_id: int = 42, saga_id: str | None = None, **overrides: Any) -> Envelope:
    command = command_msg("CriarCobranca", order_id=order_id, saga_id=saga_id or f"saga-{order_id}", **charge_payload(**overrides))
    dispatcher.handle(command)
    return command


def outbox() -> list[dict]:
    with get_engine().connect() as connection:
        return [row[0] for row in connection.execute(text("SELECT envelope FROM outbox_messages ORDER BY id"))]


def charge_of(order_id: int = 42):  # noqa: ANN201
    with uow_factory() as uow:
        return uow.get_by_order(order_id)
