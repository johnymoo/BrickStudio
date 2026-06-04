"""Shared pytest fixtures for the FastAPI backend.

This conftest starts a self-contained stack of Postgres / Redis / MinIO
on standard ports if they aren't already running, applies Alembic migrations
to a throwaway database, and exposes a fully-wired AsyncClient to tests.

It does *not* require Docker — it shells out to the system binaries
(``postgres`` / ``pg_ctl``, ``redis-server``, ``minio``) when needed, and
otherwise treats an already-running stack as a shared dev resource. The
fixture is session-scoped: a single stack handles the whole test run.

Service-startup policy: each helper starts the service only if its port is
free, and the fixtures track every process they started so the session
teardown can stop them again. This makes the suite safe to run on a dev
machine that already has a stack up.
"""
from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Project-root on sys.path (so `from app.config import settings` works).
# The api package lives at `apps/api/src/` (the standard `src/` layout), so
# we add BOTH `apps/api` (for editable installs) and `apps/api/src` (so
# `import app` works without installing the package).
# ---------------------------------------------------------------------------
HERE = Path(__file__).resolve()
API_ROOT = HERE.parents[1]
REPO_ROOT = API_ROOT.parent
SRC_ROOT = API_ROOT / "src"
TESTS_ROOT = HERE.parent  # so `import fixtures.synth_cube` works in tests
for p in (SRC_ROOT, API_ROOT, REPO_ROOT, TESTS_ROOT):
    p_str = str(p)
    if p_str not in sys.path:
        sys.path.insert(0, p_str)

# Test-environment env vars (read by app.config.Settings). Set BEFORE the app
# is imported anywhere.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://blocktool:blocktool@127.0.0.1:5432/blocktool_test",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("REDIS_CELERY_URL", "redis://127.0.0.1:6379/1")
os.environ.setdefault("S3_ENDPOINT", "http://127.0.0.1:9000")
os.environ.setdefault("S3_ACCESS_KEY", "blocktool")
os.environ.setdefault("S3_SECRET_KEY", "blocktool")
os.environ.setdefault("S3_BUCKET_RAW", "blocktool-test-raw")
os.environ.setdefault("S3_BUCKET_RECON", "blocktool-test-recon")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:5173,http://localhost:8080")


