"""Pydantic v2 request/response schemas (the "models" layer in design.md §5)."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------
class CaptureCreate(BaseModel):
    """Schema for the multipart ``POST /captures`` form fields."""

    model_config = ConfigDict(extra="forbid")

    part_id: str = Field(min_length=1, max_length=64)
    # `images` is a File[] in the request; we don't model it as a Pydantic field.
    # The route uses ``Form(...)`` + ``UploadFile`` annotations instead.


class CaptureRead(BaseModel):
    """Response shape for ``GET /captures/{capture_id}`` and the 201 from POST."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    capture_id: UUID = Field(validation_alias="id")
    part_id: str
    status: str
    image_count: int
    created_at: datetime
    job_id: UUID | None = None
    image_keys: list[str] = Field(default_factory=list)
    # Phase 2 (design-phase2.md §3.1): how the user captured the
    # photos. Default ``phone_walkaround`` matches the design contract.
    capture_mode: str = "phone_walkaround"


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------
class JobRead(BaseModel):
    """Response shape for ``GET /jobs/{job_id}``."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    job_id: UUID = Field(validation_alias="id")
    capture_id: UUID
    kind: str
    status: str
    progress: int
    stage: str | None = None
    error: str | None = None
    # ``result_asset_id`` is the id of the first asset the job produced
    # (mesh_gltf for the reconstruct pipeline). It is computed at
    # serialisation time from the Job.assets relationship so the
    # schema doesn't drift from the model layer.
    result_asset_id: UUID | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def model_validate(cls, obj: Any, *args: Any, **kwargs: Any) -> "JobRead":
        """Materialise ``result_asset_id`` from the assets relationship.

        The Job ORM has an ``assets`` collection (one-to-many). The
        "result" asset is the first one — for the reconstruct pipeline
        there is exactly one mesh_gltf asset. We resolve it here so
        the API contract stays consistent with the SSE stream.
        """
        # Pull a dict from the ORM so the Pydantic validator doesn't
        # have to understand SQLAlchemy relationships.
        if hasattr(obj, "assets") and getattr(obj, "result_asset_id", None) is None:
            assets = list(getattr(obj, "assets", []) or [])
            if assets:
                obj = {
                    "id": obj.id,
                    "capture_id": obj.capture_id,
                    "kind": obj.kind,
                    "status": obj.status,
                    "progress": obj.progress,
                    "stage": obj.stage,
                    "error": obj.error,
                    "result_asset_id": assets[0].id,
                    "started_at": obj.started_at,
                    "finished_at": obj.finished_at,
                    "created_at": obj.created_at,
                    "updated_at": obj.updated_at,
                }
        return super().model_validate(obj, *args, **kwargs)


# ---------------------------------------------------------------------------
# Asset
# ---------------------------------------------------------------------------
class AssetRead(BaseModel):
    """Response shape for ``GET /assets/{asset_id}`` (the JSON body returned
    alongside the 302 — clients can also follow the redirect)."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    asset_id: UUID = Field(validation_alias="id")
    job_id: UUID
    kind: str
    url: str
    size_bytes: int | None = None
    meta: dict[str, Any] | None = None
    expires_at: datetime | None = None


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
class HealthRead(BaseModel):
    """Response shape for ``GET /health`` (design.md §6.4)."""

    status: str
    version: str
    db: str
    storage: str
    redis: str | None = None
    details: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Error envelope
# ---------------------------------------------------------------------------
class ErrorDetail(BaseModel):
    """One field in the standard error envelope (design.md §9)."""

    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorEnvelope(BaseModel):
    """Standard ``{"error": {...}}`` response body."""

    error: ErrorDetail


__all__ = [
    "AssetRead",
    "CaptureCreate",
    "CaptureRead",
    "ErrorDetail",
    "ErrorEnvelope",
    "HealthRead",
    "JobRead",
]
