"""SQLAlchemy 2.0 declarative base + shared mixins.

All ORM models in the project inherit from :class:`Base` and use the
typed-`Mapped[]` API (the modern SQLAlchemy 2.0 style).
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Centralized naming convention keeps Alembic diffs deterministic.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model."""

    metadata = metadata


# ---- Reusable `mapped_column` factory functions ---------------------------
# We expose plain functions (not Annotated aliases) because that's the most
# explicit pattern — the column is materialized in the model and SQLAlchemy
# gets a fresh Column object per assignment.


def make_uuid_pk() -> Mapped[UUID]:
    """``id: Mapped[UUID] = make_uuid_pk()`` — a primary-key UUID with a
    client-side ``uuid4`` default that is filled in before the INSERT.
    """
    return mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)


def make_created_at() -> Mapped[datetime]:
    """``created_at: Mapped[datetime] = make_created_at()`` — set to ``now()``
    on the database side at insert time.
    """
    return mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


def make_updated_at() -> Mapped[datetime]:
    """``updated_at: Mapped[datetime] = make_updated_at()`` — ``now()`` on
    insert AND on every UPDATE.
    """
    return mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


__all__ = [
    "JSONB",
    "Base",
    "make_created_at",
    "make_updated_at",
    "make_uuid_pk",
]
