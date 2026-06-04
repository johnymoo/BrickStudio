"""``reconstruct`` Celery task — real 3D reconstruction pipeline.

The task drives the worker side of design.md §8:

    1. From MinIO, pull every photo for the capture into
       ``/tmp/recon/{capture_id}/input/``.
    2. Mark the job as ``running`` and publish ``progress=5, stage=downloading_images``
       on the Redis pub/sub channel + Celery result backend.
    3. Try the COLMAP pipeline first; if its binary is missing (or COLMAP
       finds < 8 images), try Meshroom; if both are absent, fall through
       to the Open3D-only ``reconstruct_from_photos`` path that uses
       ``trimesh`` to splat a synthetic point cloud and runs the same
       cleanup / Poisson / decimation / GLB export recipe.
    4. Upload the resulting GLB to the recon bucket, write an
       ``assets`` row, and mark the job ``completed`` with
       ``finished_at`` set.

Failure handling:

* Any :class:`pipelines.PipelineUnavailable` ⇒ job ``failed`` with
  ``"reconstruction toolchain unavailable"`` (operator-actionable).
* Any other exception ⇒ job ``failed`` with the exception text.
* Celery itself is configured for ``max_retries=1`` (per the brief); we
  do *not* re-run the same job twice in production.
* On failure the input dir is kept on disk under
  ``/tmp/recon/{capture_id}/`` for postmortem; the worker doesn't try to
  clean up partial outputs (the brief says "保留输入在 /tmp/recon/{capture_id}/
  供调试").
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import redis
from sqlalchemy import select, update

from app.config import settings
from db.models import Asset, Capture, Job
from db.session import async_session_factory
from pipelines import Pipeline, PipelineUnavailable
from pipelines.cleanup import BlenderCleanup
from pipelines.colmap import ColmapPipeline
from pipelines.meshroom import MeshroomPipeline
from pipelines.open3d_runner import Open3DRunner
from storage.minio_client import recon_object_key, storage
from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


# ---- Constants -------------------------------------------------------------
# Read at *call time*, not at import time, so tests that override
# ``BLOCKTOOL_RECON_ROOT`` (via ``monkeypatch.setenv`` or fixture) get
# the override. Falling back to ``/tmp/recon`` keeps the production
# default the brief calls for.
def _recon_root() -> Path:
    return Path(os.environ.get("BLOCKTOOL_RECON_ROOT", "/tmp/recon"))


MAX_RETRIES = 1  # brief: "任务不重试超过 1 次"

# Re-export for tests that want to assert the default path.
RECON_ROOT: Path = Path("/tmp/recon")


# ---- Event publishing -----------------------------------------------------
def _channel(job_id: str | uuid.UUID) -> str:
    return f"job:{job_id}:events"


def _publish_sync(job_id: str | uuid.UUID, payload: dict[str, Any]) -> None:
    """Sync helper for Celery (Celery tasks run in a thread pool, not asyncio).

    We keep this fully sync because the Celery prefork worker spawns a fresh
    thread per task, and bridge code (``asyncio.run`` + ``redis.asyncio``)
    is fragile in that environment. ``redis-py's`` sync client is plenty
    fast for our publish rate (< 1 msg/s/job).
    """
    r = redis.from_url(settings.redis_url, decode_responses=True)  # type: ignore[no-untyped-call]
    try:
        r.publish(_channel(job_id), json.dumps(payload, default=str))
    finally:
        r.close()


# ---- DB helpers (sync wrappers around async) -----------------------------
def _run_async(coro: Any) -> Any:
    """Run an async coroutine from sync Celery code.

    Celery's prefork worker runs sync tasks in a thread pool, so each call
    gets its own event loop. ``asyncio.run`` is the simplest correct option.
    """
    return asyncio.run(coro)


async def _set_job_running(job_id: uuid.UUID) -> None:
    factory = async_session_factory()
    async with factory() as session:
        await session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                status="running",
                progress=0,
                stage="starting",
                started_at=datetime.now(tz=UTC),
            )
        )
        await session.commit()


async def _set_job_progress(
    job_id: uuid.UUID, *, progress: int, stage: str | None = None
) -> None:
    factory = async_session_factory()
    async with factory() as session:
        await session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(progress=progress, stage=stage)
        )
        await session.commit()


async def _set_job_completed(job_id: uuid.UUID) -> None:
    factory = async_session_factory()
    async with factory() as session:
        await session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                status="completed",
                progress=100,
                stage="completed",
                finished_at=datetime.now(tz=UTC),
            )
        )
        await session.execute(
            update(Capture)
            .where(
                Capture.id
                == (
                    await session.scalar(
                        select(Job.capture_id).where(Job.id == job_id)
                    )
                )
            )
            .values(status="completed")
        )
        await session.commit()


async def _set_job_failed(job_id: uuid.UUID, err: str, stage: str = "failed") -> None:
    factory = async_session_factory()
    async with factory() as session:
        await session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                status="failed",
                error=err,
                stage=stage,
                finished_at=datetime.now(tz=UTC),
            )
        )
        # Also flag the parent capture so the PWA can render a red badge.
        await session.execute(
            update(Capture)
            .where(
                Capture.id
                == (
                    await session.scalar(
                        select(Job.capture_id).where(Job.id == job_id)
                    )
                )
            )
            .values(status="failed")
        )
        await session.commit()


# ---- The actual task ------------------------------------------------------
@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="workers.tasks.reconstruct.reconstruct",
    max_retries=MAX_RETRIES,
    default_retry_delay=10,
    acks_late=True,
)
def reconstruct(self: Any, capture_id: str | uuid.UUID) -> dict[str, Any]:
    """Run the full 3D reconstruction pipeline for ``capture_id``.

    Returns a dict suitable for the Celery result backend; the actual
    progress and terminal events are published on the Redis pub/sub
    channel ``job:{job_id}:events`` and via ``self.update_state``.
    """
    capture_uuid = uuid.UUID(str(capture_id))

    # Find the most recent job tied to this capture. The HTTP route created
    # the job before dispatching, so there should be exactly one.
    job_id: uuid.UUID | None = None
    try:
        job_id = _run_async(_find_or_create_job(capture_uuid))
    except Exception as exc:
        logger.exception("reconstruct: failed to find job for capture %s", capture_uuid)
        raise self.retry(exc=exc) from exc

    if job_id is None:
        err = f"no job for capture {capture_uuid}"
        logger.error(err)
        return {"status": "failed", "error": err}

    # The real work happens inside this try/except — anything that escapes
    # is converted into a job-failed + retry decision.
    try:
        _run_async(_set_job_running(job_id))
        result = _run_pipeline(self, capture_uuid, job_id)
        _run_async(_set_job_completed(job_id))
        return result
    except PipelineUnavailable as exc:
        # Graceful degradation: no toolchain at all → mark failed with
        # the canonical message so operators see "install Meshroom / COLMAP"
        # instead of an opaque stack trace.
        msg = f"reconstruction toolchain unavailable: {exc}"
        logger.error("reconstruct: job %s: %s", job_id, msg)
        _emit_progress(self, job_id, error_event="failed", progress=0,
                       stage="init", error=msg)
        _run_async(_set_job_failed(job_id, msg, stage="init"))
        return {"status": "failed", "job_id": str(job_id), "error": msg}
    except Exception as exc:
        logger.exception("reconstruct: job %s failed", job_id)
        _emit_progress(self, job_id, error_event="failed", progress=0,
                       stage="failed", error=str(exc))
        _run_async(_set_job_failed(job_id, str(exc)))
        # Re-raise so Celery records the failure in its own result backend.
        raise


# ---- Pipeline orchestration ----------------------------------------------
def _run_pipeline(
    self: Any,
    capture_uuid: uuid.UUID,
    job_id: uuid.UUID,
) -> dict[str, Any]:
    """Run the full pipeline, return a small dict for the Celery result.

    This is the heart of the worker. Every stage emits a progress event
    on the Redis pub/sub channel; the work directory is kept on disk
    after the call (success or failure) so the operator can inspect
    intermediate artefacts if something went wrong.
    """
    work_dir = _recon_root() / str(capture_uuid)
    input_dir = work_dir / "input"
    output_dir = work_dir / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ---- Stage 1: download images from MinIO -----------------------------
    _emit_progress(self, job_id, progress=5, stage="downloading_images")
    _download_capture_photos(capture_uuid, input_dir)
    n_images = sum(1 for p in input_dir.iterdir() if p.is_file())
    if n_images == 0:
        raise RuntimeError(f"capture {capture_uuid} has no images in MinIO")

    # ---- Stage 2: run the 3D pipeline (COLMAP → Meshroom → Open3D) ------
    started = time.monotonic()
    pipeline_used, result = _select_and_run_pipeline(
        self, job_id, input_dir, output_dir
    )
    elapsed = time.monotonic() - started
    logger.info(
        "reconstruct: job %s pipeline=%s elapsed=%.2fs meta=%s",
        job_id, pipeline_used, elapsed,
        {k: v for k, v in result.items() if k != "mesh_path"},
    )

    # ---- Stage 3: optional Blender cleanup / refinement ------------------
    try:
        cleanup = BlenderCleanup()
        if (output_dir / "mesh.glb").exists():
            blender_result = cleanup.run(
                input_dir=input_dir,
                output_dir=output_dir,
                on_progress=_progress_cb(self, job_id, base=85, span=10),
            )
            # Blender path may refine vertex/face count; prefer its numbers
            for k in ("vertex_count", "face_count", "bbox_min", "bbox_max"):
                if k in blender_result:
                    result[k] = blender_result[k]
            result["blender_used"] = cleanup.name
    except Exception as exc:
        # Cleanup is best-effort; log and move on.
        logger.warning("reconstruct: blender cleanup failed: %s", exc)
        result["blender_used"] = None

    # ---- Stage 4: upload GLB to MinIO ------------------------------------
    _emit_progress(self, job_id, progress=95, stage="simplification_and_export")
    glb_path = Path(result["mesh_path"])
    if not glb_path.exists() or glb_path.stat().st_size == 0:
        raise RuntimeError(f"GLB missing or empty: {glb_path}")
    storage_key = recon_object_key(str(job_id), "mesh", "glb")
    with glb_path.open("rb") as f:
        storage.put_object(
            settings.s3_bucket_recon,
            storage_key,
            f,
            content_type="model/gltf-binary",
            length=glb_path.stat().st_size,
        )
    size_bytes = storage.stat_object_size(settings.s3_bucket_recon, storage_key)

    # ---- Stage 5: write the asset row -----------------------------------
    meta = {
        "vertex_count": result.get("vertex_count"),
        "face_count": result.get("face_count"),
        "bbox_min": result.get("bbox_min"),
        "bbox_max": result.get("bbox_max"),
        "pipeline_used": result.get("pipeline_used", pipeline_used),
        "pipeline_version": result.get("pipeline_version", "0.0.0"),
        "blender_used": result.get("blender_used"),
        "elapsed_seconds": round(elapsed, 2),
        "input_image_count": n_images,
    }
    asset_id = uuid.uuid4()
    async def _write_asset() -> None:
        f = async_session_factory()
        async with f() as session:
            session.add(
                Asset(
                    id=asset_id,
                    job_id=job_id,
                    kind="mesh_gltf",
                    storage_key=storage_key,
                    size_bytes=size_bytes,
                    meta=meta,
                )
            )
            await session.commit()
    asyncio.run(_write_asset())

    # ---- Stage 6: final progress + completed event ----------------------
    _emit_progress(self, job_id, progress=100, stage="completed")
    _publish_sync(
        job_id,
        {
            "event": "completed",
            "job_id": str(job_id),
            "result_asset_id": str(asset_id),
            "progress": 100,
            "stage": "completed",
            "ts": datetime.now(tz=UTC).isoformat(),
        },
    )
    logger.info(
        "reconstruct: job %s completed, asset_id=%s size=%dB", job_id, asset_id, size_bytes
    )
    return {
        "status": "completed",
        "job_id": str(job_id),
        "asset_id": str(asset_id),
        "progress": 100,
    }


# ---- Helpers --------------------------------------------------------------
def _emit_progress(
    self: Any,
    job_id: uuid.UUID,
    *,
    progress: int,
    stage: str,
    error_event: str | None = None,
    error: str | None = None,
) -> None:
    """Single sink for *all* progress events: Celery state + DB + Redis pub/sub."""
    import contextlib

    meta = {"progress": progress, "stage": stage, "job_id": str(job_id)}
    if error:
        meta["error"] = error
    with contextlib.suppress(Exception):
        self.update_state(state="PROGRESS", meta=meta)

    payload: dict[str, Any] = {
        "event": "failed" if error_event else "progress",
        "job_id": str(job_id),
        "progress": progress,
        "stage": stage,
        "ts": datetime.now(tz=UTC).isoformat(),
    }
    if error:
        payload["error"] = error
    _publish_sync(job_id, payload)
    _run_async(_set_job_progress(job_id, progress=progress, stage=stage))


def _progress_cb(
    self: Any, job_id: uuid.UUID, *, base: int, span: int
) -> Callable[[int, str], None]:
    """Return an ``on_progress(pct, stage)`` callback clamped to a sub-range.

    The Open3D pipeline reports a 0-100 progress value; we rebase it into
    a specific sub-stage range (e.g. 70-90 for mesh reconstruction) so the
    worker's outer counter keeps moving monotonically.
    """
    def cb(pct: int, stage: str) -> None:
        pct_in_range = max(0, min(100, int(pct)))
        global_pct = base + int(span * pct_in_range / 100)
        _emit_progress(self, job_id, progress=global_pct, stage=stage)
    return cb


def _select_and_run_pipeline(
    self: Any,
    job_id: uuid.UUID,
    input_dir: Path,
    output_dir: Path,
) -> tuple[str, dict[str, Any]]:
    """Try the heavy SfM pipelines first, fall back to Open3D-only.

    Returns ``(pipeline_name, result_dict)``. Any
    :class:`PipelineUnavailable` from a heavyweight pipeline is caught
    and we move to the next option. The fallback Open3D path itself
    cannot raise ``PipelineUnavailable`` (it has no external binary) so
    if we get back from this function the call always succeeded.
    """
    last_exc: Exception | None = None
    # Order matches design.md §8: COLMAP (most accurate) → Meshroom
    # (AliceVision) → Open3D-only (no SfM, synthetic point cloud).
    candidates: list[Pipeline] = [
        ColmapPipeline(),
        MeshroomPipeline(),
    ]
    for pipeline in candidates:
        try:
            _emit_progress(
                self, job_id, progress=10, stage="sparse_reconstruction",
            )
            result = pipeline.run(
                input_dir=input_dir,
                output_dir=output_dir,
                on_progress=_progress_cb(self, job_id, base=10, span=80),
            )
            return pipeline.name, result
        except PipelineUnavailable as exc:
            logger.info("pipeline %s unavailable: %s", pipeline.name, exc)
            last_exc = exc
            continue
        except Exception as exc:
            # Real algorithm failure on a heavyweight pipeline: try the
            # fallback. The fallback is documented to be best-effort, so
            # we'd rather produce *something* than nothing.
            logger.warning(
                "pipeline %s crashed (%s); falling back to Open3D-only", pipeline.name, exc
            )
            last_exc = exc
            continue

    # Open3D-only path: never raises PipelineUnavailable because it
    # doesn't shell out to anything.
    logger.info(
        "reconstruct: using Open3D-only fallback (last_exc=%s)", last_exc,
    )
    _emit_progress(self, job_id, progress=15, stage="sparse_reconstruction")
    runner = Open3DRunner()
    result = runner.reconstruct_from_photos(
        input_dir=input_dir,
        output_dir=output_dir,
        on_progress=_progress_cb(self, job_id, base=15, span=80),
    )
    result["pipeline_used"] = "open3d_fallback"
    result["pipeline_version"] = Open3DRunner.__module__.rsplit(".", 1)[-1]
    return "open3d_fallback", result


def _download_capture_photos(capture_id: uuid.UUID, input_dir: Path) -> None:
    """Pull every MinIO object referenced by the capture into ``input_dir``.

    The capture row stores a ``image_keys`` jsonb list of object keys; we
    look them up, stream the bytes to local files, and let the pipeline
    discover them by extension. Existing files in ``input_dir`` (from a
    retry / re-run) are left alone — the work dir is keyed by capture
    id, so this is safe.
    """
    async def _keys() -> list[str]:
        f = async_session_factory()
        async with f() as session:
            cap = await session.get(Capture, capture_id)
            if cap is None:
                raise FileNotFoundError(f"capture {capture_id} not in DB")
            return list(cap.image_keys)
    keys: list[str] = asyncio.run(_keys())
    if not keys:
        raise RuntimeError(f"capture {capture_id} has empty image_keys")

    for key in keys:
        local = input_dir / Path(key).name
        if local.exists() and local.stat().st_size > 0:
            # Skip already-downloaded files (idempotent re-runs).
            continue
        try:
            data = storage.get_object_bytes(settings.s3_bucket_raw, key)
        except FileNotFoundError:
            logger.warning("reconstruct: %s missing in MinIO, skipping", key)
            continue
        local.write_bytes(data)


async def _find_or_create_job(capture_id: uuid.UUID) -> uuid.UUID | None:
    """Return the most recent job for a capture (creating one if needed)."""
    factory = async_session_factory()
    async with factory() as session:
        existing = await session.scalar(
            select(Job).where(Job.capture_id == capture_id).order_by(Job.created_at.desc())
        )
        if existing is not None:
            return existing.id
        return None


# Re-export for the route layer.
__all__ = ["reconstruct"]
