# =============================================================================
# apps/api/Dockerfile
# Multi-stage build for the FastAPI + Celery backend.
# - Stage 1 (deps): install uv + project + dev deps into .venv
# - Stage 2 (runtime): python:3.11-slim with the .venv + project source
#   Includes a stub `pip install` hook for Meshroom / COLMAP / Open3D
#   (the 3D reconstruction task is responsible for filling it in).
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: install dependencies with uv
# -----------------------------------------------------------------------------
FROM python:3.11-slim AS deps

# System deps needed to build asyncpg / psycopg2 wheels and run Open3D / numpy.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        libpq-dev \
        libgeos-dev \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv (pinned; per design.md §4 backend package manager is uv 0.4+).
COPY --from=ghcr.io/astral-sh/uv:0.4.18 /uv /uvx /usr/local/bin/

WORKDIR /repo

# Copy the workspace manifests first for layer caching.
COPY pyproject.toml uv.lock* ./
COPY apps/api/pyproject.toml ./apps/api/

# Install the api package (and dev tools) into a project-local .venv.
# We export to a copyable dir so the runtime stage can pick it up.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --project apps/api --no-install-project --no-dev

# -----------------------------------------------------------------------------
# Stage 2: minimal runtime image
# -----------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy

# System runtime deps only — no compilers here.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        libgeos-c1v5 \
        curl \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1001 app \
    && useradd  --system --uid 1001 --gid app --home /app app

WORKDIR /app

# Pull uv (needed at runtime to optionally install Meshroom / Open3D / etc.).
COPY --from=ghcr.io/astral-sh/uv:0.4.18 /uv /uvx /usr/local/bin/

# Copy the prepared venv from the deps stage.
COPY --from=deps /repo/apps/api/.venv /app/apps/api/.venv
ENV PATH="/app/apps/api/.venv/bin:$PATH"

# Copy the application source.
COPY apps/api /app/apps/api
# uvicorn's CMD resolves modules relative to CWD; pin it to the source tree.
WORKDIR /app/apps/api

# Optional 3D-reconstruction dependency install hook.
# The 3D-reconstruction task is responsible for filling this in:
#   - Meshroom (alicevision) — needs a custom Docker image or large apt deps
#   - COLMAP                   — same; or use `colmap/colmap` base image
#   - Open3D, trimesh, scipy, numpy — installable via uv/pip
# Uncomment / extend the RUN line below when the 3D task lands.
# RUN uv pip install --python /app/.venv/bin/python \
#         open3d trimesh scipy numpy

# Smoke-friendly healthcheck. The real /api/v1/health endpoint is wired in
# by the api-app task; this curl is a placeholder that returns 200 once uvicorn
# is bound. Override in compose if needed.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8000/api/v1/health || exit 1

USER app
EXPOSE 8000

# Use tini as PID 1 for proper signal handling.
ENTRYPOINT ["/usr/bin/tini", "--"]

# Default CMD runs the FastAPI app via uvicorn. The module path is
# ``src.app.main:app`` (relative to WORKDIR which we set to /app/apps/api).
CMD ["uvicorn", "src.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
