import json

import boto3
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.messaging.dispatcher import Dispatcher, parse_body
from app.messaging.sqs_consumer import SqsConsumer
from app.shared.database import get_engine, get_session_factory
from app.shared.outbox import OutboxRelay
from app.shared.settings import settings
from app.shared.sns_publisher import SnsEventPublisher
from app.payments.adapters.webhook_signature import build_manifest, verify_signature
from tests.conftest import charge_payload, command_msg


def test_command_round_trip(dispatcher: Dispatcher) -> None:
    sqs, sns = boto3.client("sqs", region_name="us-east-1"), boto3.client("sns", region_name="us-east-1")
    orchestrator = sqs.create_queue(QueueName="os-saga-eventos")["QueueUrl"]
    arn = sqs.get_queue_attributes(QueueUrl=orchestrator, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    sns.subscribe(TopicArn=settings.events_topic_arn, Protocol="sqs", Endpoint=arn, Attributes={"RawMessageDelivery": "true"})

    command = command_msg("CriarCobranca", order_id=7, saga_id="saga-7", **charge_payload())
    sqs.send_message(QueueUrl=settings.commands_queue_url, MessageBody=json.dumps(command.to_dict()))

    assert SqsConsumer([settings.commands_queue_url], dispatcher, wait_seconds=0).poll_once() == 1
    assert OutboxRelay(get_session_factory(), SnsEventPublisher(settings.events_topic_arn)).run_once() == 1
    [received] = sqs.receive_message(QueueUrl=orchestrator, MessageAttributeNames=["All"])["Messages"]
    assert json.loads(received["Body"])["type"] == "CobrancaCriada"
    assert received["MessageAttributes"]["order_id"]["StringValue"] == "7"


def test_parse_body_unwraps_sns_notification() -> None:
    inner = command_msg("EstornarPagamento").to_dict()
    assert parse_body(json.dumps({"Type": "Notification", "Message": json.dumps(inner)})).to_dict() == inner


def test_signature_manifest_and_edge_cases() -> None:
    assert build_manifest("ORD01ABC", "req-1", "123") == "id:ord01abc;request-id:req-1;ts:123;"
    assert build_manifest(None, "req-1", "123") == "request-id:req-1;ts:123;"
    assert verify_signature("s", None, "r", "d") is False
    assert verify_signature("s", "lixo", "r", "d") is False


def test_health_and_ready(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json() == {"status": "ok", "schema_revision": "0001"}
    with get_engine().begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num = 'antiga'"))
    try:
        assert client.get("/ready").status_code == 503
    finally:
        with get_engine().begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num = '0001'"))
