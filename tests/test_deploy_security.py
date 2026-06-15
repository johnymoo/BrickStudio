"""Static deploy checks for Compose network and port boundaries."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _compose_config(*files: str) -> dict[str, Any]:
    command = [
        "docker",
        "compose",
        "--env-file",
        str(ROOT / "deploy/.env.example"),
    ]
    for file_name in files:
        command.extend(["-f", str(ROOT / file_name)])
    command.extend(["config", "--format", "json"])

    result = subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _service(config: dict[str, Any], name: str) -> dict[str, Any]:
    return config["services"][name]


def _ports(config: dict[str, Any], service_name: str) -> list[dict[str, Any]]:
    return list(_service(config, service_name).get("ports") or [])


def _network_names(config: dict[str, Any], service_name: str) -> set[str]:
    networks = _service(config, service_name).get("networks") or {}
    if isinstance(networks, list):
        return set(networks)
    return set(networks.keys())


def test_base_compose_publishes_only_proxy_ports_on_loopback() -> None:
    config = _compose_config("deploy/docker-compose.yml")

    for service_name in config["services"]:
        ports = _ports(config, service_name)
        if service_name == "proxy":
            assert {port["target"] for port in ports} == {80, 443, 9000}
            assert {port.get("host_ip") for port in ports} == {"127.0.0.1"}
        else:
            assert ports == [], f"{service_name} must not publish host ports"


def test_base_compose_uses_layered_network_boundaries() -> None:
    config = _compose_config("deploy/docker-compose.yml")

    assert set(config["networks"].keys()) == {"frontend", "backend", "data"}
    assert config["networks"]["data"].get("internal") is True

    assert _network_names(config, "postgres") == {"data"}
    assert _network_names(config, "redis") == {"backend"}
    assert _network_names(config, "minio") == {"backend"}
    assert _network_names(config, "minio-init") == {"backend"}
    assert _network_names(config, "api") == {"backend", "data"}
    assert _network_names(config, "worker") == {"backend", "data"}
    assert _network_names(config, "migrate") == {"data"}
    assert _network_names(config, "web") == {"frontend"}
    assert _network_names(config, "proxy") == {"frontend", "backend"}


def test_compose_files_do_not_pin_container_names() -> None:
    base = _compose_config("deploy/docker-compose.yml")
    dev = _compose_config("deploy/docker-compose.yml", "deploy/docker-compose.dev.yml")

    for config in (base, dev):
        for service_name, service in config["services"].items():
            assert "container_name" not in service, service_name


def test_dev_compose_publishes_only_loopback_debug_ports_without_duplicates() -> None:
    config = _compose_config("deploy/docker-compose.yml", "deploy/docker-compose.dev.yml")

    expected_targets = {
        "postgres": {5432},
        "redis": {6379},
        "minio": {9000, 9001},
        "api": {8000},
        "web": {5173},
    }
    for service_name, targets in expected_targets.items():
        ports = _ports(config, service_name)
        assert {port["target"] for port in ports} == targets
        assert {port.get("host_ip") for port in ports} == {"127.0.0.1"}

    for service_name in set(config["services"]) - set(expected_targets):
        assert _ports(config, service_name) == [], service_name

    published: set[tuple[str | None, str, str]] = set()
    for service_name in config["services"]:
        for port in _ports(config, service_name):
            assert port.get("host_ip") != "0.0.0.0", service_name
            key = (
                port.get("host_ip"),
                str(port["published"]),
                port.get("protocol", "tcp"),
            )
            assert key not in published, f"duplicate published port {key}"
            published.add(key)


def test_recon_bucket_is_not_anonymous_and_s3_public_endpoint_uses_proxy() -> None:
    compose = _read("deploy/docker-compose.yml")
    caddyfile = _read("deploy/Caddyfile")
    env_example = _read("deploy/.env.example")

    assert "mc anonymous set download local/${MINIO_BUCKET_RECON}" not in compose
    assert ":{$S3_PROXY_PORT:9000}" in caddyfile
    assert "reverse_proxy minio:9000" in caddyfile
    assert "S3_PUBLIC_ENDPOINT=http://localhost:9000" in env_example
