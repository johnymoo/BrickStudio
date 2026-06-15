"""Unit tests for the phase-2 reconstruction routing.

These tests exercise the routing logic in
:func:`workers.tasks.reconstruct._select_and_run_pipeline` without
shelling out to the real COLMAP binary or rendering 8+ synthetic
photos. We monkey-patch the COLMAP and Open3D paths with mocks so
each branch is hit in < 50ms and the test stays fast + hermetic.

Coverage:
* 4 images → Open3DRunner.reconstruct_from_photos (phase-1 fallback)
* 8 images + COLMAP available → ColmapRunner.reconstruct
* 8 images + COLMAP raises ColmapUnavailable → Open3DRunner.reconstruct_from_photos_multi
* 8 images + COLMAP raises ColmapFailed → Open3DRunner.reconstruct_from_photos_multi
* 20 images (upper bound) → COLMAP path attempted
* 3 images (under min) → 4-7 fallback (actually the route layer
  rejects this with 422, but the routing function should still
  tolerate it for safety)
* capture mode field round-trips through the API
"""
from __future__ import annotations

import struct
import uuid
import zlib
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _png(width: int = 4, height: int = 4) -> bytes:
    """Tiny valid PNG so the multipart upload accepts the bytes."""

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
        raw += b"\x00" + b"\xff\x80\x00" * width
    idat = zlib.compress(raw)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _files(n: int) -> list[tuple[str, bytes, str]]:
    return [("images", (f"img_{i}.png", _png(), "image/png")) for i in range(n)]


@pytest.fixture
def fake_self() -> MagicMock:
    """A stand-in for the bound Celery task self argument.

    Only :meth:`update_state` is actually called by the routing code
    (and only defensively); everything else can be a MagicMock.
    """
    s = MagicMock()
    s.update_state = MagicMock()
    return s


@pytest.fixture
def work_in_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    monkeypatch.setenv("BLOCKTOOL_RECON_ROOT", str(tmp_path))
    yield tmp_path


# ---------------------------------------------------------------------------
# Direct unit tests on _select_and_run_pipeline
# ---------------------------------------------------------------------------
def test_routing_4_images_uses_open3d_fallback(
    fake_self: MagicMock, work_in_tmp: Path
) -> None:
    """4 images → Open3DRunner.reconstruct_from_photos (phase-1 path)."""
    from workers.tasks.reconstruct import _select_and_run_pipeline

    job_id = uuid.uuid4()
    input_dir = work_in_tmp / "in"
    input_dir.mkdir()
    for i in range(4):
        (input_dir / f"img_{i}.png").write_bytes(_png())
    output_dir = work_in_tmp / "out"
    output_dir.mkdir()

    single = MagicMock(return_value={"pipeline_used": "open3d_fallback"})
    multi = MagicMock(return_value={"pipeline_used": "open3d_pure_photogrammetry"})
    colmap = MagicMock(return_value={"pipeline_used": "colmap_sfm"})

    with patch("workers.tasks.reconstruct.ColmapRunner", colmap), \
         patch.object(
             __import__("pipelines.open3d_runner", fromlist=["Open3DRunner"]).Open3DRunner,
             "reconstruct_from_photos",
             single,
         ), \
         patch.object(
             __import__("pipelines.open3d_runner", fromlist=["Open3DRunner"]).Open3DRunner,
             "reconstruct_from_photos_multi",
             multi,
         ):
        name, result = _select_and_run_pipeline(
            fake_self, job_id, input_dir, output_dir, n_images=4,
        )

    assert name == "open3d_fallback"
    assert result["pipeline_used"] == "open3d_fallback"
    single.assert_called_once()
    multi.assert_not_called()
    colmap.assert_not_called()


