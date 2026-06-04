"""FastAPI app factory + global error handling.

Run with::

    uvicorn src.app.main:app --reload

This module wires up:

* CORS using ``settings.cors_origins`` (browsers, the PWA).
* An async lifespan that creates the SQLAlchemy engine, ensures MinIO
  buckets exist, and disposes the engine on shutdown.
* A global exception handler that maps :class:`core.errors.AppError`
  subclasses to the standard ``{"error": {...}}`` envelope, and turns
  unexpected exceptions into 500s with a stable ``code``.
* The v1 router under ``settings.api_prefix`` (default ``/api/v1``).
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.v1 import api_v1_router
from app.config import settings
from app.logging import configure_logging
from core.errors import AppError
from models.schemas import ErrorDetail, ErrorEnvelope
from storage.minio_client import StorageClient, storage

logger = logging.getLogger(__name__)


# ---- Lifespan -------------------------------------------------------------
@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    logger.info("api.startup: env=%s api_prefix=%s", settings.environment, settings.api_prefix)

    # Ensure MinIO buckets exist. We do this eagerly so the first request
    # doesn't pay the latency cost.
    client = StorageClient()
    try:
        client.ensure_buckets(settings.s3_bucket_raw, settings.s3_bucket_recon)
    except Exception as exc:
        # Don't fail boot — the health endpoint will report storage: down.
        logger.warning("api.startup: could not ensure MinIO buckets: %s", exc)
    _.state.storage = client

    try:
        yield
    finally:
        from db.session import dispose_engine

        await dispose_engine()
        logger.info("api.shutdown: clean")


# ---- App factory ----------------------------------------------------------
def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(
        title="Blocktool API",
        version="0.1.0",
        description="积木建模工具 — FastAPI 后端 (captures / jobs / assets / health).",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Location"],
    )

    # Routers
    app.include_router(api_v1_router, prefix=settings.api_prefix)

    # Root redirect so `/` doesn't 404 in browsers.
    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "name": "blocktool-api",
            "version": app.version,
            "api_prefix": settings.api_prefix,
            "health": f"{settings.api_prefix}/health",
        }

    # Global error handlers ------------------------------------------------
    @app.exception_handler(AppError)
    async def _app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        envelope = ErrorEnvelope(
            error=ErrorDetail(code=exc.code, message=exc.message, details=exc.details)
        )
        return JSONResponse(
            status_code=exc.http_status,
            content=envelope.model_dump(exclude_none=True),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(
        _: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = _http_code_to_error_code(exc.status_code, str(exc.detail))
        envelope = ErrorEnvelope(
            error=ErrorDetail(code=code, message=str(exc.detail) or code)
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=envelope.model_dump(exclude_none=True),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("api.unhandled: %s", exc)
        envelope = ErrorEnvelope(
            error=ErrorDetail(
                code="INTERNAL_ERROR",
                message="internal server error",
            )
        )
        return JSONResponse(
            status_code=500,
            content=envelope.model_dump(exclude_none=True),
        )

    return app


def _http_code_to_error_code(status_code: int, detail: str) -> str:
    if status_code == 404:
        return "NOT_FOUND"
    if status_code == 405:
        return "METHOD_NOT_ALLOWED"
    if 400 <= status_code < 500:
        return "BAD_REQUEST"
    if status_code == 422:
        return "VALIDATION_ERROR"
    return "INTERNAL_ERROR"


# Module-level app instance for ``uvicorn src.app.main:app``.
app = create_app()
storage_singleton = storage  # for tests that want to poke at it.
