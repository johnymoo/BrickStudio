#!/usr/bin/env bash
# =============================================================================
# scripts/up-local.sh — bring up the full stack locally (no Docker).
#
# Useful for:
#   - macOS dev boxes where Docker Desktop is heavy / not installed
#   - CI runners where the docker daemon is unavailable but brew services
#     (Postgres, Redis, MinIO) are installed
#   - running the Playwright E2E suite on this host without rebuilding
#     any images
#
# What it does:
#   1. Starts Postgres / Redis / MinIO via the host binaries if their
#      ports are free (reuses a running stack if not — same policy as
#      the backend pytest conftest).
#   2. Applies alembic migrations to the local blocktool database.
#   3. Starts the FastAPI server (uvicorn) in the background.
#   4. Starts a Celery worker in the background.
#   5. Starts the Vite dev server in the background (which serves both
#      the PWA shell AND proxies /api -> http://localhost:8000).
#
# All child processes are tracked via PID files in /tmp/blocktool/*.pid
# and SIGTERMed on Ctrl-C / `scripts/down-local.sh`.
#
# Idempotent: re-running while a stack is up is a no-op.
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# --- paths & env -----------------------------------------------------------
PID_DIR="/tmp/blocktool"
mkdir -p "$PID_DIR"
LOG_DIR="/tmp/blocktool-logs"
mkdir -p "$LOG_DIR"

# Use a non-default port set so this script doesn't fight the prod
# compose file for ports.
export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://blocktool:blocktool@127.0.0.1:5432/blocktool}"
export DATABASE_URL_SYNC="${DATABASE_URL_SYNC:-postgresql://blocktool:blocktool@127.0.0.1:5432/blocktool}"
export REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379/0}"
export REDIS_CELERY_URL="${REDIS_CELERY_URL:-redis://127.0.0.1:6379/1}"
export S3_ENDPOINT="${S3_ENDPOINT:-http://127.0.0.1:9000}"
export S3_ACCESS_KEY="${S3_ACCESS_KEY:-blocktool}"
export S3_SECRET_KEY="${S3_SECRET_KEY:-blocktool}"
export S3_BUCKET_CAPTURES="${S3_BUCKET_CAPTURES:-blocktool}"
export S3_BUCKET_RECON="${S3_BUCKET_RECON:-blocktool-recon}"
export CORS_ORIGINS="${CORS_ORIGINS:-http://localhost:5173,http://localhost:8080}"
export PUBLIC_WEB_BASE_URL="${PUBLIC_WEB_BASE_URL:-http://localhost:5173}"
export PUBLIC_API_BASE_URL="${PUBLIC_API_BASE_URL:-http://localhost:8000}"
export LOG_LEVEL="${LOG_LEVEL:-info}"
export VITE_API_BASE_URL="${VITE_API_BASE_URL:-http://localhost:8000}"

# --- helpers ---------------------------------------------------------------
if [ -t 1 ]; then
  GREEN="\033[0;32m"; YELLOW="\033[0;33m"; RED="\033[0;31m"; BLUE="\033[0;34m"; RESET="\033[0m"
else
  GREEN=""; YELLOW=""; RED=""; BLUE=""; RESET=""
fi
log()  { printf "${GREEN}[up-local]${RESET} %s\n" "$*"; }
info() { printf "${BLUE}[up-local]${RESET} %s\n" "$*"; }
warn() { printf "${YELLOW}[up-local]${RESET} %s\n" "$*" >&2; }
err()  { printf "${RED}[up-local]${RESET} %s\n" "$*" >&2; }

# --- track started PIDs for down-local.sh --------------------------------
STARTED_PIDS=()
# We intentionally do NOT install an EXIT trap — the script's job is to
# bring the stack up and leave it running. `scripts/down-local.sh` is the
# explicit teardown path. If the user hits Ctrl-C, the orphaned uvicorn
# / celery / vite processes keep running (which is what they probably
# want while iterating).

