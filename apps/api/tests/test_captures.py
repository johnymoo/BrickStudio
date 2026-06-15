"""``POST /api/v1/captures`` and ``GET /api/v1/captures/{id}``."""

from __future__ import annotations

import io
import struct
import zlib
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest


def _png(width: int = 4, height: int = 4) -> bytes:
    """Build a minimal valid 4x4 PNG (3 channels) without external deps.

    Just enough to satisfy the multipart upload — we don't decode the bytes
    on the server, we only PUT them in MinIO.
    """

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    raw = b""
    for _ in range(height):
        raw += b"\x00"  # filter: none
        raw += b"\xff\x80\x00" * width  # solid orange pixels
    idat = zlib.compress(raw)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _files(n: int) -> list[tuple[str, bytes, str]]:
    return [("images", (f"img_{i}.png", _png(), "image/png")) for i in range(n)]


async def test_create_capture_with_4_images_returns_201(app_client: AsyncIterator) -> None:
    resp = await app_client.post(
        "/api/v1/captures",
        data={"part_id": "test-part-001"},
        files=_files(4),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["part_id"] == "test-part-001"
    assert body["image_count"] == 4
    assert body["status"] == "pending"
    assert "capture_id" in body
    assert "job_id" in body


async def test_create_capture_rejects_3_images_with_422(app_client: AsyncIterator) -> None:
    resp = await app_client.post(
        "/api/v1/captures",
        data={"part_id": "test-part-002"},
        files=_files(3),
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    # Envelope is either FastAPI's default validation error or our custom
    # CAPTURE_INVALID (depending on where the check fires).
    if "error" in body:
        assert body["error"]["code"] in {"CAPTURE_INVALID", "VALIDATION_ERROR"}
    else:
        # FastAPI's default shape — should mention image count.
        assert any("image" in str(item).lower() for item in body.get("detail", []))


async def test_get_capture_returns_200(app_client: AsyncIterator) -> None:
    # Create first.
    create = await app_client.post(
        "/api/v1/captures",
        data={"part_id": "test-part-003"},
        files=_files(4),
    )
    capture_id = create.json()["capture_id"]

    resp = await app_client.get(f"/api/v1/captures/{capture_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["capture_id"] == capture_id
    assert body["part_id"] == "test-part-003"
    assert body["image_count"] == 4
    assert body["status"] in {"pending", "running", "completed", "failed"}


async def test_create_capture_rejects_later_invalid_image_without_partial_storage(
    app_client: AsyncIterator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    put_calls: list[tuple[object, ...]] = []

    monkeypatch.setattr(
        "api.v1.captures.storage.put_object", lambda *args, **_kwargs: put_calls.append(args)
    )

    files = _files(3) + [("images", ("bad.png", b"not an image", "image/png"))]
    resp = await app_client.post(
        "/api/v1/captures",
        data={"part_id": "test-part-invalid-late"},
        files=files,
    )

    assert resp.status_code == 422, resp.text
    assert put_calls == []


async def test_create_capture_cleans_up_stored_images_when_later_put_fails(
    app_client: AsyncIterator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored_keys: list[str] = []
    removed_keys: list[str] = []

    def fake_put_object(_bucket: str, key: str, *_args: object, **_kwargs: object) -> str:
        if len(stored_keys) == 2:
            raise RuntimeError("storage write failed")
        stored_keys.append(key)
        return key

    monkeypatch.setattr("api.v1.captures.storage.put_object", fake_put_object)
    monkeypatch.setattr(
        "api.v1.captures.storage.remove_object", lambda _bucket, key: removed_keys.append(key)
    )

    with pytest.raises(RuntimeError, match="storage write failed"):
        await app_client.post(
            "/api/v1/captures",
            data={"part_id": "test-part-storage-cleanup"},
            files=_files(4),
        )

    assert set(removed_keys) == set(stored_keys)


async def test_create_capture_does_not_cleanup_after_dispatch_boundary_on_refresh_failure(
    app_client: AsyncIterator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    removed_keys: list[str] = []

    async def fake_dispatch(session: AsyncSession, *_args: object, **_kwargs: object) -> UUID:
        await session.commit()
        return uuid4()

    async def fail_refresh(self: AsyncSession, *_args: object, **_kwargs: object) -> None:
        raise RuntimeError("refresh failed after dispatch")

    monkeypatch.setattr("api.v1.captures.commit_and_dispatch_reconstruct", fake_dispatch)
    monkeypatch.setattr(
        "api.v1.captures.storage.remove_object", lambda _bucket, key: removed_keys.append(key)
    )
    monkeypatch.setattr(AsyncSession, "refresh", fail_refresh)

    with pytest.raises(RuntimeError, match="refresh failed after dispatch"):
        await app_client.post(
            "/api/v1/captures",
            data={"part_id": "test-part-refresh-boundary"},
            files=_files(4),
        )

    assert removed_keys == []


async def test_create_capture_cleans_up_when_dispatcher_fails_before_durable_commit(
    app_client: AsyncIterator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored_keys: list[str] = []
    removed_keys: list[str] = []

    async def fake_dispatch(*_args: object, **_kwargs: object) -> UUID:
        raise RuntimeError("dispatcher failed before commit")

    monkeypatch.setattr(
        "api.v1.captures.storage.put_object",
        lambda _bucket, key, *_args, **_kwargs: stored_keys.append(key),
    )
    monkeypatch.setattr(
        "api.v1.captures.storage.remove_object", lambda _bucket, key: removed_keys.append(key)
    )
    monkeypatch.setattr("api.v1.captures.commit_and_dispatch_reconstruct", fake_dispatch)

    with pytest.raises(RuntimeError, match="dispatcher failed before commit"):
        await app_client.post(
            "/api/v1/captures",
            data={"part_id": "test-part-dispatch-precommit"},
            files=_files(4),
        )

    assert set(removed_keys) == set(stored_keys)


async def test_create_capture_does_not_cleanup_when_dispatcher_fails_after_commit(
    app_client: AsyncIterator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    removed_keys: list[str] = []

    async def fake_dispatch(
        session: AsyncSession,
        *_args: object,
        **_kwargs: object,
    ) -> UUID:
        await session.commit()
        raise RuntimeError("dispatcher failed after commit")

    monkeypatch.setattr("api.v1.captures.commit_and_dispatch_reconstruct", fake_dispatch)
    monkeypatch.setattr(
        "api.v1.captures.storage.remove_object", lambda _bucket, key: removed_keys.append(key)
    )

    with pytest.raises(RuntimeError, match="dispatcher failed after commit"):
        await app_client.post(
            "/api/v1/captures",
            data={"part_id": "test-part-dispatch-boundary"},
            files=_files(4),
        )

    assert removed_keys == []


async def test_get_unknown_capture_returns_404(app_client: AsyncIterator) -> None:
    import uuid

    missing = uuid.uuid4()
    resp = await app_client.get(f"/api/v1/captures/{missing}")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] in {"CAPTURE_NOT_FOUND", "NOT_FOUND"}


async def test_list_captures_returns_recent_ar_capture_that_needs_measurement(
    app_client: AsyncIterator,
) -> None:
    from db.models import Capture
    from db.session import async_session_factory

    capture_id = uuid4()
    factory = async_session_factory()
    async with factory() as session:
        session.add(
            Capture(
                id=capture_id,
                part_id="ar-brick-test",
                status="pending",
                image_count=2,
                image_keys=[
                    f"raw/{capture_id}/recognition_rgb.png",
                    f"raw/{capture_id}/000.png",
                ],
                capture_mode="phone_walkaround",
                mode="ar_recognized",
                kind="brick",
                recognition_result={
                    "ok": False,
                    "reason": "system_hint_unknown",
                    "confidence": 0.2,
                    "warnings": ["low_confidence"],
                },
            )
        )
        await session.commit()

    resp = await app_client.get("/api/v1/captures?limit=5")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [item["capture_id"] for item in body] == [str(capture_id)]
    item = body[0]
    assert item["mode"] == "ar_recognized"
    assert item["job_id"] is None
    assert item["image_keys"] == [
        f"raw/{capture_id}/recognition_rgb.png",
        f"raw/{capture_id}/000.png",
    ]
    assert item["recognition_result"]["reason"] == "system_hint_unknown"
    assert item["needs_measurement"]["reason"] == "system_hint_unknown"
    assert item["needs_measurement"]["endpoint"] == "/api/v1/parametric-blocks"


async def test_list_captures_uses_deterministic_latest_job_and_precise_needs_measurement(
    app_client: AsyncIterator,
) -> None:
    from db.models import Capture, Job
    from db.session import async_session_factory

    capture_id = uuid4()
    older_job_id = UUID("00000000-0000-0000-0000-000000000001")
    latest_job_id = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
    tied_created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fallback_id = uuid4()
    recognized_id = uuid4()

    factory = async_session_factory()
    async with factory() as session:
        session.add_all(
            [
                Capture(
                    id=capture_id,
                    part_id="photo-retry-test",
                    status="pending",
                    image_count=4,
                    image_keys=[f"raw/{capture_id}/{idx:03d}.png" for idx in range(4)],
                    capture_mode="phone_walkaround",
                    mode="photo",
                ),
                Capture(
                    id=fallback_id,
                    part_id="ar-fallback-test",
                    status="pending",
                    image_count=2,
                    image_keys=[f"raw/{fallback_id}/recognition_rgb.png"],
                    capture_mode="phone_walkaround",
                    mode="ar_recognized",
                    recognition_result={
                        "ok": False,
                        "reason": "system_hint_unknown",
                    },
                ),
                Capture(
                    id=recognized_id,
                    part_id="ar-recognized-test",
                    status="pending",
                    image_count=2,
                    image_keys=[f"raw/{recognized_id}/recognition_rgb.png"],
                    capture_mode="phone_walkaround",
                    mode="ar_recognized",
                    recognition_result={
                        "ok": True,
                        "reason": None,
                    },
                ),
                Job(
                    id=older_job_id,
                    capture_id=capture_id,
                    kind="reconstruct",
                    status="failed",
                    progress=100,
                    stage="failed",
                    created_at=tied_created_at,
                ),
                Job(
                    id=latest_job_id,
                    capture_id=capture_id,
                    kind="reconstruct",
                    status="pending",
                    progress=0,
                    stage="queued",
                    created_at=tied_created_at,
                ),
            ]
        )
        await session.commit()

    resp = await app_client.get("/api/v1/captures?limit=10")
    assert resp.status_code == 200, resp.text
    captures_by_id = {item["capture_id"]: item for item in resp.json()}

    photo_capture = captures_by_id[str(capture_id)]
    assert photo_capture["job_id"] == str(latest_job_id)
    assert photo_capture["status"] == "pending"

    fallback_capture = captures_by_id[str(fallback_id)]
    assert fallback_capture["job_id"] is None
    assert fallback_capture["status"] == "needs_measurement"
    assert fallback_capture["needs_measurement"]["reason"] == "system_hint_unknown"

    recognized_capture = captures_by_id[str(recognized_id)]
    assert recognized_capture["job_id"] is None
    assert recognized_capture["status"] == "pending"
    assert recognized_capture["needs_measurement"] is None

    detail = await app_client.get(f"/api/v1/captures/{capture_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["job_id"] == str(latest_job_id)


async def test_capture_images_returns_presigned_urls_for_that_capture_only(
    app_client: AsyncIterator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from db.models import Capture
    from db.session import async_session_factory

    capture_id = uuid4()
    other_id = uuid4()
    own_keys = [
        f"raw/{capture_id}/recognition_rgb.png",
        f"raw/{capture_id}/000.png",
    ]
    other_key = f"raw/{other_id}/000.png"
    factory = async_session_factory()
    async with factory() as session:
        session.add(
            Capture(
                id=capture_id,
                part_id="ar-brick-test",
                status="pending",
                image_count=len(own_keys),
                image_keys=own_keys,
                capture_mode="phone_walkaround",
            )
        )
        session.add(
            Capture(
                id=other_id,
                part_id="other",
                status="pending",
                image_count=1,
                image_keys=[other_key],
                capture_mode="phone_walkaround",
            )
        )
        await session.commit()

    seen: list[tuple[str, str]] = []

    def fake_presigned_get(bucket: str, key: str) -> str:
        seen.append((bucket, key))
        return f"https://raw.example.test/{key}?sig=test"

    monkeypatch.setattr("api.v1.captures.storage.presigned_get", fake_presigned_get)

    resp = await app_client.get(f"/api/v1/captures/{capture_id}/images")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [item["key"] for item in body["images"]] == own_keys
    assert [item["url"] for item in body["images"]] == [
        f"https://raw.example.test/{own_keys[0]}?sig=test",
        f"https://raw.example.test/{own_keys[1]}?sig=test",
    ]
    assert all(key != other_key for _, key in seen)