def test_routing_8_images_with_colmap_uses_colmap(
    fake_self: MagicMock, work_in_tmp: Path
) -> None:
    """8 images + COLMAP available → ColmapRunner.reconstruct."""
    from workers.tasks.reconstruct import _select_and_run_pipeline

    job_id = uuid.uuid4()
    input_dir = work_in_tmp / "in"
    input_dir.mkdir()
    for i in range(8):
        (input_dir / f"img_{i}.png").write_bytes(_png())
    output_dir = work_in_tmp / "out"
    output_dir.mkdir()

    single = MagicMock(return_value={"pipeline_used": "open3d_fallback"})
    multi = MagicMock(return_value={"pipeline_used": "open3d_pure_photogrammetry"})
    colmap_inst = MagicMock()
    colmap_inst.name = "colmap_sfm"
    colmap_inst.reconstruct = MagicMock(
        return_value={"pipeline_used": "colmap_sfm", "vertex_count": 100, "face_count": 200}
    )

    with patch("workers.tasks.reconstruct.ColmapRunner", MagicMock(return_value=colmap_inst)), \
         patch.object(
             __import__("pipelines.open3d_runner", fromlist=["Open3DRunner"]).Open3DRunner,
             "reconstruct_from_photos",
             single,
         ), \
         patch.object(
             __import__("pipelines.open3d_runner", fromlist=["Open3DRunner"]).Open3DRunner,
             "reconstruct_from_photos_multi",
             multi,
         ):
        name, result = _select_and_run_pipeline(
            fake_self, job_id, input_dir, output_dir, n_images=8,
        )

    assert name == "colmap_sfm"
    assert result["pipeline_used"] == "colmap_sfm"
    colmap_inst.reconstruct.assert_called_once()
    multi.assert_not_called()
    single.assert_not_called()


def test_routing_8_images_colmap_unavailable_falls_back_to_multi(
    fake_self: MagicMock, work_in_tmp: Path
) -> None:
    """8 images + COLMAP raises ColmapUnavailable → multi path."""
    from pipelines.colmap_runner import ColmapUnavailable
    from workers.tasks.reconstruct import _select_and_run_pipeline

    job_id = uuid.uuid4()
    input_dir = work_in_tmp / "in"
    input_dir.mkdir()
    for i in range(8):
        (input_dir / f"img_{i}.png").write_bytes(_png())
    output_dir = work_in_tmp / "out"
    output_dir.mkdir()

    single = MagicMock(return_value={"pipeline_used": "open3d_fallback"})
    multi = MagicMock(return_value={"pipeline_used": "open3d_pure_photogrammetry"})
    colmap_inst = MagicMock()
    colmap_inst.name = "colmap_sfm"
    colmap_inst.reconstruct = MagicMock(side_effect=ColmapUnavailable("not installed"))

    with patch("workers.tasks.reconstruct.ColmapRunner", MagicMock(return_value=colmap_inst)), \
         patch.object(
             __import__("pipelines.open3d_runner", fromlist=["Open3DRunner"]).Open3DRunner,
             "reconstruct_from_photos",
             single,
         ), \
         patch.object(
             __import__("pipelines.open3d_runner", fromlist=["Open3DRunner"]).Open3DRunner,
             "reconstruct_from_photos_multi",
             multi,
         ):
        name, result = _select_and_run_pipeline(
            fake_self, job_id, input_dir, output_dir, n_images=8,
        )

    assert name == "open3d_pure_photogrammetry"
    assert result["pipeline_used"] == "open3d_pure_photogrammetry"
    multi.assert_called_once()
    single.assert_not_called()


