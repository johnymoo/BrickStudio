"""Regression tests for MinIO presigned URL generation."""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from storage import minio_client
from storage.minio_client import StorageClient


class FakeMinio:
    """Small stand-in that exposes which endpoint signed the URL."""

    instances: list["FakeMinio"] = []

    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        secure: bool,
        region: str,
    ) -> None:
        self.endpoint = endpoint
        self.secure = secure
        self.calls: list[tuple[str, str, timedelta]] = []
        FakeMinio.instances.append(self)

    def presigned_get_object(self, bucket: str, key: str, *, expires: timedelta) -> str:
        self.calls.append((bucket, key, expires))
        scheme = "https" if self.secure else "http"
        return (
            f"{scheme}://{self.endpoint}/{bucket}/{key}"
            f"?X-Amz-SignedHeaders=host"
            f"&signed-host={self.endpoint}"
        )


def test_presigned_get_signs_with_public_endpoint_when_configured(monkeypatch) -> None:
    FakeMinio.instances = []
    monkeypatch.setattr(minio_client, "Minio", FakeMinio)
    monkeypatch.setattr(
        minio_client,
        "settings",
        SimpleNamespace(
            s3_endpoint="http://minio:9000",
            s3_public_endpoint="http://192.168.88.75:9000",
            s3_public_base="http://192.168.88.75:9000",
            s3_access_key="blocktool",
            s3_secret_key="blocktool",
            s3_secure=False,
            s3_region="us-east-1",
        ),
    )

    url, _expires_at = StorageClient().presigned_get("models", "mesh.glb")

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.netloc == "192.168.88.75:9000"
    assert query["signed-host"] == ["192.168.88.75:9000"]
    assert [client.endpoint for client in FakeMinio.instances] == [
        "minio:9000",
        "192.168.88.75:9000",
    ]
    assert FakeMinio.instances[0].calls == []
    assert FakeMinio.instances[1].calls[0][:2] == ("models", "mesh.glb")


def test_presigned_get_uses_internal_endpoint_when_public_endpoint_unset(monkeypatch) -> None:
    FakeMinio.instances = []
    monkeypatch.setattr(minio_client, "Minio", FakeMinio)
    monkeypatch.setattr(
        minio_client,
        "settings",
        SimpleNamespace(
            s3_endpoint="http://minio:9000",
            s3_public_endpoint=None,
            s3_public_base="http://localhost:9000",
            s3_access_key="blocktool",
            s3_secret_key="blocktool",
            s3_secure=False,
            s3_region="us-east-1",
        ),
    )

    url, _expires_at = StorageClient().presigned_get("models", "mesh.glb")

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.netloc == "minio:9000"
    assert query["signed-host"] == ["minio:9000"]
    assert [client.endpoint for client in FakeMinio.instances] == ["minio:9000"]
    assert FakeMinio.instances[0].calls[0][:2] == ("models", "mesh.glb")


def test_presigned_get_uses_injected_client_when_public_endpoint_differs(monkeypatch) -> None:
    FakeMinio.instances = []
    injected = FakeMinio(
        endpoint="injected-minio:9000",
        access_key="blocktool",
        secret_key="blocktool",
        secure=False,
        region="us-east-1",
    )
    monkeypatch.setattr(minio_client, "Minio", FakeMinio)
    monkeypatch.setattr(
        minio_client,
        "settings",
        SimpleNamespace(
            s3_endpoint="http://minio:9000",
            s3_public_endpoint="http://192.168.88.75:9000",
            s3_public_base="http://192.168.88.75:9000",
            s3_access_key="blocktool",
            s3_secret_key="blocktool",
            s3_secure=False,
            s3_region="us-east-1",
        ),
    )

    url, _expires_at = StorageClient(client=injected).presigned_get("models", "mesh.glb")

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.netloc == "injected-minio:9000"
    assert query["signed-host"] == ["injected-minio:9000"]
    assert FakeMinio.instances == [injected]
    assert injected.calls[0][:2] == ("models", "mesh.glb")


@pytest.mark.parametrize(
    "public_endpoint",
    [
        "http://192.168.88.75:9000/minio",
        "http://192.168.88.75:9000?proxy=minio",
        "http://192.168.88.75:9000#minio",
    ],
)
def test_public_endpoint_with_path_query_or_fragment_fails_clearly(
    monkeypatch, public_endpoint: str
) -> None:
    monkeypatch.setattr(minio_client, "Minio", FakeMinio)
    monkeypatch.setattr(
        minio_client,
        "settings",
        SimpleNamespace(
            s3_endpoint="http://minio:9000",
            s3_public_endpoint=public_endpoint,
            s3_public_base=public_endpoint,
            s3_access_key="blocktool",
            s3_secret_key="blocktool",
            s3_secure=False,
            s3_region="us-east-1",
        ),
    )

    with pytest.raises(ValueError, match="must not include a path, query, or fragment"):
        StorageClient()
