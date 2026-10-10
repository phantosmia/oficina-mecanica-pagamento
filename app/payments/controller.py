import logging
from html import escape

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from app.shared.database import get_session_factory
from app.shared.dependencies import get_current_admin
from app.shared.email import IEmailNotifier
from app.shared.http_errors import domain_error_handler
from app.shared.settings import settings
from app.shared.smtp_notifier import SmtpEmailNotifier
from app.payments.adapters.mercado_pago_gateway import MercadoPagoGateway
from app.payments.adapters.sqlalchemy_uow import SqlAlchemyPaymentUnitOfWork
from app.payments.adapters.webhook_signature import verify_signature
from app.payments.application.sync import SyncChargeUseCase
from app.payments.application.use_cases import GetChargeByOrderUseCase, ReceiveWebhookUseCase
from app.payments.domain.entities import Charge
from app.payments.domain.ports import IPaymentGateway
from app.payments.domain.value_objects import ChargeStatus
from app.payments.schemas import ChargeRead, WebhookAck

logger = logging.getLogger(__name__)


def uow_factory() -> SqlAlchemyPaymentUnitOfWork:
    return SqlAlchemyPaymentUnitOfWork(get_session_factory())


def get_gateway() -> IPaymentGateway:
    return MercadoPagoGateway(settings.mp_access_token, settings.mp_base_url, settings.mp_timeout_seconds, settings.payment_expiration)


def get_notifier() -> IEmailNotifier:
    return SmtpEmailNotifier()


def get_sync(gateway: IPaymentGateway = Depends(get_gateway), notifier: IEmailNotifier = Depends(get_notifier)) -> SyncChargeUseCase:
    return SyncChargeUseCase(uow_factory, gateway, notifier)


def to_response(charge: Charge) -> ChargeRead:
    return ChargeRead(**{field: getattr(charge, field) for field in ChargeRead.model_fields})


# ── Webhook do Mercado Pago ──────────────────────────────────────────────────

webhook_router = APIRouter(tags=["webhooks"])


@webhook_router.post("/webhooks/mercado-pago", response_model=WebhookAck)
async def mercado_pago_webhook(request: Request, sync: SyncChargeUseCase = Depends(get_sync)) -> WebhookAck:
    """Notificação de order do Mercado Pago (configurada no painel, tópico
    "Order"). Com `MERCADO_PAGO_WEBHOOK_SECRET` definida, assinatura inválida → 401."""
    try:
        payload = await request.json()
    except ValueError:
        payload = {}
    data_id = request.query_params.get("data.id") or (payload.get("data") or {}).get("id")
    request_id = request.headers.get("x-request-id")
    signature_valid: bool | None = None
    if settings.mp_webhook_secret:
        signature_valid = verify_signature(settings.mp_webhook_secret, request.headers.get("x-signature"), request_id, data_id)
    ReceiveWebhookUseCase(uow_factory, sync).execute(data_id, request_id, signature_valid, payload)
    if signature_valid is False:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Assinatura inválida.")
    return WebhookAck(received=True)


# ── Retorno do comprador depois do checkout ──────────────────────────────────

return_router = APIRouter(tags=["public"], include_in_schema=False)

_RETURN_TEXT = {
    ChargeStatus.AWAITING_PAYMENT: "Ainda não recebemos a confirmação do pagamento. Se você pagou por Pix ou boleto, ela pode levar alguns minutos.",
    ChargeStatus.PAID: "Pagamento confirmado! O serviço entrou na fila de reparo.",
    ChargeStatus.DECLINED: "O pagamento não foi concluído. Entre em contato com a oficina.",
    ChargeStatus.CANCELLED: "Esta cobrança foi cancelada pela oficina.",
    ChargeStatus.REFUNDED: "Este pagamento foi estornado.",
}


@return_router.get("/pagamentos/{charge_id}/retorno", response_class=HTMLResponse)
def buyer_return(charge_id: str, sync: SyncChargeUseCase = Depends(get_sync)) -> HTMLResponse:
    """Para onde o Mercado Pago manda o comprador depois do checkout. Já
    aproveita para sincronizar: funciona mesmo sem webhook (ambiente local)."""
    charge = sync.by_charge_id(charge_id)
    if charge is None:
        raise HTTPException(status_code=404, detail="Cobrança não encontrada.")
    return HTMLResponse(
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>Pagamento da OS #{charge.order_id}</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:600px;margin:3rem auto;padding:0 1rem;color:#1f2933}"
        "p{padding:1rem;background:#f5f7fa;border-radius:6px}</style></head><body>"
        f"<h1>Pagamento da OS #{charge.order_id}</h1>"
        f"<p>{escape(_RETURN_TEXT[charge.status])}</p>"
        f"<div>Valor: R$ {charge.amount:.2f}</div></body></html>"
    )


# ── Admin (JWT do OS Service) ────────────────────────────────────────────────

admin_router = APIRouter(prefix="/charges", tags=["charges"], dependencies=[Depends(get_current_admin)])


@admin_router.get("/{order_id}", response_model=ChargeRead)
def get_charge(order_id: int) -> ChargeRead:
    """Cobrança da OS, incluindo o link de pagamento e o status no Mercado Pago."""
    with domain_error_handler():
        return to_response(GetChargeByOrderUseCase(uow_factory).execute(order_id))


@admin_router.post("/{order_id}/sync", response_model=ChargeRead)
def sync_charge(order_id: int, sync: SyncChargeUseCase = Depends(get_sync)) -> ChargeRead:
    """Consulta o Mercado Pago agora (sem esperar webhook ou reconciliação)."""
    with domain_error_handler():
        GetChargeByOrderUseCase(uow_factory).execute(order_id)
        return to_response(sync.by_order_id(order_id))
