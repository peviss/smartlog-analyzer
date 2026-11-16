import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from api.database.session import get_sessionmaker
from api.llm.providers import provider_name
from api.routers.deps import LLMModelDep, SettingsDep
from api.schemas.health import HealthResponse

router = APIRouter(tags=["health"])
logger = logging.getLogger("api.health")


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health (checks the database)",
    responses={503: {"model": HealthResponse, "description": "Database unavailable"}},
)
async def health(settings: SettingsDep, llm_model: LLMModelDep):
    database = "ok"
    try:
        async with get_sessionmaker()() as session:
            await asyncio.wait_for(session.execute(text("SELECT 1")), timeout=2)
    except Exception as exc:
        logger.warning("health check: database unavailable", extra={"error": exc.__class__.__name__})
        database = "unavailable"

    body = HealthResponse(
        status="ok" if database == "ok" else "degraded",
        version=settings.app_version,
        database=database,
        llm_provider=provider_name(llm_model),
    )
    return JSONResponse(status_code=200 if database == "ok" else 503, content=body.model_dump())