# ---------------------------------------------------------------------------
# Service helpers
# ---------------------------------------------------------------------------
def _port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.25)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _wait_for_port(port: int, host: str = "127.0.0.1", timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_in_use(port, host):
            return
        time.sleep(0.2)
    raise RuntimeError(f"port {port} on {host} did not open within {timeout}s")


def _wait_for_health(url: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if 200 <= r.status < 300:
                    return
        except Exception as exc:
            last_err = exc
        time.sleep(0.2)
    raise RuntimeError(f"{url} did not become healthy: {last_err}")


# ---- Postgres -------------------------------------------------------------
_PG_BREW_DATA = Path("/opt/homebrew/var/postgresql@15")


def _postgres_bin() -> str:
    for cand in (
        shutil.which("postgres"),
        "/opt/homebrew/opt/postgresql@15/bin/postgres",
    ):
        if cand and Path(cand).exists():
            return cand
    raise RuntimeError("postgres binary not found; install postgresql@15 or system postgres")


def _start_postgres_if_needed() -> subprocess.Popen[bytes] | None:
    if _port_in_use(5432):
        return None
    pg_ctl = (
        shutil.which("pg_ctl")
        or "/opt/homebrew/opt/postgresql@15/bin/pg_ctl"
    )
    if not Path(pg_ctl).exists():
        raise RuntimeError("pg_ctl not found; install postgresql@15")
    # If the brew-managed data dir exists and has been initialized, use it.
    if _PG_BREW_DATA.exists() and (_PG_BREW_DATA / "PG_VERSION").exists():
        env = os.environ.copy()
        env["LC_ALL"] = "C"
        proc = subprocess.Popen(
            [
                pg_ctl,
                "-D",
                str(_PG_BREW_DATA),
                "-l",
                "/tmp/blocktool-pg.log",
                "-o",
                "-p 5432 -h 127.0.0.1",
                "start",
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        rc = proc.wait(timeout=30)
        if rc != 0:
            (out, err) = proc.communicate(timeout=5)
            raise RuntimeError(f"pg_ctl start failed: {out.decode(errors='ignore')}\n{err.decode(errors='ignore')}")
        _wait_for_port(5432)
        return None  # pg_ctl detached the server, no Popen to track
    # Fallback: initdb a fresh cluster in /tmp.
    data_dir = Path(f"/tmp/blocktool-pgdata-{uuid.uuid4().hex[:6]}")
    data_dir.mkdir(parents=True, exist_ok=True)
    initdb = shutil.which("initdb") or "/opt/homebrew/opt/postgresql@15/bin/initdb"
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    subprocess.run(
        [
            initdb,
            "-D",
            str(data_dir),
            "--auth=trust",
            "--username=blocktool",
            "--no-locale",
            "--encoding=UTF8",
        ],
        check=True,
        env=env,
    )
    postgres = _postgres_bin()
    proc = subprocess.Popen(
        [
            postgres,
            "-D",
            str(data_dir),
            "-p",
            "5432",
            "-h",
            "127.0.0.1",
            "-k",
            str(data_dir),
        ],
        env=env,
    )
    _wait_for_port(5432, timeout=30.0)
    return proc


def _ensure_blocktool_role_and_db() -> None:
    """Create ``blocktool`` role and ``blocktool_test`` db if missing."""
    psql = shutil.which("psql") or "/opt/homebrew/opt/postgresql@15/bin/psql"
    env = os.environ.copy()
    env["PGUSER"] = "blocktool"  # used because we trust-local
    # Check if db exists; create it if not.
    check = subprocess.run(
        [psql, "-h", "127.0.0.1", "-U", "blocktool", "-d", "postgres", "-tAc",
         "SELECT 1 FROM pg_database WHERE datname='blocktool_test'"],
        capture_output=True,
        env=env,
        check=False,
    )
    if check.returncode == 0 and check.stdout.strip() == b"1":
        return
    # Create role and db.
    subprocess.run(
        [psql, "-h", "127.0.0.1", "-U", "blocktool", "-d", "postgres", "-c",
         "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='blocktool') "
         "THEN CREATE ROLE blocktool SUPERUSER LOGIN; END IF; END $$;"],
        check=True,
        env=env,
    )
    subprocess.run(
        [psql, "-h", "127.0.0.1", "-U", "blocktool", "-d", "postgres", "-c",
         "CREATE DATABASE blocktool_test OWNER blocktool"],
        check=False,  # may already exist
        env=env,
    )


# ---- Redis ----------------------------------------------------------------
def _start_redis_if_needed() -> subprocess.Popen[bytes] | None:
    if _port_in_use(6379):
        return None
    redis_bin = shutil.which("redis-server")
    if not redis_bin:
        raise RuntimeError("redis-server not installed (brew install redis)")
    proc = subprocess.Popen(
        [redis_bin, "--port", "6379", "--save", "", "--appendonly", "no", "--bind", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _wait_for_port(6379, timeout=15.0)
    return proc


# ---- MinIO ----------------------------------------------------------------
def _start_minio_if_needed() -> subprocess.Popen[bytes] | None:
    if _port_in_use(9000):
        return None
    minio_bin = shutil.which("minio")
    if not minio_bin:
        raise RuntimeError("minio binary not found (brew install minio/stable/minio)")
    data_dir = Path("/tmp/blocktool-minio-data")
    if data_dir.exists():
        shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["MINIO_ROOT_USER"] = "blocktool"
    env["MINIO_ROOT_PASSWORD"] = "blocktool"
    proc = subprocess.Popen(
        [minio_bin, "server", str(data_dir), "--address", ":9000", "--console-address", ":9001"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _wait_for_port(9000, timeout=30.0)
    _wait_for_health("http://127.0.0.1:9000/minio/health/live", timeout=30.0)
    return proc


# ---------------------------------------------------------------------------
# Session-scope: spin up stack
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session", autouse=True)
def _services() -> Iterator[None]:
    started: list[subprocess.Popen[bytes]] = []
    try:
        pg_proc = _start_postgres_if_needed()
        if pg_proc is not None:
            started.append(pg_proc)
        _ensure_blocktool_role_and_db()
        rd_proc = _start_redis_if_needed()
        if rd_proc is not None:
            started.append(rd_proc)
        mn_proc = _start_minio_if_needed()
        if mn_proc is not None:
            started.append(mn_proc)
        yield
    finally:
        for proc in started:
            try:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        # If we started the brew postgres via pg_ctl, leave it running.
        # (pg_ctl detached the server, so there's no Popen to track.)


# ---------------------------------------------------------------------------
# Session-scope: apply migrations once
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session", autouse=True)
def _apply_migrations(_services: None) -> Iterator[None]:
    from alembic import command
    from alembic.config import Config

    ini_path = API_ROOT / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    # Force-load app metadata via the env.py import path.
    command.upgrade(cfg, "head")
    yield


# ---------------------------------------------------------------------------
# Function-scope: truncate between tests
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
async def _truncate_tables() -> AsyncIterator[None]:
    from sqlalchemy import text

    from db.session import async_session_factory

    factory = async_session_factory()
    async with factory() as session:
        # Only truncate if the migration has actually been applied.
        result = await session.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'captures' LIMIT 1"
            )
        )
        if result.first() is not None:
            await session.execute(
                text("TRUNCATE TABLE assets, jobs, captures RESTART IDENTITY CASCADE")
            )
        await session.commit()
    yield


# ---------------------------------------------------------------------------
# Function-scope: an httpx AsyncClient wired to the FastAPI app
# ---------------------------------------------------------------------------
# Declare pytest plugins we want auto-loaded. The 3D-pipeline tests live
# in ``tests/fixtures/synth_cube.py`` and contribute the
# ``synth_cube_dir`` / ``synth_cube_dir_fresh`` fixtures. pytest's
# default conftest auto-discovery doesn't walk into subpackages, so we
# register the plugin explicitly.
pytest_plugins = ["fixtures.synth_cube"]


@pytest.fixture
async def app_client() -> AsyncIterator:
    from httpx import ASGITransport, AsyncClient

    # Reset settings cache to pick up env overrides.
    from app.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


# Convenience aliases --------------------------------------------------------
@pytest.fixture
def settings():
    from app.config import get_settings

    return get_settings()
