# =============================================================================
# apps/web/Dockerfile
# Multi-stage build for the React + Vite PWA.
# - Stage 1 (deps): install pnpm + node modules
# - Stage 2 (build): run vite build (typecheck + bundle)
# - Stage 3 (runtime): nginx serving the static bundle
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: install dependencies
# -----------------------------------------------------------------------------
FROM node:20-alpine AS deps
RUN corepack enable && corepack prepare pnpm@9.12.1 --activate
WORKDIR /repo

# Copy only the manifests first for better layer caching.
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml* ./
COPY apps/web/package.json ./apps/web/
COPY tsconfig.base.json ./
COPY eslint.config.mjs prettier.config.mjs ./

# Fetch deps for the web workspace package only.
RUN pnpm install --frozen-lockfile --filter @blocktool/web...

# -----------------------------------------------------------------------------
# Stage 2: build the production bundle
# -----------------------------------------------------------------------------
FROM node:20-alpine AS build
RUN corepack enable && corepack prepare pnpm@9.12.1 --activate
WORKDIR /repo

# Reuse node_modules from deps stage.
COPY --from=deps /repo/node_modules ./node_modules
COPY --from=deps /repo/apps/web/node_modules ./apps/web/node_modules

# Copy the actual source.
COPY package.json pnpm-workspace.yaml ./
COPY tsconfig.base.json ./
COPY apps/web ./apps/web

WORKDIR /repo/apps/web
RUN pnpm build

# -----------------------------------------------------------------------------
# Stage 3: serve the static bundle with nginx
# -----------------------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

# Drop the default config and ship a SPA-friendly one.
RUN rm -f /etc/nginx/conf.d/default.conf
COPY <<'EOF' /etc/nginx/conf.d/default.conf
server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    # Long-cache hashed Vite assets.
    location /assets/ {
        expires 1y;
        add_header Cache-Control "public, immutable";
        try_files $uri =404;
    }

    # SPA fallback — every unknown path falls back to index.html so that
    # vite-plugin-pwa's client-side router works.
    location / {
        try_files $uri $uri/ /index.html;
        add_header Cache-Control "no-cache";
    }

    # Health endpoint for the docker-compose healthcheck.
    location = /healthz {
        access_log off;
        return 200 "ok\n";
        add_header Content-Type text/plain;
    }
}
EOF

# Copy the built static files.
COPY --from=build /repo/apps/web/dist /usr/share/nginx/html

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1/healthz || exit 1

CMD ["nginx", "-g", "daemon off;"]
