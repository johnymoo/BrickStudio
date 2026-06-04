# 积木建模工具

基于手机 + 免费工具的积木建模 Web 应用。用户拍照 → 自动 3D 重建 → 沉淀为可复用零件库 → 后续拼搭设计。

## 当前阶段
**首轮交付 (v0.1)**: 跑通最小闭环
- 移动 Web 拍照上传
- 后端 3D 重建 (Meshroom + COLMAP)
- 3D 查看器

后续阶段: 拼搭编辑器 / 零件库 / 网格手动编辑 / 组合推荐 / 用户系统。

## 技术栈
- 前端: React + TypeScript + Vite + TailwindCSS + Three.js (R3F) + PWA
- 后端: FastAPI + Celery + Redis + PostgreSQL
- 3D: Meshroom (AliceVision) + COLMAP + Open3D
- 存储: MinIO (S3 兼容)
- 容器: Docker Compose

## 快速开始

### 一键起全栈 (docker compose)

```bash
# 准备 (首次)
cp deploy/.env.example deploy/.env
# 按需修改 deploy/.env (生产前请把 _dev_pw / _dev_secret 占位符换掉)

# 一键起 Postgres / Redis / MinIO / API / Worker / Web / Caddy / migrate
bash scripts/up.sh

# 访问 URL (脚本会一并打印)
#   前端 PWA       http://localhost         (Caddy 反代,80 端口)
#   前端 (开发)    http://localhost:5173
#   API            http://localhost:8000/api/v1/health
#   API via Caddy  http://localhost/api/v1/health
#   MinIO 控制台   http://localhost:9001   (账密在 deploy/.env)

# 停
docker compose -f deploy/docker-compose.yml down
# 重置 (CAUTION: 删 Postgres / MinIO 持久化)
docker compose -f deploy/docker-compose.yml down -v
```

`scripts/up.sh` 做的事:
- `deploy/.env` 缺则从 `.env.example` 拷一份
- `docker compose -f deploy/docker-compose.yml up -d --build`
- 轮询 `docker compose ps` 等所有服务 healthy / `migrate` exited (0)
- 兜底再跑一次 `alembic upgrade head` (host 侧)
- 打印 URL 列表

支持 `bash scripts/up.sh --dev` (叠加 docker-compose.dev.yml, Vite + HMR + Playwright 镜像),
`--no-migrate`, `--logs` (起完自动追日志)。

### 跑 E2E 测试 (Playwright)

E2E 假定 stack 已经起来 (走 `bash scripts/up.sh` 或 `bash scripts/up-local.sh`)。
Chromium 镜像已经装在 `apps/web/Dockerfile.dev` 里;本机直接跑:

```bash
# 装 Playwright + Chromium 浏览器 (沙箱外跑一次, 沙箱内已经预装在 dev 镜像里)
pnpm install
pnpm --filter @blocktool/web exec playwright install --with-deps chromium

# 跑测试
pnpm --filter @blocktool/web exec playwright test
# 或: pnpm e2e

# 单跑 smoke / full-flow
pnpm --filter @blocktool/web exec playwright test tests/e2e/smoke.spec.ts
pnpm --filter @blocktool/web exec playwright test tests/e2e/full-flow.spec.ts

# 通过 Caddy (默认 80) 跑 full-flow
BLOCKTOOL_E2E_BASE_URL=http://localhost pnpm --filter @blocktool/web exec playwright test
# 通过 Vite dev (5173) 跑
BLOCKTOOL_E2E_BASE_URL=http://localhost:5173 pnpm --filter @blocktool/web exec playwright test
```

跑完会落两份产物:
- `tests/e2e/screenshots/final.png` — full-flow.spec.ts 最后一张 3D 查看器截图
- `tests/e2e/playwright-report/index.html` — HTML 报告 (`--reporter=html`)

> 沙箱无 docker: 沙箱里没有 docker 守护进程, 不能跑 `bash scripts/up.sh`;
> E2E 必须在 docker enabled 的开发机 / CI 上跑。沙箱内我已经用
> `scripts/up-local.sh` (走本机 brew 的 Postgres / Redis / MinIO 二进制)
> + `pnpm dev` 验证过 smoke + full-flow 都能跑通, 截图也保存了。

### 单跑

```bash
# 仅跑 dev overlay (HMR + reload)
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.dev.yml up

# 在 docker 沙箱外起本地 stack (用 brew 的 pg/redis/minio)
bash scripts/up-local.sh
bash scripts/down-local.sh
```

## 目录结构
参见 [docs/design.md](docs/design.md) 第 5 节。

## 设计文档
- [docs/design.md](docs/design.md) - 总体设计与首轮契约
- [docs/api-contract.md](docs/api-contract.md) - API 详细契约
- [docs/data-model.md](docs/data-model.md) - 数据模型

## 开发
参见 [docs/design.md](docs/design.md) 各章节的规范。
