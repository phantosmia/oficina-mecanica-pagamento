from fastapi import FastAPI

from app.payments.controller import admin_router, return_router, webhook_router
from app.shared.logging_config import RequestIDMiddleware, configure_logging
from app.shared.settings import settings
from app.system.controller import router as system_router

configure_logging(settings.log_level)

app = FastAPI(
    title=settings.app_name,
    description=(
        "Microsserviço de Pagamento da oficina mecânica: cobrança e estorno via Mercado Pago (Checkout Pro, API "
        "de Orders). Cobranças são criadas e desfeitas pela saga (SQS); a confirmação vem do webhook, do retorno do "
        "comprador ou da reconciliação, sempre consultando o Mercado Pago. As rotas /charges exigem o JWT de admin "
        "emitido pelo OS Service."
    ),
    version="1.0.0",
)

app.add_middleware(RequestIDMiddleware)

app.include_router(system_router)
app.include_router(webhook_router)
app.include_router(return_router)
app.include_router(admin_router)
