"""v1 API router aggregation."""
from fastapi import APIRouter

from api.v1 import assets, captures, health, jobs

api_v1_router = APIRouter()
api_v1_router.include_router(health.router)
api_v1_router.include_router(captures.router)
api_v1_router.include_router(jobs.router)
api_v1_router.include_router(assets.router)

__all__ = ["api_v1_router"]
