#!/usr/bin/env bash
# =============================================================================
# scripts/down-local.sh — tear down everything scripts/up-local.sh started.
#
# We only kill the PIDs we tracked in /tmp/blocktool/*.pid. The pg / redis
# / minio services are left running so re-running up-local is fast (those
# are reused if their ports are busy, and a sleep-then-SIGTERM on them
# is racy on macOS brew services).
# =============================================================================
set -euo pipefail

PID_DIR="/tmp/blocktool"
LOG_DIR="/tmp/blocktool-logs"

if [ -t 1 ]; then
  GREEN="\033[0;32m"; YELLOW="\033[0;33m"; RED="\033[0;31m"; RESET="\033[0m"
else
  GREEN=""; YELLOW=""; RED=""; RESET=""
fi
log()  { printf "${GREEN}[down-local]${RESET} %s\n" "$*"; }
warn() { printf "${YELLOW}[down-local]${RESET} %s\n" "$*" >&2; }
err()  { printf "${RED}[down-local]${RESET} %s\n" "$*" >&2; }

# Order matters: vite first (no deps), then uvicorn + celery (so they
# stop accepting requests), then any helper PIDs.
for svc in vite uvicorn celery; do
  pidfile="$PID_DIR/$svc.pid"
  if [ -f "$pidfile" ]; then
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      log "Stopping $svc (pid $pid)..."
      kill "$pid" 2>/dev/null || true
      sleep 1
      if kill -0 "$pid" 2>/dev/null; then
        warn "$svc did not exit on SIGTERM, sending SIGKILL"
        kill -9 "$pid" 2>/dev/null || true
      fi
    fi
    rm -f "$pidfile"
  fi
done

# Catch any stragglers (e.g. if a shell wrapper exited and orphaned a
# child process).
stragglers=$(pgrep -f "celery -A src.workers.celery_app" || true)
if [ -n "$stragglers" ]; then
  warn "Killing stray celery workers: $stragglers"
  pkill -f "celery -A src.workers.celery_app" || true
fi
stragglers=$(pgrep -f "uvicorn src.app.main" || true)
if [ -n "$stragglers" ]; then
  warn "Killing stray uvicorn: $stragglers"
  pkill -f "uvicorn src.app.main" || true
fi
stragglers=$(pgrep -f "vite.*5173" || true)
if [ -n "$stragglers" ]; then
  warn "Killing stray vite: $stragglers"
  pkill -f "vite.*5173" || true
fi

log "Down. PG / Redis / MinIO left running (use 'brew services stop ...' to stop those)."
