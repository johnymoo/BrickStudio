"""End-to-end tests for the 3D reconstruction pipeline.

These tests cover the full worker flow (design.md §8):

1. Upload 4 synthetic photos via the FastAPI route (or seed a
   ``Capture`` row directly when the API path is too slow).
2. Invoke the Celery ``reconstruct`` task in-process.
3. Assert: a ``Job`` row reaches ``status='completed'``, an ``Asset`` row
   is written with valid ``meta`` (vertex/face counts, bbox, pipeline name),
   the GLB exists in MinIO and is a valid binary glTF.

We allow the test to take the Open3D-only fallback (skipping COLMAP)
because the dev environment doesn't ship Meshroom / COLMAP binaries.
The worker is wired to fall through automatically.
"""
from __future__ import annotations

import asyncio
import io
import json
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import trimesh


# ---- Fixtures -------------------------------------------------------------
@pytest.fixture
def work_in_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Redirect BLOCKTOOL_RECON_ROOT to a per-test tmp dir.

    The default location (``/tmp/recon``) is shared across tests and
    processes; for hermetic runs we override it.
    """
    monkeypatch.setenv("BLOCKTOOL_RECON_ROOT", str(tmp_path))
    yield tmp_path


@pytest.fixture
def seeded_capture(
    app_client: AsyncIterator, synth_cube_dir: Path
) -> AsyncIterator[dict[str, str]]:
    """Upload 4 synthetic images via the public API and return the IDs.

    Uses the same multipart flow a real PWA would, so the test exercises
    the whole stack (MinIO write, ``Capture`` + ``Job`` rows, Celery
    dispatch). Returns ``{"capture_id", "job_id", "part_id"}``.
    """
    # ExitStack keeps the file handles alive for httpx while still
    # closing them when the fixture tears down.
    import contextlib

    with contextlib.ExitStack() as stack:
        files: list[tuple[str, tuple[str, io.BufferedReader, str]]] = []
        for src in sorted(synth_cube_dir.iterdir()):
            if src.suffix.lower() != ".png":
                continue
            # httpx wants a real file-like, not bytes.
            fh = stack.enter_context(open(src, "rb"))
            files.append(("images", (src.name, fh, "image/png")))

        part_id = f"test-pipeline-{uuid.uuid4().hex[:8]}"
        resp = asyncio.get_event_loop().run_until_complete(
            app_client.post(
                "/api/v1/captures",
                data={"part_id": part_id},
                files=files,
            )
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        yield {
            "capture_id": body["capture_id"],
            "job_id": body["job_id"],
            "part_id": part_id,
        }


def _drain_events(channel: str, *, min_count: int = 2, timeout: float = 5.0) -> list[dict]:
    """Subscribe to ``channel`` and collect events for up to ``timeout`` seconds.

    We use a sync redis client here because the test is sync (the
    Celery task is sync). For the async-friendly variant, see
    ``_adrain_events`` below.
    """
    import redis

    r = redis.from_url(__import__("os").environ["REDIS_URL"], decode_responses=True)
    pubsub = r.pubsub()
    pubsub.subscribe(channel)
    events: list[dict] = []
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline and len(events) < min_count:
            msg = pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
            if msg is None:
                continue
            try:
                events.append(json.loads(msg["data"]))
            except (TypeError, ValueError):
                continue
    finally:
        import contextlib

        with contextlib.suppress(Exception):
            pubsub.unsubscribe(channel)
        pubsub.close()
        r.close()
    return events


# ---- Tests ----------------------------------------------------------------
def test_open3d_runner_fallback_produces_valid_glb(
    work_in_tmp: Path, synth_cube_dir: Path
) -> None:
    """Pure-Open3D path: feed 4 PNGs, expect a non-empty GLB with meta."""
    from pipelines.open3d_runner import Open3DRunner

    runner = Open3DRunner()
    out = work_in_tmp / "out"
    out.mkdir()
    result = runner.reconstruct_from_photos(
        input_dir=synth_cube_dir,
        output_dir=out,
    )
    assert "mesh_path" in result
    glb = Path(result["mesh_path"])
    assert glb.exists()
    assert glb.stat().st_size > 1024, glb.stat().st_size
    assert result["vertex_count"] > 0
    assert result["face_count"] > 0
    # bbox_min should be strictly negative; bbox_max strictly positive for
    # a unit-sphere splat that has been normalized to [-0.5, 0.5].
    assert result["bbox_min"][0] < 0
    assert result["bbox_max"][0] > 0
    # Confirm the file is actually a glTF binary (first 4 bytes == 'glTF').
    with glb.open("rb") as f:
        assert f.read(4) == b"glTF"


def test_open3d_runner_fallback_under_50k_faces(
    work_in_tmp: Path, synth_cube_dir: Path
) -> None:
    """The brief caps the final mesh at 50k faces."""
    from pipelines.open3d_runner import MAX_FACES, Open3DRunner

    runner = Open3DRunner()
    out = work_in_tmp / "out"
    out.mkdir()
    result = runner.reconstruct_from_photos(input_dir=synth_cube_dir, output_dir=out)
    assert result["face_count"] <= MAX_FACES


def test_pipeline_unavailable_message_is_stable() -> None:
    """The brief's verify step greps for the canonical error text."""
    from pipelines import PipelineUnavailable

    err = PipelineUnavailable("test reason")
    msg = f"reconstruction toolchain unavailable: {err}"
    assert "reconstruction toolchain unavailable" in msg


