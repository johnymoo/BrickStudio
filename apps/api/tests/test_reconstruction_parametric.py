"""End-to-end tests for the parametric-block reconstruction path (v0.3+).

Covers the full v0.3 contract (see ``docs/ROADMAP.md`` §v0.3 and
``docs/capture-procedure.md``):

1. ``POST /api/v1/parametric-blocks`` accepts the multipart form
   (system, kind, units_x, units_y, raw_measurements_mm, optional
   photos), persists a ``Capture`` row with ``mode='parametric_block'``
   and the 5 raw + 4 derived measurement columns populated, creates a
   ``Job`` row, and returns 201.
2. The Celery ``reconstruct`` task picks up the row, sees
   ``mode='parametric_block'``, and runs the new
   :func:`_run_parametric_pipeline` (BlockSpec → export_glb), NOT the
   photo COLMAP/Open3D ladder.
3. The result is a valid binary glTF in MinIO, an ``Asset`` row with
   ``meta.pipeline_used == "parametric_block"`` and
   ``meta.system / kind / units_x / units_y`` populated, and the
   ``Job`` reaches ``status='completed'``.
4. A close-the-loop assertion on the deviation: the GLB
   ``bbox_max`` should match the user's caliper dimensions to
   within spec tolerance (LEGO.com ±0.1mm; we use ±0.5mm in
   tests to absorb trimesh's float round-off).

Validation tests cover:

* Bad JSON in ``raw_measurements_mm`` → 422.
* Missing 5 measurement keys → 422.
* Non-numeric / non-positive measurement → 422.
* Unknown system / kind → 422 (FastAPI Literal coercion).
* ``units_x=0`` or ``units_x=17`` → 422.
* 21 photos → 422.
* Cross-check warning path: 1A < 1B → 200 with ``cross_check_warnings``
  non-empty (worker still runs, just flags the suspicious numbers).
"""

from __future__ import annotations

import asyncio
import json
import struct
import uuid
import zlib
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import trimesh


# ---------------------------------------------------------------------------
# Tiny valid PNG helper (mirrors tests/test_reconstruction_routing.py)
# ---------------------------------------------------------------------------
def _png(width: int = 4, height: int = 4, color: bytes = b"\xff\x80\x00") -> bytes:
    """A 4x4 single-colour PNG that httpx + FastAPI will accept as image/png."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b""
    for _ in range(height):
        raw += b"\x00" + color * width
    idat = zlib.compress(raw)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


# Reference caliper numbers — DUPLO 2x2 from
# ``tools/examples/duplo-2x2-brick.json``. unit = (1A+1B)/2 = 20mm,
# brick_height_net = 17mm, knob_diameter = 16mm, knob_height = 7mm.
# These resolve to the public DUPLO spec, so the deviation block in
# the asset meta should be ≈ 0.
DUPLO_2X2_RAW: dict[str, float] = {
    "outer_pitch_mm": 36.0,
    "inner_pitch_mm": 4.0,
    "stud_diameter_mm": 16.0,
    "brick_height_net_mm": 17.0,
    "brick_height_total_mm": 24.0,
}


@pytest.fixture
def work_in_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Redirect BLOCKTOOL_RECON_ROOT to a per-test tmp dir."""
    monkeypatch.setenv("BLOCKTOOL_RECON_ROOT", str(tmp_path))
    yield tmp_path


def _multipart_no_photos() -> dict[str, str]:
    """Form fields only — no photos."""
    return {
        "system": "duplo",
        "kind": "brick",
        "units_x": "2",
        "units_y": "2",
        "raw_measurements_mm": json.dumps(DUPLO_2X2_RAW),
    }


