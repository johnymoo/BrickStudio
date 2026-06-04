"""Async SQLAlchemy engine + session factory.

We expose :func:`async_session_factory` (callable) and :func:`get_engine` for
code paths that need a long-lived engine (e.g. Celery workers). HTTP handlers
should depend on :func:`app.deps.get_db_session` instead, which yields a
proper transactional session per request.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session

from app.config import settings


def _engine_kwargs() -> dict[str, Any]:
    return {
        "echo": False,
        "pool_pre_ping": True,
        # NullPool keeps a single connection per call, avoiding cross-loop
        # issues when Celery tasks spawn their own event loops via
        # ``asyncio.run``. Trade-off: slightly more reconnect overhead.
        "poolclass": __import__("sqlalchemy.pool", fromlist=["NullPool"]).NullPool,
    }


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Lazily build the global async engine (process-local)."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(settings.sqlalchemy_url, **_engine_kwargs())
    return _engine


def async_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return (and cache) the process-local session factory."""
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            class_=AsyncSession,
        )
    return _sessionmaker


async def dispose_engine() -> None:
    """Tear down the engine (used by the FastAPI shutdown hook)."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


def get_sync_engine_url() -> str:
    """Sync DSN for alembic / one-off scripts."""
    return settings.sqlalchemy_url_sync


# Type alias for the typed annotations.
__all__ = [
    "AsyncEngine",
    "AsyncSession",
    "Session",
    "async_session_factory",
    "dispose_engine",
    "get_engine",
    "get_sync_engine_url",
]


# Convenience for the import elsewhere in this package (e.g. workers).
async def get_session() -> AsyncIterator[AsyncSession]:
    factory = async_session_factory()
    async with factory() as session:
        yield session
