"""``/api/v1/parametric-blocks`` — multipart upload for measured-only captures.

This is the v0.3 entry point for the parametric-block workflow
(``docs/ROADMAP.md`` §v0.3, ``docs/capture-procedure.md``). It sits
*alongside* the existing ``/captures`` photo endpoint — it does NOT
replace it. The two paths share a single ``Capture`` row (the
``mode`` column disambiguates) and a single ``reconstruct`` Celery
task (the worker dispatches on ``capture.mode``).

Why a separate endpoint
-----------------------
The photo endpoint enforces 4-20 images; the parametric path is
often zero-image (the user has nothing to upload — just caliper
numbers) and at most ~20 (reference shots for visual comparison,
not for SfM). The form fields are also different: photos for
``/captures`` vs. system/kind/units/measurements for this endpoint.
Folding the two into one path would just bloat the request schema
with optional everything.

Request shape
-------------
::

    POST /api/v1/parametric-blocks
    Content-Type: multipart/form-data

      photos:               0-20 File (optional, image/*)
      part_id:              str (optional; default = system-kind-UnitsXunitsY)
      system:               "duplo" | "lego" | "feile" | "generic"
      kind:                 "brick" | "plate" | "tile" | "slope"
      units_x, units_y:     int (1..16)
      raw_measurements_mm:  str (JSON object — 5 caliper fields)

The 5 raw measurements map 1:1 to ``tools/measure_block.py``'s
``raw_measurements_mm``. The route re-uses the same derive + cross
check logic so the API and the CLI stay numerically consistent.

Response shape (201)
--------------------
::

    {
      "capture_id":          "<uuid>",
      "part_id":             "duplo-brick-2x2",
      "status":              "pending",
      "mode":                "parametric_block",
      "system":              "duplo",
      "kind":                "brick",
      "units_x":             2,
      "units_y":             2,
      "raw_measurements_mm": {...5 fields...},
      "derived_spec_mm":     {...4 fields...},
      "cross_check_warnings": [],
      "job_id":              "<uuid>",
      "created_at":          "2026-06-06T..."
    }

Worker
------
The Celery task ``workers.tasks.reconstruct.reconstruct`` reads
``capture.mode`` at the start and dispatches:

* ``"photo"`` (default) → existing COLMAP → Open3D multi → Open3D
  fallback ladder (unchanged).
* ``"parametric_block"`` → new :func:`_run_parametric_pipeline` that
  calls :func:`services.block_generator.export_glb` and writes the
  resulting mesh straight to MinIO. No photo download, no SfM.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, UploadFile, status
from fastapi.responses import JSONResponse

from app.deps import DBSessionDep
from core.errors import CaptureInvalid
from db.models import Capture, Job
from models.schemas import ParametricBlockRead
from storage.minio_client import raw_object_key, storage
from workers.tasks.reconstruct import reconstruct as reconstruct_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/parametric-blocks", tags=["parametric-blocks"])

# 0-20 photos: the photo endpoint enforces 4-20, but the parametric
# path is often zero-image. 20 is a hard cap (matches the photo
# endpoint's upper bound) so the MinIO + DB write paths stay
# uniform across the two endpoints.
MIN_PHOTOS = 0
MAX_PHOTOS = 20
ALLOWED_CONTENT_TYPES = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
}

# Mirrors the public surface of tools/measure_block.py so the route
# layer and the CLI produce identical numbers.
_RAW_KEYS: tuple[str, ...] = (
    "outer_pitch_mm",
    "inner_pitch_mm",
    "stud_diameter_mm",
    "brick_height_net_mm",
    "brick_height_total_mm",
)
# Cross-check tolerance — same as tools/measure_block.py
# (CROSS_CHECK_TOL_MM). When the direct ③ reading and the
# (1A - 1B)/2 derivation differ by more than this, we tag a warning.
_CROSS_CHECK_TOL_MM: float = 0.5


def _derive_spec_from_raw(raw: dict[str, float]) -> dict[str, float]:
    """Map 5 caliper measurements → 4 ``BlockSpec`` override fields.

    Mirrors :func:`tools.measure_block.derive_spec_from_raw` verbatim
    — duplicated here (3 trivial lines) so the API doesn't import
    the CLI tool (``tools/`` is not on the import path of the API
    package).
    """
    unit = (raw["outer_pitch_mm"] + raw["inner_pitch_mm"]) / 2.0
    knob_d = raw["stud_diameter_mm"]
    height = raw["brick_height_net_mm"]
    knob_h = raw["brick_height_total_mm"] - raw["brick_height_net_mm"]
    return {
        "unit_mm": unit,
        "knob_diameter_mm": knob_d,
        "height_mm": height,
        "knob_height_mm": knob_h,
    }


def _cross_check_raw(raw: dict[str, float]) -> list[str]:
    """Return a list of warning strings; empty list = clean.

    Mirrors :func:`tools.measure_block.cross_check_raw` verbatim.
    """
    warnings: list[str] = []
    if raw["outer_pitch_mm"] <= raw["inner_pitch_mm"]:
        warnings.append(
            f"1A ({raw['outer_pitch_mm']}) should be > 1B ({raw['inner_pitch_mm']}); got 1A <= 1B."
        )
    if raw["inner_pitch_mm"] <= 0:
        warnings.append(f"1B ({raw['inner_pitch_mm']}) must be > 0.")
    if raw["brick_height_total_mm"] <= raw["brick_height_net_mm"]:
        warnings.append(
            f"④ ({raw['brick_height_total_mm']}) should be > ② "
            f"({raw['brick_height_net_mm']}); got ④ <= ②."
        )
    derived_stud_d = (raw["outer_pitch_mm"] - raw["inner_pitch_mm"]) / 2.0
    diff = abs(derived_stud_d - raw["stud_diameter_mm"])
    if diff > _CROSS_CHECK_TOL_MM:
        warnings.append(
            f"stud_Ø cross-check: ③ ({raw['stud_diameter_mm']}) vs "
            f"(1A-1B)/2 ({derived_stud_d:.3f}) differ by {diff:.3f} mm "
            f"(> {_CROSS_CHECK_TOL_MM}); check 1B reading."
        )
    return warnings


def _settings_s3_bucket_raw() -> str:
    """Local helper to keep ``storage.put_object`` lines tidy."""
    from app.config import settings

    return settings.s3_bucket_raw


@router.post(
    "",
    response_model=ParametricBlockRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a measured-only parametric block (no SfM, caliper only)",
    responses={
        201: {"description": "Capture + Job rows created; worker dispatched."},
        422: {"description": "Validation failed (missing fields, bad JSON, out of range)."},
    },
)
async def create_parametric_block(
    session: DBSessionDep,
    system: Annotated[Literal["duplo", "lego", "feile", "generic"], Form()],
    kind: Annotated[Literal["brick", "plate", "tile", "slope"], Form()],
    units_x: Annotated[int, Form(ge=1, le=16)],
    units_y: Annotated[int, Form(ge=1, le=16)],
    raw_measurements_mm: Annotated[
        str,
        Form(
            description=(
                "JSON object with 5 caliper fields: outer_pitch_mm, "
                "inner_pitch_mm, stud_diameter_mm, brick_height_net_mm, "
                "brick_height_total_mm. All in millimetres."
            )
        ),
    ],
    photos: Annotated[
        list[UploadFile],
        File(
            description=(
                f"0-{MAX_PHOTOS} optional reference photos. The "
                "parametric-block worker does NOT consume these for "
                "3D reconstruction; they are kept for visual "
                "side-by-side comparison only."
            )
        ),
    ] = [],  # noqa: B006 — FastAPI's Annotated/File idiom for optional file list
    part_id: Annotated[
        str | None,
        Form(
            max_length=64,
            description=("Logical part id. Default = '{system}-{kind}-{units_x}x{units_y}'."),
        ),
    ] = None,
) -> JSONResponse:
    # ---- 1. Validate photo count --------------------------------------------
    photos_list = photos or []
    n = len(photos_list)
    if n < MIN_PHOTOS or n > MAX_PHOTOS:
        raise CaptureInvalid(
            f"need {MIN_PHOTOS} <= photo_count <= {MAX_PHOTOS}, got {n}",
            details={"photo_count": n, "min": MIN_PHOTOS, "max": MAX_PHOTOS},
        )

    # ---- 2. Parse + validate raw_measurements_mm ---------------------------
    try:
        raw_obj = json.loads(raw_measurements_mm)
    except json.JSONDecodeError as exc:
        raise CaptureInvalid(
            f"raw_measurements_mm is not valid JSON: {exc.msg} (line {exc.lineno})",
            details={"line": exc.lineno, "column": exc.colno},
        ) from exc
    if not isinstance(raw_obj, dict):
        raise CaptureInvalid(
            "raw_measurements_mm must be a JSON object",
            details={"got_type": type(raw_obj).__name__},
        )

    missing = [k for k in _RAW_KEYS if k not in raw_obj]
    if missing:
        raise CaptureInvalid(
            f"raw_measurements_mm missing keys: {missing}",
            details={"missing": missing, "required": list(_RAW_KEYS)},
        )
    # Coerce + range-check
    raw: dict[str, float] = {}
    for k in _RAW_KEYS:
        v = raw_obj[k]
        try:
            v_f = float(v)
        except (TypeError, ValueError) as exc:
            raise CaptureInvalid(
                f"raw_measurements_mm.{k} is not numeric: {v!r}",
                details={"key": k, "value": v},
            ) from exc
        if v_f <= 0:
            raise CaptureInvalid(
                f"raw_measurements_mm.{k} must be > 0, got {v_f}",
                details={"key": k, "value": v_f},
            )
        raw[k] = v_f

    cross_warnings = _cross_check_raw(raw)
    derived = _derive_spec_from_raw(raw)

    # ---- 3. Default part_id -------------------------------------------------
    if not part_id:
        part_id = f"{system}-{kind}-{units_x}x{units_y}"

    # ---- 4. Upload photos (0-20) to MinIO ---------------------------------
    # Allocate the capture id BEFORE the upload loop so the MinIO key
    # is stable (and so the no-photos path still has a valid id).
    capture_id = uuid.uuid4()
    keys: list[str] = []
    for idx, upload in enumerate(photos_list):
        ext = ALLOWED_CONTENT_TYPES.get((upload.content_type or "").lower(), "bin")
        filename = f"ref_{idx:03d}.{ext}"
        # Use a per-capture prefix under a parametric/ sub-tree so
        # the bucket listing is easy to scope. The key shape mirrors
        # the photo endpoint's ``raw_object_key`` prefix convention.
        key = raw_object_key(str(capture_id), filename)
        photo_bytes = await upload.read()
        if not photo_bytes:
            raise CaptureInvalid(
                f"photo #{idx} is empty",
                details={"index": idx, "filename": upload.filename},
            )
        storage.put_object(
            _settings_s3_bucket_raw(),
            key,
            photo_bytes,
            content_type=upload.content_type or "application/octet-stream",
        )
        keys.append(key)
        await upload.close()

    # ---- 5. Persist capture row -------------------------------------------
    capture = Capture(
        id=capture_id,
        part_id=part_id,
        status="pending",
        # ``image_count`` is reused for the count of reference photos
        # so the schema doesn't grow a new column.
        image_count=len(keys),
        image_keys=keys,
        capture_mode="parametric_block",  # phase-2 column; keeps the
        #                                   PWA happy with a non-null
        #                                   value.
        # ---- v0.3 parametric columns (model 0003) ----
        mode="parametric_block",
        system=system,
        kind=kind,
        units_x=units_x,
        units_y=units_y,
        raw_measurements_mm=raw,
        derived_spec_mm=derived,
        cross_check_warnings=cross_warnings,
    )
    session.add(capture)

    # ---- 6. Pre-create job row + dispatch --------------------------------
    job = Job(
        capture_id=capture_id,
        kind="reconstruct",  # same task as the photo path; the worker
        #                       dispatches on capture.mode.
        status="pending",
        progress=0,
        stage="queued",
    )
    session.add(job)
    await session.flush()  # populate job.id

    async_result = reconstruct_task.apply_async(
        args=[str(capture_id)],
        task_id=str(job.id),
    )
    job.celery_task_id = async_result.id

    await session.commit()
    await session.refresh(capture)

    logger.info(
        "parametric_blocks.create: capture_id=%s part_id=%s system=%s kind=%s "
        "units=%dx%d photos=%d cross_warnings=%d job_id=%s",
        capture_id,
        part_id,
        system,
        kind,
        units_x,
        units_y,
        len(keys),
        len(cross_warnings),
        job.id,
    )

    body = ParametricBlockRead(
        capture_id=capture.id,
        part_id=capture.part_id,
        status=capture.status,
        mode="parametric_block",
        system=system,
        kind=kind,
        units_x=units_x,
        units_y=units_y,
        raw_measurements_mm=raw,
        derived_spec_mm=derived,
        cross_check_warnings=cross_warnings,
        job_id=job.id,
        created_at=capture.created_at,
    )
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=body.model_dump(mode="json"),
    )


__all__ = [
    "router",
]