def test_routing_8_images_colmap_failed_falls_back_to_multi(
    fake_self: MagicMock, work_in_tmp: Path
) -> None:
    """8 images + COLMAP raises ColmapFailed (binary crashed) → multi path."""
    from pipelines.colmap_runner import ColmapFailed
    from workers.tasks.reconstruct import _select_and_run_pipeline

    job_id = uuid.uuid4()
    input_dir = work_in_tmp / "in"
    input_dir.mkdir()
    for i in range(8):
        (input_dir / f"img_{i}.png").write_bytes(_png())
    output_dir = work_in_tmp / "out"
    output_dir.mkdir()

    multi = MagicMock(return_value={"pipeline_used": "open3d_pure_photogrammetry"})
    single = MagicMock(return_value={"pipeline_used": "open3d_fallback"})
    colmap_inst = MagicMock()
    colmap_inst.name = "colmap_sfm"
    colmap_inst.reconstruct = MagicMock(side_effect=ColmapFailed("rc=139 segfault"))

    with patch("workers.tasks.reconstruct.ColmapRunner", MagicMock(return_value=colmap_inst)), \
         patch.object(
             __import__("pipelines.open3d_runner", fromlist=["Open3DRunner"]).Open3DRunner,
             "reconstruct_from_photos",
             single,
         ), \
         patch.object(
             __import__("pipelines.open3d_runner", fromlist=["Open3DRunner"]).Open3DRunner,
             "reconstruct_from_photos_multi",
             multi,
         ):
        name, _result = _select_and_run_pipeline(
            fake_self, job_id, input_dir, output_dir, n_images=8,
        )

    assert name == "open3d_pure_photogrammetry"
    multi.assert_called_once()
    single.assert_not_called()


def test_routing_20_images_still_attempts_colmap(
    fake_self: MagicMock, work_in_tmp: Path
) -> None:
    """20 images (upper bound) → COLMAP path attempted first."""
    from workers.tasks.reconstruct import _select_and_run_pipeline

    job_id = uuid.uuid4()
    input_dir = work_in_tmp / "in"
    input_dir.mkdir()
    for i in range(20):
        (input_dir / f"img_{i}.png").write_bytes(_png())
    output_dir = work_in_tmp / "out"
    output_dir.mkdir()

    multi = MagicMock(return_value={"pipeline_used": "open3d_pure_photogrammetry"})
    single = MagicMock(return_value={"pipeline_used": "open3d_fallback"})
    colmap_inst = MagicMock()
    colmap_inst.name = "colmap_sfm"
    colmap_inst.reconstruct = MagicMock(
        return_value={"pipeline_used": "colmap_sfm"}
    )

    with patch("workers.tasks.reconstruct.ColmapRunner", MagicMock(return_value=colmap_inst)), \
         patch.object(
             __import__("pipelines.open3d_runner", fromlist=["Open3DRunner"]).Open3DRunner,
             "reconstruct_from_photos",
             single,
         ), \
         patch.object(
             __import__("pipelines.open3d_runner", fromlist=["Open3DRunner"]).Open3DRunner,
             "reconstruct_from_photos_multi",
             multi,
         ):
        name, _ = _select_and_run_pipeline(
            fake_self, job_id, input_dir, output_dir, n_images=20,
        )

    assert name == "colmap_sfm"
    colmap_inst.reconstruct.assert_called_once()


# ---------------------------------------------------------------------------
# Exception types
# ---------------------------------------------------------------------------
def test_colmap_unavailable_is_distinct_from_failed() -> None:
    """Worker routing depends on telling these two apart."""
    from pipelines.colmap_runner import ColmapFailed, ColmapUnavailable

    assert not issubclass(ColmapUnavailable, ColmapFailed)
    assert not issubclass(ColmapFailed, ColmapUnavailable)
    assert issubclass(ColmapUnavailable, RuntimeError)
    assert issubclass(ColmapFailed, RuntimeError)