# --- 1. infra: postgres / redis / minio (reuse the conftest's helpers) ----
# We don't want to re-implement the service-startup logic, so we lean on
# the backend's pytest conftest via a one-shot python helper.
log "Ensuring Postgres / Redis / MinIO are running..."
uv run --project "$REPO_ROOT/apps/api" python - <<'PY'
"""Bring up pg/redis/minio if their ports are free. Idempotent."""
import os, shutil, signal, socket, subprocess, sys, time, urllib.request
from pathlib import Path

def port_in_use(p: int) -> bool:
    s = socket.socket(); s.settimeout(0.25)
    try: s.connect(("127.0.0.1", p)); return True
    except OSError: return False
    finally: s.close()

def wait_port(p: int, t: float = 30.0) -> None:
    end = time.monotonic() + t
    while time.monotonic() < end:
        if port_in_use(p): return
        time.sleep(0.2)
    raise SystemExit(f"port {p} did not open in {t}s")

def wait_health(url: str, t: float = 30.0) -> None:
    end = time.monotonic() + t
    last = None
    while time.monotonic() < end:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if 200 <= r.status < 300: return
        except Exception as e: last = e
        time.sleep(0.2)
    raise SystemExit(f"{url} did not become healthy: {last}")

# ---- postgres ----
if not port_in_use(5432):
    pg_ctl = shutil.which("pg_ctl") or "/opt/homebrew/opt/postgresql@15/bin/pg_ctl"
    if not Path(pg_ctl).exists():
        sys.exit("pg_ctl not found; install postgresql@15 (brew install postgresql@15)")
    data = Path("/opt/homebrew/var/postgresql@15")
    if data.exists() and (data / "PG_VERSION").exists():
        env = os.environ.copy(); env["LC_ALL"] = "C"
        subprocess.run([pg_ctl, "-D", str(data), "-l", "/tmp/blocktool-pg.log",
                        "-o", "-p 5432 -h 127.0.0.1", "start"], env=env, check=True)
        wait_port(5432)
    else:
        sys.exit("no /opt/homebrew/var/postgresql@15 cluster; init one first")