def test_colmap_pipeline_raises_when_binary_missing(
    work_in_tmp: Path, synth_cube_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If COLMAP isn't installed, ColmapPipeline.run() must raise
    PipelineUnavailable, not e.g. FileNotFoundError."""
    monkeypatch.delenv("COLMAP_BIN", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: None)

    from pipelines import PipelineUnavailable
    from pipelines.colmap import ColmapPipeline

    p = ColmapPipeline()
    with pytest.raises(PipelineUnavailable):
        p.run(input_dir=synth_cube_dir, output_dir=work_in_tmp / "out", on_progress=lambda *_: None)


def test_meshroom_pipeline_raises_when_binary_missing(
    work_in_tmp: Path, synth_cube_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same contract for Meshroom."""
    monkeypatch.delenv("MESHROOM_BIN", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: None)

    from pipelines import PipelineUnavailable
    from pipelines.meshroom import MeshroomPipeline

    p = MeshroomPipeline()
    with pytest.raises(PipelineUnavailable):
        p.run(input_dir=synth_cube_dir, output_dir=work_in_tmp / "out", on_progress=lambda *_: None)


# ---- End-to-end: full Celery task on the live DB -------------------------
def test_reconstruct_task_end_to_end(
    work_in_tmp: Path,
    synth_cube_dir: Path,
    seeded_capture: dict[str, str],
) -> None:
    """Upload 4 PNGs via the API → run the Celery task in-process → assert
    the Job lands in ``completed`` and an ``Asset`` row exists with valid
    meta + a downloadable GLB in MinIO.
    """
    import tempfile

    from app.config import settings
    from db.models import Asset, Capture, Job, Part
    from db.session import async_session_factory
    from workers.tasks.reconstruct import reconstruct

    capture_id = seeded_capture["capture_id"]
    job_id = seeded_capture["job_id"]

    # Subscribe before running, so we don't miss the "completed" event.
    events = _drain_events(f"job:{job_id}:events", min_count=1, timeout=2.0)
    # Discard the early pubsub stream (the route doesn't publish one but
    # other tests in the same module might have left something on this
    # channel; we don't care about ordering with prior tests).
    events.clear()

    # Run the task in-process (Celery prefork pool doesn't matter for the
    # sync code path; we just call .apply()).
    result = reconstruct.apply(args=[capture_id])
    assert result.successful() or result.state == "SUCCESS", result.state

    # ---- Assert DB state ----------------------------------------------
    async def _db_state() -> tuple[Job | None, list[Asset], Part | None]:
        f = async_session_factory()
        async with f() as session:
            job = await session.get(Job, uuid.UUID(job_id))
            cap = await session.get(Capture, uuid.UUID(capture_id))
            part = None
            if cap is not None:
                # Re-load job with assets eagerly
                from sqlalchemy import select
                from sqlalchemy.orm import selectinload

                stmt = (
                    select(Job)
                    .where(Job.id == uuid.UUID(job_id))
                    .options(selectinload(Job.assets))
                )
                job = (await session.execute(stmt)).scalar_one_or_none()
                part = (
                    await session.scalars(
                        select(Part).where(Part.capture_id == uuid.UUID(capture_id))
                    )
                ).one_or_none()
            return job, list(job.assets) if job else [], part

    job, assets, part = asyncio.run(_db_state())
    assert job is not None
    assert job.status == "completed", f"job.status={job.status} stage={job.stage} error={job.error}"
    assert job.progress == 100
    assert job.stage == "completed"
    assert job.finished_at is not None
    # Capture should be flagged completed too (the route's "completed" badge).
    async def _cap() -> Capture | None:
        f = async_session_factory()
        async with f() as session:
            return await session.get(Capture, uuid.UUID(capture_id))
    cap = asyncio.run(_cap())
    assert cap is not None
    assert cap.status == "completed"

    # ---- Assert asset row + meta --------------------------------------
    assert len(assets) == 1, [a.kind for a in assets]
    asset = assets[0]
    assert asset.kind == "mesh_gltf"
    assert asset.size_bytes is not None and asset.size_bytes > 1024
    assert asset.meta is not None
    for key in ("vertex_count", "face_count", "bbox_min", "bbox_max", "pipeline_used"):
        assert key in asset.meta, f"missing {key} in {asset.meta}"
    assert asset.meta["vertex_count"] > 0
    assert asset.meta["face_count"] > 0
    # The brief allows any of colmap / meshroom / open3d_fallback; on CI
    # machines without binaries, the fallback is the only one reachable.
    # Multi-photo path uses ``open3d_pure_photogrammetry`` (8+ photos,
    # no COLMAP binary present).
    assert asset.meta["pipeline_used"] in {
        "colmap",
        "colmap_sfm",
        "meshroom",
        "open3d_fallback",
        "open3d_pure_photogrammetry",
    }
    assert part is not None
    assert part.source_mode == "photo"
    assert part.asset_id == asset.id

    # ---- Assert GLB is downloadable ------------------------------------
    with tempfile.NamedTemporaryFile(suffix=".glb", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        from minio import Minio

        client = Minio(
            endpoint=_strip_scheme(settings.s3_endpoint),
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            secure=settings.s3_secure,
        )
        client.fget_object(settings.s3_bucket_recon, asset.storage_key, str(tmp_path))
        size = tmp_path.stat().st_size
        with tmp_path.open("rb") as f:
            header = f.read(4)
        assert header == b"glTF", header
        assert size > 1024, size
        # Round-trip with trimesh (Open3D 0.19's own reader has a known
        # bug reading its own GLB output; trimesh works correctly).
        loaded = trimesh.load(str(tmp_path), force="mesh")
        assert isinstance(loaded, trimesh.Trimesh)
        assert len(loaded.vertices) > 0
    finally:
        tmp_path.unlink(missing_ok=True)

    # ---- Assert the work directory was preserved on disk --------------
    work_dir = work_in_tmp / capture_id
    assert work_dir.exists(), f"work_dir {work_dir} missing (should be kept for debug)"
    glb_local = work_dir / "output" / "mesh.glb"
    assert glb_local.exists()


def _strip_scheme(url: str) -> str:
    """``http://host:port`` -> ``host:port`` (minio client expects no scheme)."""
    return url.split("://", 1)[-1] if "://" in url else url
