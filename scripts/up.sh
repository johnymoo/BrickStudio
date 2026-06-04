#!/usr/bin/env bash
# =============================================================================
# scripts/up.sh — bring up the full blocktool stack with one command.
#
# What it does:
#   1. Ensures deploy/.env exists (copies from .env.example if missing).
#   2. Runs `docker compose -f deploy/docker-compose.yml up -d --build`.
#   3. Waits for the core services to become healthy (postgres, redis,
#      minio, api, worker, web, proxy, migrate).
#   4. Runs `alembic upgrade head` inside the api container as a belt-
#      and-braces safety net (the in-compose `migrate` service already
#      does this, but running alembic from the host means you can target
#      a remote DB without rebuilding the image).
#   5. Prints the URLs the user can hit.
#
# Idempotent: re-running while a stack is up is a no-op for compose and a
# no-op for alembic (alembic is a no-op when at HEAD).
#
# Usage:
#   bash scripts/up.sh              # default: production-ish compose
#   bash scripts/up.sh --dev        # also layer docker-compose.dev.yml
#   bash scripts/up.sh --no-migrate # skip the explicit alembic step
#   bash scripts/up.sh --logs       # tail logs after bringing the stack up
#
# Exit codes:
#   0 — stack is healthy and reachable
#   1 — docker missing / .env creation failed
#   2 — a service did not become healthy within the deadline
#   3 — alembic migration failed
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# --- color / logging --------------------------------------------------------
if [ -t 1 ]; then
  GREEN="\033[0;32m"; YELLOW="\033[0;33m"; RED="\033[0;31m"; BLUE="\033[0;34m"; RESET="\033[0m"
else
  GREEN=""; YELLOW=""; RED=""; BLUE=""; RESET=""
fi
log()  { printf "${GREEN}[up]${RESET} %s\n" "$*"; }
info() { printf "${BLUE}[up]${RESET} %s\n" "$*"; }
warn() { printf "${YELLOW}[up]${RESET} %s\n" "$*" >&2; }
err()  { printf "${RED}[up]${RESET} %s\n" "$*" >&2; }

# --- flag parsing -----------------------------------------------------------
COMPOSE_FILES=("docker-compose.yml")
RUN_MIGRATE=1
TAIL_LOGS=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dev)
      COMPOSE_FILES+=("docker-compose.dev.yml")
      shift
      ;;
    --no-migrate)
      RUN_MIGRATE=0
      shift
      ;;
    --logs)
      TAIL_LOGS=1
      shift
      ;;
    -h|--help)
      sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      err "unknown flag: $1"
      exit 1
      ;;
  esac
done

# --- compose invocation helper ---------------------------------------------
COMPOSE=(docker compose)
for f in "${COMPOSE_FILES[@]}"; do
  COMPOSE+=(-f "deploy/$f")
done
COMPOSE+=(--env-file "deploy/.env")
log "compose invocation: ${COMPOSE[*]} up -d --build"

# --- 1. deploy/.env --------------------------------------------------------
ENV_FILE="deploy/.env"
ENV_EXAMPLE="deploy/.env.example"
if [ ! -f "$ENV_FILE" ]; then
  if [ ! -f "$ENV_EXAMPLE" ]; then
    err "$ENV_EXAMPLE missing — repository is broken."
    exit 1
  fi
  log "Creating $ENV_FILE from $ENV_EXAMPLE"
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  warn "Open $ENV_FILE and change the *_dev_pw / *_dev_secret placeholders before exposing this stack publicly."
fi

# --- 2. docker presence check ----------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  err "docker not on PATH. Install Docker Desktop (or compatible) and re-run."
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  err "docker daemon not reachable. Is the Docker Desktop app running?"
  exit 1
fi

# --- 3. bring the stack up -------------------------------------------------
log "Building images and starting services (this can take a few minutes on first run)..."
"${COMPOSE[@]}" up -d --build

# --- 4. wait for health ----------------------------------------------------
# Services we wait on, in the order we want to see them light up.
# The in-compose `migrate` service exits 0 on success, so we wait on it
# via `service_completed_successfully` (encoded as a normal "running" state
# with a healthy status, depending on compose version).
SERVICES=(
  postgres
  redis
  minio
  minio-init
  migrate
  api
  worker
  web
  proxy
)

HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-180}"  # seconds total per service
POLL_INTERVAL=2

