"""S3 / MinIO storage adapter.

The task brief calls for a thin client that:
  * uploads objects (multipart-friendly),
  * mints presigned GET URLs for the browser,
  * and makes sure the required buckets exist on startup.

We use the official ``minio`` Python client; it speaks the S3 protocol
and works against both MinIO and AWS S3 with the same code.
"""
from __future__ import annotations

import io
import logging
from datetime import UTC, datetime, timedelta
from typing import BinaryIO, Final
from urllib.parse import quote, urlparse

from minio import Minio
from minio.error import S3Error

from app.config import settings

logger = logging.getLogger(__name__)

PRESIGNED_TTL_SECONDS: Final[int] = 60 * 15  # 15 min — matches the brief's "expires soon" feel.


class StorageClient:
    """Thin facade around the MinIO client used by the rest of the app."""

    def __init__(self, client: Minio | None = None) -> None:
        self._client = client or Minio(
            endpoint=_strip_scheme(settings.s3_endpoint),
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            secure=settings.s3_secure,
            region=settings.s3_region,
        )
        self._presign_client = self._client
        public_endpoint = settings.s3_public_endpoint
        if public_endpoint:
            _validate_presign_endpoint(public_endpoint)
            if public_endpoint.rstrip("/") != settings.s3_endpoint.rstrip("/"):
                endpoint, secure = _minio_endpoint_and_secure(
                    public_endpoint, default_secure=settings.s3_secure
                )
                self._presign_client = Minio(
                    endpoint=endpoint,
                    access_key=settings.s3_access_key,
                    secret_key=settings.s3_secret_key,
                    secure=secure,
                    region=settings.s3_region,
                )

    # ---- Buckets ----------------------------------------------------------
    def ensure_bucket(self, bucket: str) -> None:
        """Idempotently create ``bucket`` if it doesn't exist yet."""
        try:
            if not self._client.bucket_exists(bucket):
                self._client.make_bucket(bucket, location=settings.s3_region)
                logger.info("Created bucket %s", bucket)
        except S3Error as exc:
            # Race-safe: if the bucket appeared between bucket_exists() and
            # make_bucket() the second call returns "BucketAlreadyExists".
            if exc.code in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                return
            raise

    def ensure_buckets(self, *buckets: str) -> None:
        for b in buckets:
            self.ensure_bucket(b)

    # ---- Objects ----------------------------------------------------------
    def put_object(
        self,
        bucket: str,
        key: str,
        data: bytes | BinaryIO,
        *,
        content_type: str = "application/octet-stream",
        length: int | None = None,
    ) -> str:
        """Upload ``data`` to ``bucket/key`` and return the key."""
        if isinstance(data, (bytes, bytearray)):
            stream: BinaryIO = io.BytesIO(data)
            data_length: int = length if length is not None else len(data)
        else:
            stream = data
            if length is None:
                raise ValueError("length must be provided for non-bytes data")
            data_length = length
        self._client.put_object(
            bucket,
            key,
            stream,
            length=data_length,
            content_type=content_type,
        )
        return key

    def get_object_bytes(self, bucket: str, key: str) -> bytes:
        """Download ``bucket/key`` and return it as a single ``bytes`` blob.

        Used by the Celery worker to pull source photos into a tmp dir
        before running the 3D pipeline. We don't stream-to-disk because
        individual captures are < 100 MB and a BytesIO round-trip is
        simpler than juggling partial downloads.
        """
        try:
            resp = self._client.get_object(bucket, key)
        except S3Error as exc:
            # Surface missing objects as ``FileNotFoundError`` so callers
            # (the Celery task) can treat it the same as a local read miss.
            if exc.code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
                raise FileNotFoundError(f"s3://{bucket}/{key} not found") from exc
            raise
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    def stat_object_size(self, bucket: str, key: str) -> int:
        """Return the size in bytes of ``bucket/key`` without downloading it."""
        try:
            stat = self._client.stat_object(bucket, key)
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
                raise FileNotFoundError(f"s3://{bucket}/{key} not found") from exc
            raise
        return int(stat.size or 0)

    def presigned_get(
        self,
        bucket: str,
        key: str,
        *,
        expires_seconds: int = PRESIGNED_TTL_SECONDS,
    ) -> tuple[str, datetime]:
        """Return a presigned GET URL plus its expiry timestamp (UTC)."""
        url = self._presign_client.presigned_get_object(
            bucket, key, expires=timedelta(seconds=expires_seconds)
        )
        return url, datetime.now(tz=UTC) + timedelta(seconds=expires_seconds)

    def remove_object(self, bucket: str, key: str) -> None:
        self._client.remove_object(bucket, key)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _strip_scheme(url: str) -> str:
    """``http://host:port`` -> ``host:port`` (minio client expects no scheme)."""
    if "://" in url:
        return url.split("://", 1)[1]
    return url


def _validate_presign_endpoint(url: str) -> None:
    """Reject public endpoints that would require path-aware S3 URL signing."""
    parsed = urlparse(url if "://" in url else f"//{url}")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("S3_PUBLIC_ENDPOINT must not include a path, query, or fragment")


def _minio_endpoint_and_secure(url: str, *, default_secure: bool) -> tuple[str, bool]:
    """Return the MinIO endpoint and TLS flag for an S3 endpoint URL."""
    if "://" not in url:
        return url, default_secure
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("S3_PUBLIC_ENDPOINT scheme must be http or https")
    return parsed.netloc, parsed.scheme == "https"


# Module-level singleton — the rest of the app imports this directly.
def get_storage() -> StorageClient:
    return StorageClient()


storage = get_storage()


# ---------------------------------------------------------------------------
# Public key-builder helpers (used by captures.py)
# ---------------------------------------------------------------------------
def raw_object_key(capture_id: str, filename: str) -> str:
    """Object key inside the raw/captures bucket.

    Layout: ``captures/<capture_id>/<filename>`` — keeps related objects
    collocated for cheap listing, and gives natural prefix-deletion later.
    """
    return f"captures/{capture_id}/{quote(filename, safe='-_./')}"


def recon_object_key(job_id: str, kind: str, ext: str = "glb") -> str:
    """Object key inside the recon bucket."""
    return f"recon/{job_id}/{kind}.{ext}"


__all__ = [
    "PRESIGNED_TTL_SECONDS",
    "StorageClient",
    "get_storage",
    "raw_object_key",
    "recon_object_key",
    "storage",
]