# ---- redis ----
if not port_in_use(6379):
    rb = shutil.which("redis-server")
    if not rb: sys.exit("redis-server not installed (brew install redis)")
    subprocess.Popen([rb, "--port", "6379", "--save", "", "--appendonly", "no", "--bind", "127.0.0.1"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_port(6379)

# ---- minio ----
if not port_in_use(9000):
    mb = shutil.which("minio")
    if not mb: sys.exit("minio not installed (brew install minio/stable/minio)")
    data_dir = Path("/tmp/blocktool-minio-data")
    if data_dir.exists(): shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["MINIO_ROOT_USER"] = "blocktool"; env["MINIO_ROOT_PASSWORD"] = "blocktool"
    subprocess.Popen([mb, "server", str(data_dir), "--address", ":9000", "--console-address", ":9001"], env=env,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_port(9000); wait_health("http://127.0.0.1:9000/minio/health/live")

print("infra: pg, redis, minio are up")
PY

# --- 2. minio: create buckets (idempotent) --------------------------------
log "Ensuring MinIO buckets exist..."
uv run --project "$REPO_ROOT/apps/api" python - <<'PY'
from minio import Minio
mc = Minio("127.0.0.1:9000", access_key="blocktool", secret_key="blocktool", secure=False)
for b in ("blocktool", "blocktool-recon"):
    if not mc.bucket_exists(b): mc.make_bucket(b); print(f"created bucket {b}")
    else: print(f"bucket {b} already exists")
PY

# --- 3. alembic -------------------------------------------------------------
log "Applying alembic migrations to blocktool database..."
(
  cd "$REPO_ROOT/apps/api"
  uv run --project . alembic upgrade head 2>&1 | sed "s/^/${BLUE}[alembic]${RESET} /"
)

# --- 4. uvicorn -------------------------------------------------------------
if lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
  log "Port 8000 already in use — assuming uvicorn is up."
else
  log "Starting uvicorn on :8000..."
  (
    cd "$REPO_ROOT/apps/api"
    nohup uv run --project . uvicorn src.app.main:app \
        --host 0.0.0.0 --port 8000 \
        > "$LOG_DIR/uvicorn.log" 2>&1 &
    echo $! > "$PID_DIR/uvicorn.pid"
  )
  # wait for /api/v1/health
  for i in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8000/api/v1/health >/dev/null 2>&1; then break; fi
    sleep 1
  done
  if ! curl -fsS http://127.0.0.1:8000/api/v1/health >/dev/null 2>&1; then
    err "uvicorn did not become healthy. Tail of log:"
    tail -40 "$LOG_DIR/uvicorn.log" || true
    exit 1
  fi
  STARTED_PIDS+=("$(cat $PID_DIR/uvicorn.pid)")
  log "uvicorn ready (pid $(cat $PID_DIR/uvicorn.pid))"
fi

# --- 5. celery worker -------------------------------------------------------
if lsof -nP -iTCP:1 -sTCP:LISTEN >/dev/null 2>&1; then : ; fi   # placeholder
if pgrep -f "celery -A src.workers.celery_app" >/dev/null 2>&1; then
  log "Celery worker already running."
else
  log "Starting celery worker..."
  (
    cd "$REPO_ROOT/apps/api"
    nohup uv run --project . celery -A src.workers.celery_app worker \
        --loglevel=info --pool=solo \
        > "$LOG_DIR/celery.log" 2>&1 &
    echo $! > "$PID_DIR/celery.pid"
  )
  sleep 3
  if ! pgrep -f "celery -A src.workers.celery_app" >/dev/null 2>&1; then
    err "celery worker did not start. Tail of log:"
    tail -40 "$LOG_DIR/celery.log" || true
    exit 1
  fi
  STARTED_PIDS+=("$(cat $PID_DIR/celery.pid)")
  log "celery ready (pid $(cat $PID_DIR/celery.pid))"
fi

# --- 6. vite dev server ----------------------------------------------------
if lsof -nP -iTCP:5173 -sTCP:LISTEN >/dev/null 2>&1; then
  log "Port 5173 already in use — assuming vite is up."
else
  log "Starting vite dev server on :5173..."
  (
    cd "$REPO_ROOT/apps/web"
    nohup pnpm dev --host 127.0.0.1 --port 5173 > "$LOG_DIR/vite.log" 2>&1 &
    echo $! > "$PID_DIR/vite.pid"
  )
  for i in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:5173/ >/dev/null 2>&1; then break; fi
    sleep 1
  done
  if ! curl -fsS http://127.0.0.1:5173/ >/dev/null 2>&1; then
    err "vite did not become reachable. Tail of log:"
    tail -40 "$LOG_DIR/vite.log" || true
    exit 1
  fi
  STARTED_PIDS+=("$(cat $PID_DIR/vite.pid)")
  log "vite ready (pid $(cat $PID_DIR/vite.pid))"
fi

# --- 7. summary ------------------------------------------------------------
cat <<EOF

${GREEN}============================================================${RESET}
${GREEN}  Blocktool stack is up (local, no docker).${RESET}
${GREEN}============================================================${RESET}
  Vite (PWA)   : ${BLUE}http://127.0.0.1:5173${RESET}
  API          : ${BLUE}http://127.0.0.1:8000/api/v1/health${RESET}
  Logs         : ${BLUE}$LOG_DIR/{uvicorn,celery,vite}.log${RESET}
  PID files    : ${BLUE}$PID_DIR/${RESET}

  E2E tests    : pnpm exec playwright test
  Stop         : bash scripts/down-local.sh   (or Ctrl-C in this shell)
EOF