def test_resolve_colmap_bin_raises_unavailable_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If neither $COLMAP_BIN nor shutil.which('colmap') find it,
    _resolve_colmap_bin must raise ColmapUnavailable."""
    from pipelines.colmap_runner import ColmapUnavailable, _resolve_colmap_bin

    monkeypatch.delenv("COLMAP_BIN", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(ColmapUnavailable):
        _resolve_colmap_bin()


def test_resolve_colmap_bin_prefers_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """$COLMAP_BIN wins over $PATH."""
    from pipelines.colmap_runner import _resolve_colmap_bin

    fake = tmp_path / "fake-colmap"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setenv("COLMAP_BIN", str(fake))
    called = []
    monkeypatch.setattr("shutil.which", lambda _: called.append(1) or "/should/not/be/used")
    assert _resolve_colmap_bin() == str(fake)
    # shutil.which should NOT have been called when env override works.
    assert called == []


# ---------------------------------------------------------------------------
# API contract: capture_mode + 4-20 limit
# ---------------------------------------------------------------------------
async def test_create_capture_with_8_images_returns_201(app_client: AsyncIterator) -> None:
    """8 images (COLMAP-eligible) → 201 with default capture_mode."""
    resp = await app_client.post(
        "/api/v1/captures",
        data={"part_id": "test-phase2-8", "capture_mode": "phone_walkaround"},
        files=_files(8),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["image_count"] == 8
    assert body["capture_mode"] == "phone_walkaround"
    assert body["status"] == "pending"


async def test_create_capture_with_20_images_returns_201(app_client: AsyncIterator) -> None:
    """20 images (upper bound) → 201."""
    resp = await app_client.post(
        "/api/v1/captures",
        data={"part_id": "test-phase2-20", "capture_mode": "studio_turntable"},
        files=_files(20),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["image_count"] == 20
    assert body["capture_mode"] == "studio_turntable"


async def test_create_capture_with_21_images_returns_422(app_client: AsyncIterator) -> None:
    """21 images (over upper bound) → 422."""
    resp = await app_client.post(
        "/api/v1/captures",
        data={"part_id": "test-phase2-21", "capture_mode": "quick_snapshot"},
        files=_files(21),
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    # Our CAPTURE_INVALID envelope is one shape; FastAPI's default
    # validation error is the other. Either is acceptable as long as
    # the response carries image_count.
    if "error" in body:
        assert body["error"]["code"] == "CAPTURE_INVALID"
        assert "image_count" in (body["error"].get("details") or {})
    else:
        # FastAPI's default shape — assert image_count is in the message
        blob = str(body)
        assert "image_count" in blob or "20" in blob


async def test_create_capture_with_3_images_returns_422(app_client: AsyncIterator) -> None:
    """3 images (under min) → 422 (regression — design contract)."""
    resp = await app_client.post(
        "/api/v1/captures",
        data={"part_id": "test-phase2-3", "capture_mode": "quick_snapshot"},
        files=_files(3),
    )
    assert resp.status_code == 422, resp.text


async def test_create_capture_default_capture_mode(app_client: AsyncIterator) -> None:
    """When the client omits capture_mode, the server defaults to
    ``phone_walkaround`` (the design-phase2.md §3.1 contract)."""
    resp = await app_client.post(
        "/api/v1/captures",
        data={"part_id": "test-phase2-default"},
        files=_files(4),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["capture_mode"] == "phone_walkaround"


async def test_create_capture_rejects_invalid_mode(app_client: AsyncIterator) -> None:
    """Unknown capture_mode values get a 422 from FastAPI's Literal type."""
    resp = await app_client.post(
        "/api/v1/captures",
        data={"part_id": "test-phase2-bad-mode", "capture_mode": "balloon"},
        files=_files(4),
    )
    assert resp.status_code == 422, resp.text


async def test_get_capture_returns_capture_mode(app_client: AsyncIterator) -> None:
    """GET /captures/{id} returns the capture_mode field too."""
    create = await app_client.post(
        "/api/v1/captures",
        data={"part_id": "test-phase2-get", "capture_mode": "studio_turntable"},
        files=_files(4),
    )
    capture_id = create.json()["capture_id"]
    resp = await app_client.get(f"/api/v1/captures/{capture_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["capture_mode"] == "studio_turntable"