log "Waiting for services to become healthy (timeout ${HEALTH_TIMEOUT}s each)..."
for svc in "${SERVICES[@]}"; do
  info "  - $svc"
  deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))
  ok=0
  while [ "$(date +%s)" -lt "$deadline" ]; do
    # `docker compose ps --format json` is the modern way; fall back gracefully.
    line="$("${COMPOSE[@]}" ps --format json "$svc" 2>/dev/null | head -n 1 || true)"
    if [ -z "$line" ]; then
      sleep "$POLL_INTERVAL"
      continue
    fi
    state=$(printf '%s' "$line" | python3 -c "import json,sys; d=json.loads(sys.stdin.read() or '{}'); print(d.get('Health','-') + '|' + d.get('State','-'))" 2>/dev/null || echo "-")
    health="${state%%|*}"
    runstate="${state##*|}"
    if [ "$svc" = "migrate" ]; then
      # migrate is a one-shot: "exited (0)" is the happy path.
      if echo "$runstate" | grep -qiE "exited \(0\)"; then ok=1; break; fi
      if echo "$runstate" | grep -qiE "exited \(1\)|exited (?!0)|dead"; then
        err "migrate container exited with failure (state: $runstate)"
        "${COMPOSE[@]}" logs --tail=80 migrate || true
        exit 3
      fi
    else
      if [ "$health" = "healthy" ]; then ok=1; break; fi
    fi
    sleep "$POLL_INTERVAL"
  done
  if [ "$ok" -ne 1 ]; then
    err "service $svc did not become healthy within ${HEALTH_TIMEOUT}s"
    "${COMPOSE[@]}" logs --tail=80 "$svc" || true
    exit 2
  fi
done

# --- 5. alembic safety net -------------------------------------------------
# The in-compose `migrate` service already ran `alembic upgrade head`. We
# re-run it from the host in case the operator changed the DB target
# (deploy/.env DATABASE_URL points at a remote host, for instance).
if [ "$RUN_MIGRATE" -eq 1 ]; then
  log "Re-applying alembic migrations from the host (idempotent)..."
  if command -v uv >/dev/null 2>&1; then
    (
      set -a; source "$ENV_FILE"; set +a
      cd "$REPO_ROOT/apps/api"
      uv run --project . alembic upgrade head 2>&1 | sed "s/^/${BLUE}[alembic]${RESET} /"
    ) || { err "alembic upgrade head failed"; exit 3; }
  else
    warn "uv not on PATH — skipping host-side alembic. The in-compose 'migrate' service has already run."
  fi
fi

# --- 6. print URLs ---------------------------------------------------------
PROXY_PORT="$(grep '^PROXY_HTTP_PORT=' "$ENV_FILE" | cut -d= -f2 | tr -d '"' || echo 80)"
API_PORT="$(grep '^API_PORT=' "$ENV_FILE" | cut -d= -f2 | tr -d '"' || echo 8000)"
WEB_PORT="$(grep '^WEB_DEV_PORT=' "$ENV_FILE" | cut -d= -f2 | tr -d '"' || echo 8080)"
MINIO_API_PORT="$(grep '^MINIO_API_PORT=' "$ENV_FILE" | cut -d= -f2 | tr -d '"' || echo 9000)"
MINIO_CONSOLE_PORT="$(grep '^MINIO_CONSOLE_PORT=' "$ENV_FILE" | cut -d= -f2 | tr -d '"' || echo 9001)"

cat <<EOF

${GREEN}============================================================${RESET}
${GREEN}  Blocktool stack is up.${RESET}
${GREEN}============================================================${RESET}
  Frontend (PWA)        : ${BLUE}http://localhost:${WEB_PORT}${RESET}
  Frontend via proxy    : ${BLUE}http://localhost:${PROXY_PORT}${RESET}
  API (direct)          : ${BLUE}http://localhost:${API_PORT}/api/v1/health${RESET}
  API via proxy         : ${BLUE}http://localhost:${PROXY_PORT}/api/v1/health${RESET}
  MinIO API             : ${BLUE}http://localhost:${MINIO_API_PORT}${RESET}
  MinIO console         : ${BLUE}http://localhost:${MINIO_CONSOLE_PORT}${RESET}

  E2E tests:   pnpm install && pnpm playwright install --with-deps chromium && pnpm playwright test
  Stop:        ${COMPOSE[*]} down
  Reset data:  ${COMPOSE[*]} down -v   (CAUTION: deletes Postgres / MinIO volumes)

EOF

# --- 7. optional log tail --------------------------------------------------
if [ "$TAIL_LOGS" -eq 1 ]; then
  info "Tailing logs (Ctrl-C to detach)..."
  "${COMPOSE[@]}" logs -f --tail=40
fi
