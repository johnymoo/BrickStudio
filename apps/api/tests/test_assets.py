"""``GET /api/v1/assets/{asset_id}`` — redirect to a presigned URL."""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from sqlalchemy import insert

from db.models import Asset
from db.session import async_session_factory


async def test_get_unknown_asset_returns_404(app_client: AsyncIterator) -> None:
    resp = await app_client.get(f"/api/v1/assets/{uuid.uuid4()}")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] in {"ASSET_NOT_FOUND", "NOT_FOUND"}


async def test_get_asset_returns_302_with_presigned_url(app_client: AsyncIterator) -> None:
    """End-to-end: create a capture+job+asset, then read the asset URL."""
    import struct
    import zlib
    from datetime import datetime, timezone

    def png() -> bytes:
        def chunk(tag: bytes, data: bytes) -> bytes:
            return (
                struct.pack(">I", len(data))
                + tag
                + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
            )

        sig = b"\x89PNG\r\n\x1a\n"
        ihdr = struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0)
        raw = b"\x00" + b"\xff\x80\x00" * 4
        idat = zlib.compress(raw * 4)
        return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")

    files = [("images", (f"img_{i}.png", png(), "image/png")) for i in range(4)]
    create = await app_client.post(
        "/api/v1/captures",
        data={"part_id": "test-asset"},
        files=files,
    )
    capture_id = create.json()["capture_id"]
    job_id = create.json()["job_id"]

    # Create an asset row referencing the recon bucket + a real key.
    asset_id = uuid.uuid4()
    factory = async_session_factory()
    async with factory() as session:
        await session.execute(
            insert(Asset).values(
                id=asset_id,
                job_id=uuid.UUID(job_id),
                kind="mesh_gltf",
                storage_key="recon/test/mesh.glb",
                size_bytes=1024,
                meta={"vertices": 100, "faces": 200},
                created_at=datetime.now(tz=timezone.utc),
            )
        )
        await session.commit()

    # Now hit the asset endpoint. We use follow_redirects=False so we can
    # inspect the 302 status + Location header.
    resp = await app_client.get(f"/api/v1/assets/{asset_id}", follow_redirects=False)
    assert resp.status_code == 302, resp.text
    assert "Location" in resp.headers
    location = resp.headers["Location"]
    assert "mesh.glb" in location or "X-Amz-Signature" in location or "blocktool-test-recon" in location, location

    # And the body (we keep it next to the 302) is a JSON envelope.
    body = resp.json()
    assert body["asset_id"] == str(asset_id)
    assert body["job_id"] == job_id
    assert body["kind"] == "mesh_gltf"
    assert body["url"], body
