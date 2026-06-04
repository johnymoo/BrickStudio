"""Application configuration loaded from env / `deploy/.env`.

Uses pydantic-settings so config can be supplied via process env, an explicit
`.env` file, or the YAML-ish `deploy/.env` (which has simple `KEY=VALUE` lines,
compatible with python-dotenv out of the box).

All fields are read once at import time and exposed as a singleton
:data:`settings` for convenience. The instance is frozen (immutable) to keep
the rest of the codebase honest.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _default_env_file() -> str:
    """Locate `deploy/.env` relative to the repo root, falling back to .env.example.

    The repo root is assumed to be 4 parents up from this file
    (apps/api/src/app/config.py -> /).
    """
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        candidate = ancestor / "deploy" / ".env"
        if candidate.exists():
            return str(candidate)
        candidate = ancestor / "deploy" / ".env.example"
        if candidate.exists():
            return str(candidate)
    return ".env"


class Settings(BaseSettings):
    """Strongly-typed application settings.

    Names mirror the keys defined in `deploy/.env.example` so the same
    `.env` file works for the API process, the Celery worker, and `alembic`.
    """

    model_config = SettingsConfigDict(
        env_file=_default_env_file(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    # ---- Postgres ----------------------------------------------------------
    database_url: str = Field(
        default="postgresql+asyncpg://blocktool:blocktool@localhost:5432/blocktool",
        description="Async DSN consumed by SQLAlchemy (asyncpg driver).",
    )
    database_url_sync: str | None = Field(
        default=None,
        description="Sync DSN for alembic / scripts that cannot use asyncpg.",
    )

    # ---- Redis -------------------------------------------------------------
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Generic Redis URL (health checks, pub/sub).",
    )
    redis_celery_url: str = Field(
        default="redis://localhost:6379/1",
        description="Dedicated Redis DB for Celery broker + result backend.",
    )

    # ---- MinIO / S3 --------------------------------------------------------
    s3_endpoint: str = Field(
        default="http://localhost:9000",
        description="MinIO endpoint used by the S3 client.",
    )
    s3_region: str = Field(default="us-east-1")
    s3_access_key: str = Field(default="blocktool")
    s3_secret_key: str = Field(default="blocktool")
    s3_bucket_raw: str = Field(
        default="blocktool",
        description="Bucket for original uploaded photos.",
    )
    s3_bucket_recon: str = Field(
        default="blocktool-recon",
        description="Bucket for 3D reconstruction artefacts.",
    )
    s3_public_endpoint: str | None = Field(
        default=None,
        description=(
            "Public-facing S3 endpoint (e.g. Caddy reverse proxy). "
            "Used to build asset URLs that the browser can reach."
        ),
    )
    s3_secure: bool = Field(default=False, description="Use https for S3 client.")

    # ---- API ---------------------------------------------------------------
    api_prefix: str = Field(default="/api/v1", description="URL prefix for all v1 routes.")
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://localhost:8080"],
        description="Allowed CORS origins (browser frontends).",
    )
    environment: Literal["dev", "test", "staging", "prod"] = "dev"
    log_level: str = "INFO"

    # ---- Celery ------------------------------------------------------------
    celery_broker_url: str | None = Field(
        default=None,
        description="Defaults to redis_celery_url when unset.",
    )
    celery_result_backend: str | None = Field(
        default=None,
        description="Defaults to redis_celery_url when unset.",
    )

    # ---- Validators --------------------------------------------------------
    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors(cls, value: object) -> list[str]:
        """Allow `CORS_ORIGINS=a,b,c` (string) and JSON list forms."""
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        raise ValueError(f"Unsupported cors_origins type: {type(value)!r}")

    @field_validator("api_prefix")
    @classmethod
    def _normalize_api_prefix(cls, value: str) -> str:
        if not value.startswith("/"):
            value = "/" + value
        return value.rstrip("/") or "/"

    # ---- Derived properties ------------------------------------------------
    @property
    def sqlalchemy_url(self) -> str:
        """DATABASE_URL with the asyncpg driver forced (in case user gave a sync DSN)."""
        url = self.database_url
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url

    @property
    def sqlalchemy_url_sync(self) -> str:
        if self.database_url_sync:
            return self.database_url_sync
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    @property
    def effective_celery_broker(self) -> str:
        return self.celery_broker_url or self.redis_celery_url

    @property
    def effective_celery_backend(self) -> str:
        return self.celery_result_backend or self.redis_celery_url

    @property
    def s3_public_base(self) -> str:
        """Public-facing S3 base URL (used to build browser-reachable asset URLs)."""
        return self.s3_public_endpoint or self.s3_endpoint


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide cached :class:`Settings` accessor."""
    return Settings()


# Module-level singleton — import this directly in app code.
settings = get_settings()
