"""FastAPI application factory."""

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from pydantic_ai.models import Model

from api.core.config import get_settings
from api.core.errors import register_exception_handlers
from api.core.logging import configure_logging, request_id_var
from api.database.session import dispose_engine
from api.llm.providers import build_model, close_model, provider_name
from api.routers import api_router

logger = logging.getLogger("api.http")


def create_app(llm_model: Model | None = None) -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.llm_model = llm_model or build_model(settings)
        logger.info(
            "starting",
            extra={
                "llm_provider": provider_name(app.state.llm_model),
                "llm_model": app.state.llm_model.model_name,
                "environment": settings.environment,
            },
        )
        yield
        if llm_model is None:  # only close what we created
            await close_model(app.state.llm_model)
        await dispose_engine()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Ingest logs from microservices, query them and get AI-generated insights.",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        return response

    register_exception_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
