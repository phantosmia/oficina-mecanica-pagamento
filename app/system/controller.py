from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.shared.database import get_engine

router = APIRouter(tags=["system"])


class HealthStatus(BaseModel):
    status: str


class ReadinessStatus(BaseModel):
    status: str
    schema_revision: str


def _head_revision() -> str | None:
    return ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()


@router.get("/health", response_model=HealthStatus)
def healthcheck() -> HealthStatus:
    return HealthStatus(status="ok")


@router.get("/ready", response_model=ReadinessStatus)
def readiness() -> ReadinessStatus:
    """O pod só recebe tráfego se o banco responde e as migrations já foram
    aplicadas (o Job de migration roda separado, antes do rollout)."""
    try:
        with get_engine().connect() as connection:
            current = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except SQLAlchemyError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Banco indisponível.") from error
    if current != _head_revision():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Migrations pendentes.")
    return ReadinessStatus(status="ok", schema_revision=current)
