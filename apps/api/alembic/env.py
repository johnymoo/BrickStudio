"""Alembic environment — async-aware.

Reads the DSN from :class:`app.config.settings` and runs migrations against
an async engine via ``connection.run_sync(...)`` so we can share the
``Base.metadata`` defined in :mod:`db.base` without any sync copy.
"""
from __future__ import annotations

import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Make the project root importable so `from app.config import settings` works
# when alembic is invoked from anywhere. We add `apps/api/src` (the actual
# source tree) AND `apps/api` (for editable installs).
HERE = Path(__file__).resolve()
API_ROOT = HERE.parents[1]  # apps/api
SRC_ROOT = API_ROOT / "src"
for p in (SRC_ROOT, API_ROOT, API_ROOT.parent):
    p_str = str(p)
    if p_str not in sys.path:
        sys.path.insert(0, p_str)

from app.config import settings  # noqa: E402
from db.base import Base  # noqa: E402
import db.models  # noqa: E402,F401  (registers models on Base.metadata)

config = context.config
config.set_main_option("sqlalchemy.url", settings.sqlalchemy_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Render SQL to stdout (no DB connection)."""
    context.configure(
        url=settings.sqlalchemy_url_sync,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations against a live async DB connection."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        future=True,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
