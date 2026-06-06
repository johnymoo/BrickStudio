"""v1 API router aggregation."""

from fastapi import APIRouter

from api.v1 import assets, captures, health, jobs, parametric_blocks

api_v1_router = APIRouter()
api_v1_router.include_router(health.router)
api_v1_router.include_router(captures.router)
api_v1_router.include_router(jobs.router)
api_v1_router.include_router(assets.router)
# v0.3 parametric-block endpoint. Sits alongside ``captures`` — the
# two paths share a single ``Capture`` row + ``reconstruct`` task,
# disambiguated by ``capture.mode``.
api_v1_router.include_router(parametric_blocks.router)

__all__ = ["api_v1_router"]