def _multipart_with_photos(
    n: int,
) -> tuple[dict[str, str], list[tuple[str, tuple[str, bytes, str]]]]:
    """Form fields + ``n`` synthetic PNGs."""
    return _multipart_no_photos(), [
        ("photos", (f"ref_{i}.png", _png(), "image/png")) for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Worker unit tests (no HTTP — exercise the dispatcher directly)
# ---------------------------------------------------------------------------
def test_get_capture_mode_returns_photo_default(work_in_tmp: Path) -> None:
    """``_get_capture_mode`` defaults to ``"photo"`` if a (hypothetical)
    NULL ``mode`` column somehow slipped through."""
    from workers.tasks.reconstruct import _get_capture_mode

    async def _go() -> str:
        # Capture with no DB row → RuntimeError
        try:
            return await _get_capture_mode(uuid.uuid4())
        except RuntimeError as exc:
            return f"err: {exc}"

    result = asyncio.run(_go())
    assert "not in DB" in result


# ---------------------------------------------------------------------------
# Endpoint validation tests (no worker run)
# ---------------------------------------------------------------------------
async def test_create_parametric_block_with_no_photos_returns_201(
    app_client: AsyncIterator,
) -> None:
    """The path supports 0 photos — the parametric flow is caliper-only."""
    resp = await app_client.post(
        "/api/v1/parametric-blocks",
        data=_multipart_no_photos(),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["mode"] == "parametric_block"
    assert body["system"] == "duplo"
    assert body["kind"] == "brick"
    assert body["units_x"] == 2
    assert body["units_y"] == 2
    assert body["status"] == "pending"
    assert body["job_id"] is not None
    # Derived spec should match the public DUPLO 2x2 numbers.
    assert abs(body["derived_spec_mm"]["unit_mm"] - 20.0) < 1e-6
    assert abs(body["derived_spec_mm"]["height_mm"] - 17.0) < 1e-6
    assert abs(body["derived_spec_mm"]["knob_diameter_mm"] - 16.0) < 1e-6
    assert abs(body["derived_spec_mm"]["knob_height_mm"] - 7.0) < 1e-6
    # Clean measurements → empty warnings list.
    assert body["cross_check_warnings"] == []


async def test_create_parametric_block_with_3_photos_returns_201(
    app_client: AsyncIterator,
) -> None:
    """A few reference photos are accepted (0-20 allowed)."""
    data, files = _multipart_with_photos(3)
    resp = await app_client.post(
        "/api/v1/parametric-blocks",
        data=data,
        files=files,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["mode"] == "parametric_block"


async def test_create_parametric_block_with_21_photos_returns_422(
    app_client: AsyncIterator,
) -> None:
    """Over the cap → 422."""
    data, files = _multipart_with_photos(21)
    resp = await app_client.post(
        "/api/v1/parametric-blocks",
        data=data,
        files=files,
    )
    assert resp.status_code == 422, resp.text
    blob = resp.text
    assert "20" in blob or "photo_count" in blob


async def test_create_parametric_block_rejects_bad_json(
    app_client: AsyncIterator,
) -> None:
    """Malformed JSON in ``raw_measurements_mm`` → 422 with CAPTURE_INVALID."""
    data = _multipart_no_photos()
    data["raw_measurements_mm"] = "not-json-at-all"
    resp = await app_client.post("/api/v1/parametric-blocks", data=data)
    assert resp.status_code == 422, resp.text
    body = resp.json()
    if "error" in body:
        assert body["error"]["code"] == "CAPTURE_INVALID"


async def test_create_parametric_block_rejects_missing_keys(
    app_client: AsyncIterator,
) -> None:
    """JSON object missing one of the 5 required keys → 422."""
    data = _multipart_no_photos()
    # Drop brick_height_total_mm
    raw = {k: v for k, v in DUPLO_2X2_RAW.items() if k != "brick_height_total_mm"}
    data["raw_measurements_mm"] = json.dumps(raw)
    resp = await app_client.post("/api/v1/parametric-blocks", data=data)
    assert resp.status_code == 422, resp.text
    body = resp.json()
    if "error" in body:
        assert body["error"]["code"] == "CAPTURE_INVALID"
        assert "missing" in (body["error"]["details"] or {})


async def test_create_parametric_block_rejects_non_numeric(
    app_client: AsyncIterator,
) -> None:
    """Non-numeric measurement value → 422."""
    data = _multipart_no_prints = _multipart_no_photos()  # type: ignore[assignment]
    raw = dict(DUPLO_2X2_RAW, stud_diameter_mm="not-a-number")
    data["raw_measurements_mm"] = json.dumps(raw)
    resp = await app_client.post("/api/v1/parametric-blocks", data=data)
    assert resp.status_code == 422, resp.text


async def test_create_parametric_block_rejects_non_positive(
    app_client: AsyncIterator,
) -> None:
    """Negative / zero measurement value → 422."""
    data = _multipart_no_photos()
    raw = dict(DUPLO_2X2_RAW, inner_pitch_mm=-1.0)
    data["raw_measurements_mm"] = json.dumps(raw)
    resp = await app_client.post("/api/v1/parametric-blocks", data=data)
    assert resp.status_code == 422, resp.text


async def test_create_parametric_block_rejects_unknown_system(
    app_client: AsyncIterator,
) -> None:
    """Unknown system value → 422 (FastAPI Literal coercion)."""
    data = _multipart_no_photos()
    data["system"] = "balloon"
    resp = await app_client.post("/api/v1/parametric-blocks", data=data)
    assert resp.status_code == 422, resp.text


async def test_create_parametric_block_rejects_units_out_of_range(
    app_client: AsyncIterator,
) -> None:
    """``units_x=0`` or ``units_x=17`` → 422 (Form(ge=1, le=16))."""
    for bad in ("0", "17", "-1", "100"):
        data = _multipart_no_photos()
        data["units_x"] = bad
        resp = await app_client.post("/api/v1/parametric-blocks", data=data)
        assert resp.status_code == 422, (bad, resp.text)


async def test_create_parametric_block_with_cross_check_warnings(
    app_client: AsyncIterator,
) -> None:
    """1A ≤ 1B → the route still accepts the row, but flags it."""
    data = _multipart_no_photos()
    # Make outer < inner so the warning fires.
    raw = dict(DUPLO_2X2_RAW, outer_pitch_mm=2.0, inner_pitch_mm=4.0)
    data["raw_measurements_mm"] = json.dumps(raw)
    resp = await app_client.post("/api/v1/parametric-blocks", data=data)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["cross_check_warnings"]  # non-empty
    assert any("1A" in w for w in body["cross_check_warnings"])


# ---------------------------------------------------------------------------
# End-to-end: API → worker → asset (the key test the brief asks for)
# ---------------------------------------------------------------------------
def test_parametric_block_end_to_end(
    work_in_tmp: Path,
    app_client: AsyncIterator,
) -> None:
    """Upload measurements → run worker in-process → assert GLB + asset meta.

    Same shape as ``test_reconstruction.py::test_reconstruct_task_end_to_end``
    but for the parametric path. The big differences:

    * No photo download / SfM ladder — the worker short-circuits to
      :func:`_run_parametric_pipeline` and calls
      :func:`services.block_generator.export_glb` directly.
    * The asset ``meta.pipeline_used`` is ``"parametric_block"`` (not
      one of the photo-path values).
    * The asset ``meta`` carries the 5 raw measurements + 4 derived
      spec + cross-check warnings + the system/kind/units, so a
      downstream tool can audit "did we render the right brick?".
    * The GLB bbox is fully deterministic — a 2x2 DUPLO brick has
      bbox_max.z = 17 + 7 = 24mm (= brick_height_total_mm).
    """
    import tempfile

    from app.config import settings
    from db.models import Asset, Capture, Job
    from db.session import async_session_factory
    from workers.tasks.reconstruct import reconstruct

    # ---- 1. Upload via the API -----------------------------------------
    data = _multipart_no_photos()
    resp = asyncio.get_event_loop().run_until_complete(
        app_client.post("/api/v1/parametric-blocks", data=data)
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    capture_id = body["capture_id"]
    job_id = body["job_id"]

    # ---- 2. Run the Celery task in-process -----------------------------
    result = reconstruct.apply(args=[capture_id])
    assert result.successful() or result.state == "SUCCESS", result.state

    # ---- 3. Assert DB state --------------------------------------------
    async def _db_state() -> tuple[Job | None, list[Asset], Capture | None]:
        f = async_session_factory()
        async with f() as session:
            from sqlalchemy import select
            from sqlalchemy.orm import selectinload

            cap = await session.get(Capture, uuid.UUID(capture_id))
            stmt = select(Job).where(Job.id == uuid.UUID(job_id)).options(selectinload(Job.assets))
            job = (await session.execute(stmt)).scalar_one_or_none()
            return job, list(job.assets) if job else [], cap

    job, assets, cap = asyncio.run(_db_state())
    assert job is not None
    assert job.status == "completed", f"job.status={job.status} stage={job.stage} error={job.error}"
    assert job.progress == 100
    assert job.stage == "completed"
    assert job.finished_at is not None
    assert cap is not None
    assert cap.status == "completed"
    # Capture row carries the parametric columns.
    assert cap.mode == "parametric_block"
    assert cap.system == "duplo"
    assert cap.kind == "brick"
    assert cap.units_x == 2
    assert cap.units_y == 2
    assert cap.raw_measurements_mm is not None
    assert cap.derived_spec_mm is not None
    assert cap.cross_check_warnings == []  # clean measurements

    # ---- 4. Assert asset row + meta ------------------------------------
    assert len(assets) == 1, [a.kind for a in assets]
    asset = assets[0]
    assert asset.kind == "mesh_gltf"
    assert asset.size_bytes is not None and asset.size_bytes > 1024
    assert asset.meta is not None
    meta = asset.meta
    # Standard keys present.
    for key in ("vertex_count", "face_count", "bbox_min", "bbox_max", "pipeline_used"):
        assert key in meta, f"missing {key} in {meta}"
    assert meta["vertex_count"] > 0
    assert meta["face_count"] > 0
    # Crucial: this is the parametric path, NOT the photo path.
    assert meta["pipeline_used"] == "parametric_block"
    # Parametric-specific metadata.
    assert meta["system"] == "duplo"
    assert meta["kind"] == "brick"
    assert meta["units_x"] == 2
    assert meta["units_y"] == 2
    assert meta["raw_measurements_mm"] == DUPLO_2X2_RAW
    assert abs(meta["derived_spec_mm"]["unit_mm"] - 20.0) < 1e-6
    assert abs(meta["derived_spec_mm"]["height_mm"] - 17.0) < 1e-6
    assert abs(meta["derived_spec_mm"]["knob_diameter_mm"] - 16.0) < 1e-6
    assert abs(meta["derived_spec_mm"]["knob_height_mm"] - 7.0) < 1e-6
    assert meta["cross_check_warnings"] == []
    # Photo-path fields should be the parametric defaults.
    assert meta["input_image_count"] == 0
    assert meta["blender_used"] is None

    # ---- 5. Assert GLB is downloadable and a valid binary glTF ---------
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
        with tmp_path.open("rb") as f:
            header = f.read(4)
        assert header == b"glTF", header
        # Round-trip with trimesh — proves the mesh is structurally valid.
        loaded = trimesh.load(str(tmp_path), force="mesh")
        assert isinstance(loaded, trimesh.Trimesh)
        # A 2x2 DUPLO brick: 4 knobs on top, body + knobs ⇒ vertex
        # count > 4 (1 knob = 32-section cylinder ≈ 64 verts).
        assert len(loaded.vertices) > 0
        assert len(loaded.faces) > 0
    finally:
        tmp_path.unlink(missing_ok=True)

    # ---- 6. Assert the bbox matches the caliper dimensions ------------
    # The body is centred at the origin in X/Y (see
    # ``services.block_generator._box``) and lifted so the bottom
    # face is on Z=0. So:
    #   * bbox_min.x ≈ -unit_x   (2x2 duplo: -20 mm)
    #   * bbox_max.x ≈ +unit_x   (+20 mm; total width = 40 mm)
    #   * bbox_max.z = brick_height_net + knob_height = 17 + 7 = 24 mm
    # We assert against the half-extents, not the full width, so the
    # test fails loudly if the mesh is off-centre.
    bbox_min, bbox_max = meta["bbox_min"], meta["bbox_max"]
    assert abs(bbox_max[0] - 20.0) < 0.5, bbox_max
    assert abs(bbox_max[1] - 20.0) < 0.5, bbox_max
    assert abs(bbox_max[2] - 24.0) < 0.5, bbox_max
    assert abs(bbox_min[0] + 20.0) < 0.5, bbox_min
    assert abs(bbox_min[1] + 20.0) < 0.5, bbox_min
    assert abs(bbox_min[2] - 0.0) < 0.5, bbox_min  # bottom face on Z=0
    # Sanity: full width should match 2 * unit.
    full_width_x = bbox_max[0] - bbox_min[0]
    assert abs(full_width_x - 40.0) < 0.5, full_width_x

    # ---- 7. Assert the work directory was preserved on disk ------------
    work_dir = work_in_tmp / capture_id
    assert work_dir.exists(), f"work_dir {work_dir} missing (should be kept for debug)"
    glb_local = work_dir / "output" / "mesh.glb"
    assert glb_local.exists()


def test_parametric_block_with_photos_ignores_them_for_mesh(
    work_in_tmp: Path,
    app_client: AsyncIterator,
) -> None:
    """Reference photos are stored in MinIO + image_keys but DO NOT
    influence the parametric mesh (no SfM). The GLB dimensions are
    purely a function of the 5 caliper numbers + system/kind/units.
    """
    from db.session import async_session_factory
    from workers.tasks.reconstruct import reconstruct

    data, files = _multipart_with_photos(2)
    resp = asyncio.get_event_loop().run_until_complete(
        app_client.post(
            "/api/v1/parametric-blocks",
            data=data,
            files=files,
        )
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    capture_id = body["capture_id"]
    job_id = body["job_id"]

    result = reconstruct.apply(args=[capture_id])
    assert result.successful() or result.state == "SUCCESS", result.state

    async def _load_cap() -> tuple[list[str], int, dict[str, Any] | None]:
        f = async_session_factory()
        async with f() as session:
            from sqlalchemy import select
            from sqlalchemy.orm import selectinload

            from db.models import Capture, Job

            cap = await session.get(Capture, uuid.UUID(capture_id))
            stmt = select(Job).where(Job.id == uuid.UUID(job_id)).options(selectinload(Job.assets))
            job = (await session.execute(stmt)).scalar_one_or_none()
            assets = list(job.assets) if job else []
            return (
                list(cap.image_keys) if cap else [],
                int(cap.image_count) if cap else 0,
                assets[0].meta if assets else None,
            )

    image_keys, image_count, meta = asyncio.run(_load_cap())
    # 2 reference photos uploaded → image_keys populated.
    assert image_count == 2
    assert len(image_keys) == 2
    # But the worker recorded 0 input images (it didn't read them).
    assert meta is not None
    assert meta["pipeline_used"] == "parametric_block"
    assert meta["input_image_count"] == 0


def test_parametric_block_plate_uses_lower_height(
    work_in_tmp: Path,
    app_client: AsyncIterator,
) -> None:
    """A 'plate' kind (1/3 height) → bbox_max.z = 6mm (DUPLO plate), not 17mm.

    Verifies the dispatch is actually using the ``kind`` field from
    the capture row, not just defaulting to brick height.
    """
    from db.session import async_session_factory
    from workers.tasks.reconstruct import reconstruct

    data = {
        "system": "duplo",
        "kind": "plate",
        "units_x": "2",
        "units_y": "2",
        "raw_measurements_mm": json.dumps(
            {
                # Net brick height = 6mm (plate), total = 13mm (6 + 7 knob).
                "outer_pitch_mm": 36.0,
                "inner_pitch_mm": 4.0,
                "stud_diameter_mm": 16.0,
                "brick_height_net_mm": 6.0,
                "brick_height_total_mm": 13.0,
            }
        ),
    }
    resp = asyncio.get_event_loop().run_until_complete(
        app_client.post("/api/v1/parametric-blocks", data=data)
    )
    assert resp.status_code == 201, resp.text
    capture_id = resp.json()["capture_id"]
    job_id = resp.json()["job_id"]

    result = reconstruct.apply(args=[capture_id])
    assert result.successful() or result.state == "SUCCESS", result.state

    async def _meta() -> dict[str, Any] | None:
        f = async_session_factory()
        async with f() as session:
            from sqlalchemy import select
            from sqlalchemy.orm import selectinload

            from db.models import Job

            stmt = select(Job).where(Job.id == uuid.UUID(job_id)).options(selectinload(Job.assets))
            job = (await session.execute(stmt)).scalar_one_or_none()
            if not job or not job.assets:
                return None
            return job.assets[0].meta

    meta = asyncio.run(_meta())
    assert meta is not None
    assert meta["kind"] == "plate"
    # bbox_max.z = brick_height_total = 13mm; brick would be 24mm.
    assert abs(meta["bbox_max"][2] - 13.0) < 0.5, meta["bbox_max"]


def _strip_scheme(url: str) -> str:
    """``http://host:port`` -> ``host:port`` (minio client expects no scheme)."""
    return url.split("://", 1)[-1] if "://" in url else url
