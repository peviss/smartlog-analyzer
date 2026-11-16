"""HTTP routers. They only translate HTTP into service calls; every endpoint lives under /api."""

from fastapi import APIRouter

from api.routers import analysis, health, logs

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(logs.router)
api_router.include_router(analysis.router)
