"""Pydantic v2 request/response schemas (the "models" layer in design.md §5)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Measurement fallback shared by AR capture responses and capture detail
# ---------------------------------------------------------------------------
class NeedsMeasurement(BaseModel):
    """Returned when recognition confidence is too low — caliper fallback."""

    model_config = ConfigDict(extra="forbid")

    #: The 5 caliper fields the client should collect (same as
    #: ``/parametric-blocks``).
    fields: list[str] = Field(default_factory=list)
    #: Human-readable, caliper-friendly guidance ("卡两端外边缘…").
    guidance: str
    #: Where to submit the measured fallback.
    endpoint: str = "/api/v1/parametric-blocks"
    #: Why recognition fell back (for logs / UI).
    reason: str | None = None


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
    updated_at: datetime | None = None
    job_id: UUID | None = None
    image_keys: list[str] = Field(default_factory=list)
    # Phase 2 (design-phase2.md §3.1): how the user captured the
    # photos. Default ``phone_walkaround`` matches the design contract.
    capture_mode: str = "phone_walkaround"
    mode: str = "photo"
    system: str | None = None
    kind: str | None = None
    units_x: int | None = None
    units_y: int | None = None
    recognition_result: dict[str, Any] | None = None
    needs_measurement: NeedsMeasurement | None = None


class CaptureImageRead(BaseModel):
    """A browser-fetchable URL for one raw image owned by a capture."""

    key: str
    url: str
    expires_at: datetime | None = None


class CaptureImagesRead(BaseModel):
    """Response for ``GET /captures/{capture_id}/images``."""

    capture_id: UUID
    images: list[CaptureImageRead] = Field(default_factory=list)


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
    def model_validate(cls, obj: Any, *args: Any, **kwargs: Any) -> JobRead:
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
# Parametric block (v0.3+, capture-procedure.md / ROADMAP.md §v0.3)
# ---------------------------------------------------------------------------
# The 5 caliper-friendly fields. Matches ``tools/measure_block.py``'s
# ``raw_measurements_mm`` shape verbatim — the worker just feeds them
# through ``derive_spec_from_raw`` (re-used via tools.measure_block, or
# inlined into the worker) to populate the 4 ``BlockSpec`` overrides.
#
# All values are positive millimetres; the worker cross-checks
# (1A > 1B, ④ > ②, |③ - (1A - 1B)/2| < 0.5 mm) and stores the warnings
# in ``capture.cross_check_warnings``.
_RAW_MEASUREMENT_KEYS: tuple[str, ...] = (
    "outer_pitch_mm",
    "inner_pitch_mm",
    "stud_diameter_mm",
    "brick_height_net_mm",
    "brick_height_total_mm",
)


class ParametricBlockRequest(BaseModel):
    """Request body for ``POST /api/v1/parametric-blocks``.

    The HTTP layer accepts these as ``Form()`` fields, not as a JSON
    body — multipart is the only way to also accept optional
    reference photos. This model exists so the field shape is
    documented and testable in isolation (e.g. unit tests of the
    worker spec-derivation logic).
    """

    model_config = ConfigDict(extra="forbid")

    #: One of ``"duplo"`` / ``"lego"`` / ``"feile"`` / ``"generic"``.
    #: ``"generic"`` requires explicit ``unit_mm`` etc. (worker will
    #: error out if those are missing).
    system: Literal["duplo", "lego", "feile", "generic"] = "duplo"
    #: One of ``"brick"`` / ``"plate"`` / ``"tile"`` / ``"slope"``.
    kind: Literal["brick", "plate", "tile", "slope"] = "brick"
    #: Stud-grid dimensions in units. Positive ints; 1..16 covers the
    #: whole 1x1..16x16 range we render in v0.3.
    units_x: int = Field(default=2, ge=1, le=16)
    units_y: int = Field(default=2, ge=1, le=16)
    #: 5 caliper-friendly measurements in mm. Each must be > 0.
    raw_measurements_mm: dict[str, float]


class ParametricBlockRead(BaseModel):
    """Response shape for ``POST /api/v1/parametric-blocks``.

    Mirrors :class:`CaptureRead` for the parametric path: the user
    gets back a ``capture_id`` (the DB row) and a ``job_id`` (the
    Celery task that builds the GLB). ``status`` is always
    ``"pending"`` on the 201 — the GLB lands in MinIO within ~1s
    (the worker is in-process fast for parametric, no SfM).
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    capture_id: UUID = Field(validation_alias="id")
    part_id: str
    status: str
    #: Always ``"parametric_block"`` on this endpoint.
    mode: str = "parametric_block"
    system: str
    kind: str
    units_x: int
    units_y: int
    #: Verbatim from the form field — preserved so the worker can
    #: re-derive the spec on retry.
    raw_measurements_mm: dict[str, float]
    #: 4 spec values pre-derived by the route layer
    #: (``unit_mm`` / ``height_mm`` / ``knob_diameter_mm`` /
    #: ``knob_height_mm``). The worker re-derives from
    #: ``raw_measurements_mm`` for safety; this field is informational.
    derived_spec_mm: dict[str, float] | None = None
    #: Cross-check warnings (empty list = clean). The route layer
    #: runs the same checks as ``tools/measure_block.cross_check_raw``
    #: and persists the result.
    cross_check_warnings: list[str] = Field(default_factory=list)
    job_id: UUID | None = None
    created_at: datetime


