"""Unit-test conftest — overrides the session-wide DB/Redis/MinIO fixtures.

Pure-function tests (no database, no HTTP client) live under ``tests/unit/``.
This conftest neutralises the autouse fixtures from the parent ``conftest.py``
so that ``pytest tests/unit/`` works without Postgres, Redis, or MinIO.
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def _services() -> None:  # type: ignore[override]
    """No-op override — unit tests need no infrastructure."""


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations(_services: None) -> None:  # type: ignore[override]
    """No-op override — unit tests need no migrations."""


@pytest.fixture(autouse=True)
async def _truncate_tables() -> None:  # type: ignore[override]
    """No-op override — unit tests need no table truncation."""
