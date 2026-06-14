"""``/api/v1/assets/{asset_id}`` — redirect to a presigned S3 URL."""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.config import settings
from app.deps import DBSessionDep
from core.errors import AssetNotFound
from db.models import Asset
from models.schemas import AssetRead
from storage.minio_client import storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assets", tags=["assets"])


@router.get(
    "/{asset_id}",
    summary="Fetch a presigned URL for a reconstruction asset",
    response_model=None,
    responses={
        200: {"description": "JSON body with the presigned URL"},
        302: {"description": "Redirect to the presigned URL"},
    },
)
async def get_asset(
    asset_id: uuid.UUID,
    request: Request,
    session: DBSessionDep,
) -> RedirectResponse | JSONResponse:
    asset = await session.get(Asset, asset_id)
    if asset is None:
        raise AssetNotFound(f"asset {asset_id} not found")

    url, expires_at = storage.presigned_get(settings.s3_bucket_recon, asset.storage_key)
    body = AssetRead(
        asset_id=asset.id,
        job_id=asset.job_id,
        kind=asset.kind,
        url=url,
        size_bytes=asset.size_bytes,
        meta=asset.meta,
        expires_at=expires_at,
    )

    # The brief says "302 跳到 presigned URL". We also return a small JSON body
    # so curl / SDK clients that don't follow redirects still get something
    # useful. Most browsers will follow the 302 and never see the body.
    if _prefers_json(request.headers.get("accept")):
        return JSONResponse(content=body.model_dump(mode="json"))

    resp = JSONResponse(content=body.model_dump(mode="json"))
    resp.headers["Location"] = url
    resp.status_code = 302
    return resp


def _prefers_json(accept: str | None) -> bool:
    return any(part.split(";", 1)[0].strip().lower() == "application/json" for part in (accept or "").split(","))
