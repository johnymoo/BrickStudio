"""Static deploy checks for Compose network and port boundaries."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CADDY_LISTENERS = (":{$PROXY_HTTP_PORT:80}", ":443", ":9000")


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _compose_config(*files: str, env: dict[str, str] | None = None) -> dict[str, Any]:
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
        env={**os.environ, **(env or {})},
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


def _environment(config: dict[str, Any], service_name: str) -> dict[str, Any]:
    environment = _service(config, service_name).get("environment") or {}
    if isinstance(environment, list):
        return dict(item.split("=", 1) for item in environment)
    return dict(environment)


def _caddy_listeners() -> set[int]:
    caddyfile = _read("deploy/Caddyfile")
    listeners: set[int] = set()
    for listener in CADDY_LISTENERS:
        if f"{listener} {{" in caddyfile:
            listeners.add(int(listener.rsplit(":", 1)[1].rstrip("}")))
    return listeners


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


def test_dev_api_services_use_dev_web_origin_for_public_web_and_cors() -> None:
    config = _compose_config("deploy/docker-compose.yml", "deploy/docker-compose.dev.yml")

    for service_name in ("api", "worker"):
        environment = _environment(config, service_name)
        assert environment["PUBLIC_WEB_BASE_URL"] == "http://localhost:5173"
        assert environment["CORS_ORIGINS"] == "http://localhost:5173"


def test_custom_s3_proxy_port_publishes_to_stable_caddy_listener() -> None:
    config = _compose_config("deploy/docker-compose.yml", env={"S3_PROXY_PORT": "19000"})
    s3_ports = [port for port in _ports(config, "proxy") if port["target"] == 9000]

    assert len(s3_ports) == 1
    assert s3_ports[0]["published"] == "19000"
    assert s3_ports[0]["host_ip"] == "127.0.0.1"
    assert ":9000 {" in _read("deploy/Caddyfile")
    assert ":{$S3_PROXY_PORT:9000}" not in _read("deploy/Caddyfile")


def test_proxy_published_targets_have_matching_caddy_listeners() -> None:
    config = _compose_config("deploy/docker-compose.yml")
    proxy_targets = {port["target"] for port in _ports(config, "proxy")}

    assert proxy_targets == {80, 443, 9000}
    assert proxy_targets <= _caddy_listeners()


def test_custom_https_host_port_still_targets_stable_caddy_listener() -> None:
    config = _compose_config("deploy/docker-compose.yml", env={"PROXY_HTTPS_PORT": "18443"})
    https_ports = [port for port in _ports(config, "proxy") if port["target"] == 443]

    assert len(https_ports) == 1
    assert https_ports[0]["published"] == "18443"
    assert https_ports[0]["host_ip"] == "127.0.0.1"
    assert 443 in _caddy_listeners()
    assert ":{$PROXY_HTTPS_PORT:443}" not in _read("deploy/Caddyfile")


def test_recon_bucket_is_not_anonymous_and_s3_public_endpoint_uses_proxy() -> None:
    compose = _read("deploy/docker-compose.yml")
    caddyfile = _read("deploy/Caddyfile")
    env_example = _read("deploy/.env.example")

    assert "mc anonymous set download local/${MINIO_BUCKET_RECON}" not in compose
    assert ":9000 {" in caddyfile
    assert "reverse_proxy minio:9000" in caddyfile
    assert "S3_PUBLIC_ENDPOINT=http://localhost:9000" in env_example


def test_root_s3_proxy_handles_browser_cors_and_preflight() -> None:
    caddyfile = _read("deploy/Caddyfile")
    s3_block = caddyfile.split(":9000 {", 1)[1]

    assert "@s3_cors_preflight method OPTIONS" in s3_block
    assert "handle @s3_cors_preflight" in s3_block
    assert 'header Access-Control-Allow-Origin "*"' in s3_block
    assert 'header Access-Control-Allow-Methods "GET, HEAD, PUT, POST, DELETE, OPTIONS"' in s3_block
    assert 'header Access-Control-Allow-Headers "Content-Type, Authorization, X-Requested-With, X-Amz-Date, X-Amz-Content-Sha256, X-Amz-Security-Token"' in s3_block
    assert 'header Access-Control-Max-Age "3600"' in s3_block
    assert 'respond "" 204' in s3_block


def test_api_preflight_allows_library_admin_token_header() -> None:
    caddyfile = _read("deploy/Caddyfile")
    api_block = caddyfile.split("(app_routes) {", 1)[1].split("# Health endpoint", 1)[0]

    assert "@cors_preflight method OPTIONS" in api_block
    assert "handle @cors_preflight" in api_block
    assert 'header Access-Control-Allow-Methods "GET, POST, PUT, PATCH, DELETE, OPTIONS"' in api_block
    assert (
        'header Access-Control-Allow-Headers "Content-Type, Authorization, X-Requested-With, X-Library-Admin-Token"'
        in api_block
    )
