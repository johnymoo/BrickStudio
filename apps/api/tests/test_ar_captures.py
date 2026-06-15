"""Endpoint tests for POST /api/v1/ar-captures."""
from __future__ import annotations

import asyncio
import json
import struct
import tempfile
import uuid
import zlib
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import trimesh
from fixtures.synth_studs import make_ar_bundle


def _png(width: int = 4, height: int = 4, color: bytes = b"\xff\x80\x00") -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + color * width for _ in range(height))
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def _files(rgb: bytes, depth: bytes, n_images: int = 4):
    files = [
        ("recognition_rgb", ("rgb.png", rgb, "image/png")),
        ("recognition_depth", ("depth.png", depth, "image/png")),
    ]
    files += [("images", (f"{i}.png", _png(), "image/png")) for i in range(n_images)]
    return files


async def test_ar_capture_recognized_feile(app_client: AsyncIterator) -> None:
    rgb, depth, meta = make_ar_bundle("feile", 2, 4)
    resp = await app_client.post(
        "/api/v1/ar-captures",
        data={"kind": "brick", "ar_metadata": json.dumps(meta)},
        files=_files(rgb, depth),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "recognized"
    assert body["mode"] == "ar_recognized"
    assert body["recognized"]["system"] == "feile"
    assert body["recognized"]["units_x"] == 2
    assert body["recognized"]["units_y"] == 4
    assert body["job_id"] is not None
    assert body["needs_measurement"] is None


async def test_ar_capture_unknown_hint_needs_measurement(app_client: AsyncIterator) -> None:
    rgb, depth, meta = make_ar_bundle("feile", 2, 2)
    resp = await app_client.post(
        "/api/v1/ar-captures",
        data={"kind": "brick", "ar_metadata": json.dumps(meta), "system_hint": "unknown"},
        files=_files(rgb, depth),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "needs_measurement"
    assert body["job_id"] is None
    assert body["needs_measurement"]["endpoint"] == "/api/v1/parametric-blocks"
    assert "outer_pitch_mm" in body["needs_measurement"]["fields"]


async def test_ar_capture_needs_measurement_is_visible_from_capture_detail(app_client: AsyncIterator) -> None:
    from db.models import Capture
    from db.session import async_session_factory

    capture_uuid = uuid.uuid4()
    factory = async_session_factory()
    async with factory() as session:
        session.add(
            Capture(
                id=capture_uuid,
                part_id="ar-brick-test",
                status="pending",
                image_count=4,
                image_keys=[
                    f"raw/{capture_uuid}/000.png",
                    f"raw/{capture_uuid}/001.png",
                    f"raw/{capture_uuid}/002.png",
                    f"raw/{capture_uuid}/003.png",
                ],
                capture_mode="phone_walkaround",
                mode="ar_recognized",
                kind="brick",
                recognition_result={
                    "ok": False,
                    "reason": "low_confidence",
                    "confidence": 0.2,
                },
            )
        )
        await session.commit()
    capture_id = str(capture_uuid)

    resp = await app_client.get(f"/api/v1/captures/{capture_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["capture_id"] == capture_id
    assert body["mode"] == "ar_recognized"
    assert body["kind"] == "brick"
    assert body["job_id"] is None
    assert body["image_keys"] == [
        f"raw/{capture_id}/000.png",
        f"raw/{capture_id}/001.png",
        f"raw/{capture_id}/002.png",
        f"raw/{capture_id}/003.png",
    ]
    assert body["recognition_result"]["ok"] is False
    assert body["recognition_result"]["reason"] is not None
    assert body["needs_measurement"]["reason"] == body["recognition_result"]["reason"]
    assert body["needs_measurement"]["endpoint"] == "/api/v1/parametric-blocks"
    assert "outer_pitch_mm" in body["needs_measurement"]["fields"]
    assert body["updated_at"] is not None


async def test_ar_capture_rejects_missing_depth(app_client: AsyncIterator) -> None:
    rgb, _depth, meta = make_ar_bundle("feile")
    resp = await app_client.post(
        "/api/v1/ar-captures",
        data={"kind": "brick", "ar_metadata": json.dumps(meta)},
        files=[("recognition_rgb", ("rgb.png", rgb, "image/png"))]
        + [("images", (f"{i}.png", _png(), "image/png")) for i in range(4)],
    )
    assert resp.status_code == 422, resp.text


async def test_ar_capture_rejects_bad_metadata_json(app_client: AsyncIterator) -> None:
    rgb, depth, _meta = make_ar_bundle("feile")
    resp = await app_client.post(
        "/api/v1/ar-captures",
        data={"kind": "brick", "ar_metadata": "not-json"},
        files=_files(rgb, depth),
    )
    assert resp.status_code == 422, resp.text


async def test_ar_capture_rejects_too_few_images(app_client: AsyncIterator) -> None:
    rgb, depth, meta = make_ar_bundle("feile")
    resp = await app_client.post(
        "/api/v1/ar-captures",
        data={"kind": "brick", "ar_metadata": json.dumps(meta)},
        files=_files(rgb, depth, n_images=2),
    )
    assert resp.status_code == 422, resp.text


async def test_ar_capture_rejects_rgb_png_as_recognition_depth(app_client: AsyncIterator) -> None:
    rgb, _depth, meta = make_ar_bundle("feile")
    resp = await app_client.post(
        "/api/v1/ar-captures",
        data={"kind": "brick", "ar_metadata": json.dumps(meta)},
        files=_files(rgb, _png()),
    )

    assert resp.status_code == 422, resp.text
    assert "recognition_depth" in resp.text


async def test_ar_capture_rejects_unsupported_recognition_rgb_content_type(app_client: AsyncIterator) -> None:
    rgb, depth, meta = make_ar_bundle("feile")
    files = [
        ("recognition_rgb", ("rgb.svg", rgb, "image/svg+xml")),
        ("recognition_depth", ("depth.png", depth, "image/png")),
    ]
    files += [("images", (f"{i}.png", _png(), "image/png")) for i in range(4)]

    resp = await app_client.post(
        "/api/v1/ar-captures",
        data={"kind": "brick", "ar_metadata": json.dumps(meta)},
        files=files,
    )

    assert resp.status_code == 422, resp.text
    assert "recognition_rgb" in resp.text


async def test_ar_capture_rejects_later_invalid_angle_without_recognition_or_storage(
    app_client: AsyncIterator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rgb, depth, meta = make_ar_bundle("feile")
    put_calls: list[tuple[object, ...]] = []
    recognize_calls: list[tuple[object, ...]] = []

    def fail_if_called(*args, **kwargs):
        recognize_calls.append(args)
        raise AssertionError("recognize_brick should not be called")

    monkeypatch.setattr("api.v1.ar_captures.storage.put_object", lambda *args, **_kwargs: put_calls.append(args))
    monkeypatch.setattr("api.v1.ar_captures.recognize_brick", fail_if_called)

    files = _files(rgb, depth, n_images=3)
    files.append(("images", ("bad.png", b"not an image", "image/png")))
    resp = await app_client.post(
        "/api/v1/ar-captures",
        data={"kind": "brick", "ar_metadata": json.dumps(meta)},
        files=files,
    )

    assert resp.status_code == 422, resp.text
    assert put_calls == []
    assert recognize_calls == []


async def test_ar_capture_cleans_up_stored_raw_objects_when_later_put_fails(
    app_client: AsyncIterator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rgb, depth, meta = make_ar_bundle("feile")
    stored_keys: list[str] = []
    removed_keys: list[str] = []

    def fake_put_object(_bucket: str, key: str, *_args: object, **_kwargs: object) -> str:
        if len(stored_keys) == 2:
            raise RuntimeError("storage write failed")
        stored_keys.append(key)
        return key

    monkeypatch.setattr("api.v1.ar_captures.storage.put_object", fake_put_object)
    monkeypatch.setattr("api.v1.ar_captures.storage.remove_object", lambda _bucket, key: removed_keys.append(key))

    with pytest.raises(RuntimeError, match="storage write failed"):
        await app_client.post(
            "/api/v1/ar-captures",
            data={"kind": "brick", "ar_metadata": json.dumps(meta)},
            files=_files(rgb, depth),
        )

    assert set(removed_keys) == set(stored_keys)


async def test_ar_capture_does_not_cleanup_after_needs_measurement_commit_boundary_on_refresh_failure(
    app_client: AsyncIterator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    rgb, depth, meta = make_ar_bundle("feile")
    removed_keys: list[str] = []

    async def fail_refresh(self: AsyncSession, *_args: object, **_kwargs: object) -> None:
        raise RuntimeError("refresh failed after commit")

    monkeypatch.setattr("api.v1.ar_captures.storage.remove_object", lambda _bucket, key: removed_keys.append(key))
    monkeypatch.setattr(AsyncSession, "refresh", fail_refresh)

    with pytest.raises(RuntimeError, match="refresh failed after commit"):
        await app_client.post(
            "/api/v1/ar-captures",
            data={"kind": "brick", "ar_metadata": json.dumps(meta), "system_hint": "unknown"},
            files=_files(rgb, depth),
        )

    assert removed_keys == []


async def test_ar_capture_does_not_cleanup_when_dispatcher_fails_after_commit(
    app_client: AsyncIterator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    rgb, depth, meta = make_ar_bundle("feile")
    removed_keys: list[str] = []

    async def fake_dispatch(
        session: AsyncSession,
        *_args: object,
        **_kwargs: object,
    ) -> uuid.UUID:
        await session.commit()
        raise RuntimeError("dispatcher failed after commit")

    monkeypatch.setattr("api.v1.ar_captures.commit_and_dispatch_reconstruct", fake_dispatch)
    monkeypatch.setattr("api.v1.ar_captures.storage.remove_object", lambda _bucket, key: removed_keys.append(key))

    with pytest.raises(RuntimeError, match="dispatcher failed after commit"):
        await app_client.post(
            "/api/v1/ar-captures",
            data={"kind": "brick", "ar_metadata": json.dumps(meta)},
            files=_files(rgb, depth),
        )

    assert removed_keys == []


# ---------------------------------------------------------------------------
# Fixtures for e2e tests
# ---------------------------------------------------------------------------
@pytest.fixture
def work_in_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("BLOCKTOOL_RECON_ROOT", str(tmp_path))
    yield tmp_path


# ---------------------------------------------------------------------------
# End-to-end: POST → worker → GLB asset
# ---------------------------------------------------------------------------
def test_ar_capture_end_to_end(work_in_tmp: Path, app_client) -> None:
    """POST a recognized FEILE 2x4 bundle -> run the worker in-process ->
    assert a canonical GLB asset with pipeline_used == 'ar_recognized'."""
    from app.config import settings
    from db.models import Asset, Job
    from db.session import async_session_factory
    from workers.tasks.reconstruct import reconstruct

    rgb, depth, meta = make_ar_bundle("feile", 2, 4)
    resp = asyncio.get_event_loop().run_until_complete(
        app_client.post(
            "/api/v1/ar-captures",
            data={"kind": "brick", "ar_metadata": json.dumps(meta)},
            files=_files(rgb, depth),
        )
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "recognized"
    capture_id = body["capture_id"]
    job_id = body["job_id"]

    result = reconstruct.apply(args=[capture_id])
    assert result.successful() or result.state == "SUCCESS", result.state

    async def _state() -> tuple[Job | None, list[Asset]]:
        f = async_session_factory()
        async with f() as session:
            from sqlalchemy import select
            from sqlalchemy.orm import selectinload

            stmt = select(Job).where(Job.id == uuid.UUID(job_id)).options(selectinload(Job.assets))
            job = (await session.execute(stmt)).scalar_one_or_none()
            return job, list(job.assets) if job else []

    job, assets = asyncio.run(_state())
    assert job is not None and job.status == "completed", job
    assert len(assets) == 1
    meta_out = assets[0].meta
    assert meta_out["pipeline_used"] == "ar_recognized"
    assert meta_out["system"] == "feile"
    assert meta_out["units_x"] == 2 and meta_out["units_y"] == 4
    assert meta_out["input_image_count"] == 0
    # FEILE 2x4: bbox_max.x = 16mm, bbox_max.y = 32mm.
    assert abs(meta_out["bbox_max"][0] - 16.0) < 0.5, meta_out["bbox_max"]
    assert abs(meta_out["bbox_max"][1] - 32.0) < 0.5, meta_out["bbox_max"]

    # GLB is a real binary glTF in MinIO.
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
        client.fget_object(settings.s3_bucket_recon, assets[0].storage_key, str(tmp_path))
        with tmp_path.open("rb") as f:
            assert f.read(4) == b"glTF"
        loaded = trimesh.load(str(tmp_path), force="mesh")
        assert isinstance(loaded, trimesh.Trimesh)
        assert len(loaded.vertices) > 0
    finally:
        tmp_path.unlink(missing_ok=True)


def _strip_scheme(url: str) -> str:
    """``http://host:port`` -> ``host:port`` (minio client expects no scheme)."""
    return url.split("://", 1)[-1] if "://" in url else url
