#!/usr/bin/env bash
# =============================================================================
# scripts/bootstrap.sh — one-shot local dev environment preparation.
# Idempotent: safe to re-run.
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Colors (no-op if not a TTY).
if [ -t 1 ]; then
  GREEN="\033[0;32m"
  YELLOW="\033[0;33m"
  RED="\033[0;31m"
  RESET="\033[0m"
else
  GREEN=""; YELLOW=""; RED=""; RESET=""
fi

log()  { printf "${GREEN}[bootstrap]${RESET} %s\n" "$*"; }
warn() { printf "${YELLOW}[bootstrap]${RESET} %s\n" "$*" >&2; }
err()  { printf "${RED}[bootstrap]${RESET} %s\n" "$*" >&2; }

# ---------------------------------------------------------------------------
# 1. deploy/.env
# ---------------------------------------------------------------------------
ENV_FILE="deploy/.env"
ENV_EXAMPLE="deploy/.env.example"

if [ ! -f "$ENV_EXAMPLE" ]; then
  err "$ENV_EXAMPLE missing — repository is broken."
  exit 1
fi

if [ -f "$ENV_FILE" ]; then
  warn "$ENV_FILE already exists — leaving it alone. Delete it manually to re-init."
else
  log "Creating $ENV_FILE from $ENV_EXAMPLE"
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  warn "Open $ENV_FILE and change the *_dev_pw / *_dev_secret placeholders before exposing this stack publicly."
fi

# ---------------------------------------------------------------------------
# 2. pnpm install (frontend workspaces)
# ---------------------------------------------------------------------------
if command -v pnpm >/dev/null 2>&1; then
  log "Running pnpm install (frontend workspaces)..."
  pnpm install || warn "pnpm install failed — fix and re-run."
else
  warn "pnpm not on PATH — skip frontend install. Install Node 20+ and pnpm 9+ to bootstrap the web app."
fi

# ---------------------------------------------------------------------------
# 3. uv sync (backend workspace) — non-fatal if uv is missing.
# ---------------------------------------------------------------------------
if command -v uv >/dev/null 2>&1; then
  log "Running uv sync (backend workspace)..."
  (cd "$REPO_ROOT" && uv sync --project apps/api --no-install-project) \
    || warn "uv sync failed — fix and re-run."
else
  warn "uv not on PATH — skip backend install. Install uv 0.4+ to bootstrap the API."
fi

# ---------------------------------------------------------------------------
# 4. Print next steps
# ---------------------------------------------------------------------------
cat <<'NEXT'

[bootstrap] All done. Suggested next commands:

  # Bring up the full stack (Postgres / Redis / MinIO / API / Worker / Web / Caddy):
  docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d

  # OR — development mode with bind-mounted source and HMR for the web app:
  docker compose \
    -f deploy/docker-compose.yml \
    -f deploy/docker-compose.dev.yml \
    --env-file deploy/.env \
    up

  # Tail logs for a single service:
  docker compose -f deploy/docker-compose.yml logs -f api

  # Stop and remove everything (keeps volumes):
  docker compose -f deploy/docker-compose.yml down

NEXT