# ---------------------------------------------------------------------------
# AR capture recognition (v0.5)
# ---------------------------------------------------------------------------
class RecognizedBlock(BaseModel):
    """The recognizer's classification of an AR capture."""

    model_config = ConfigDict(extra="forbid")

    system: str | None = None
    kind: str
    units_x: int | None = None
    units_y: int | None = None
    pitch_mm: float | None = None
    confidence: float = 0.0


class ArCaptureRead(BaseModel):
    """Response shape for ``POST /api/v1/ar-captures``."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    capture_id: UUID
    part_id: str
    #: ``"recognized"`` (a GLB job was dispatched) or
    #: ``"needs_measurement"`` (no job; client falls back to caliper).
    status: Literal["recognized", "needs_measurement"]
    mode: str = "ar_recognized"
    recognized: RecognizedBlock
    needs_measurement: NeedsMeasurement | None = None
    job_id: UUID | None = None
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime


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
# Library part (issue #5)
# ---------------------------------------------------------------------------
class LibraryPartRead(BaseModel):
    """Response shape for ``/library`` list + detail."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    part_id: UUID = Field(validation_alias="id")
    capture_id: UUID
    #: GLB asset id — the web fetches the presigned URL via GET /assets/{id}.
    asset_id: UUID | None = None
    #: photo / parametric_block / ar_recognized (snapshot of capture.mode).
    source_mode: str
    system: str | None = None
    kind: str | None = None
    units_x: int | None = None
    units_y: int | None = None
    derived_spec_mm: dict[str, Any] | None = None
    color: str | None = None
    name: str
    notes: str | None = None
    #: pending / verified / rejected.
    status: str
    created_at: datetime
    updated_at: datetime


class LibraryPartUpdate(BaseModel):
    """Request body for ``PATCH /library/{id}`` — partial update."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    notes: str | None = None
    status: Literal["pending", "verified", "rejected"] | None = None

    @field_validator("name", "status")
    @classmethod
    def _reject_null_required_fields(cls, value: object) -> object:
        if value is None:
            raise ValueError("field may be omitted but not null")
        return value


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
    "ArCaptureRead",
    "AssetRead",
    "CaptureCreate",
    "CaptureImageRead",
    "CaptureImagesRead",
    "CaptureRead",
    "ErrorDetail",
    "ErrorEnvelope",
    "HealthRead",
    "JobRead",
    "LibraryPartRead",
    "LibraryPartUpdate",
    "NeedsMeasurement",
    "ParametricBlockRead",
    "ParametricBlockRequest",
    "RecognizedBlock",
]
