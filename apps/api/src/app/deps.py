"""Shared FastAPI dependencies (db session, redis, settings)."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from db.session import async_session_factory

SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Yield a transactional async session per request.

    Commits on clean exit, rolls back on exception. Closes the session in any
    case. This is the canonical dependency for HTTP handlers.
    """
    factory = async_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


DBSessionDep = Annotated[AsyncSession, Depends(get_db_session)]
