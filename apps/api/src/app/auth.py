"""Small auth dependencies used before full user accounts exist."""
from __future__ import annotations

from typing import Annotated

from fastapi import Header, HTTPException, status

from app.config import settings


async def require_library_admin(
    x_library_admin_token: Annotated[str | None, Header(alias="X-Library-Admin-Token")] = None,
) -> None:
    """Require the configured library admin token for mutable curation APIs."""

    expected = settings.library_admin_token
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="library admin token is not configured",
        )
    if x_library_admin_token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="library admin token required",
        )


__all__ = ["require_library_admin"]
