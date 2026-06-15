# 积木建模工具

基于手机 + 免费工具的积木建模 Web 应用。用户拍照 → 自动 3D 重建 → 沉淀为可复用零件库 → 后续拼搭设计。

## 当前阶段

**阶段二 (v0.2)**: 用户能自己用起来
- 拍照 <PRIVATE_PERSON> 智能引导 + 删/重拍 + 实时清晰度提示
- 后端真 3D 重建 (COLMAP 真 SfM, ≥8 张照片; Open3D fallback, 4-7 张)
- 5 阶段进度可视化 (下载 / 稀疏重建 / 稠密重建 / 网格 / 完成)
- 端到端 Playwright (8 张真实照片 fixture)

后续阶段: 拼搭编辑器 / 零件库 / 网格手动编辑 / 组合推荐 / 用户系统。

## 技术栈
- 前端: React + TypeScript + Vite + TailwindCSS + Three.js (R3F) + PWA
- 后端: FastAPI + Celery + Redis + PostgreSQL
- 3D: COLMAP (SfM) + Open3D (multi-photo fallback) + Blender (可选网格清理)
- 存储: MinIO (S3 兼容)
- 容器: Docker Compose

---

## 自己用起来 — 5 分钟指南

> 第一次跑, 大概 5-10 分钟 (其中 `brew install colmap` 要 30-60 分钟, 但只需一次)。

### 准备 (一次性)

1. **装 COLMAP** (真 3D 重建的核心, ≥8 张照片时启用):

   > 💡 **COLMAP 装包提示**: macOS 用 `brew install colmap` (大依赖: Ceres / CGAL / LSDSVO, 编译 30-60 分钟)。
   > 也可以用 `apt install colmap` (Ubuntu 22.04+) 或 `conda install -c conda-forge colmap`。
   > 装完跑 `colmap help` 应该看到 "COLMAP 3.x" 或 "COLMAP 4.x"。

   ```bash
   brew install colmap
   colmap help  # 确认能跑, 应该看到 "COLMAP 4.0.x"
   ```

   **没有自动检测到?** 设 `COLMAP_BIN` 环境变量指向 binary 位置:

   ```bash
   # macOS (Apple Silicon)
   export COLMAP_BIN=/opt/homebrew/bin/colmap

   # macOS (Intel)
   export COLMAP_BIN=/usr/local/bin/colmap

   # Linux
   export COLMAP_BIN=/usr/local/bin/colmap
   ```

   也可以写到 `deploy/.env` 里:

   ```bash
   # deploy/.env
   COLMAP_BIN=/opt/homebrew/bin/colmap
   RECONSTRUCT_MIN_IMAGES_FOR_COLMAP=8   # 走 COLMAP 路径最少照片数
   ```

   没装也不影响用, 4-7 张照片或 COLMAP 失败时自动 fallback 到 Open3D 路径。

2. **装 Docker**: 用 [Docker Desktop](https://www.docker.com/products/docker-desktop/)
   或 OrbStack。镜像大约 2-3 GB, 第一次 `docker compose up` 会拉 + 构建。

3. **克隆 + 配 env**:
   ```bash
   git clone <repo>
   cd 积木工具
   cp deploy/.env.example deploy/.env
   # 按需改 deploy/.env (生产前把 _dev_pw / _dev_secret 占位符换掉)
   ```

4. **起全栈**:
   ```bash
   bash scripts/up.sh
   ```
   第一次会比较慢 (构建 8 个服务的镜像 + 拉 base 镜像 + 跑 alembic 迁移)。
   脚本会轮询 `docker compose ps` 等所有服务 healthy, 然后打印:

   ```
   全部 healthy ✓
     前端 PWA       http://localhost
     前端 (开发)    http://localhost:5173
     API            http://localhost:8000/api/v1/health
     MinIO 控制台   http://localhost:9001
   ```

   停: `docker compose -f deploy/docker-compose.yml down`

   手机或局域网设备访问 `http://<LAN-IP>:5173` 时, `deploy/.env`
   里的 `S3_PUBLIC_ENDPOINT` 也要改成浏览器能直接访问的 MinIO API
   根地址, 例如 `http://<LAN-IP>:9000`。不要写 `/minio` 这种路径,
   S3 预签名会绑定 host/path, 改写后会触发 `SignatureDoesNotMatch`。

### 拍积木 (浏览器)

1. **打开网页**: 浏览器访问 <http://localhost> (Caddy 80 端口) 或
   <http://localhost:5173> (Vite dev 模式)
2. **点 "📷 开始新的采集"** 进入拍照页
3. **选拍照模式** (顶部 segmented control):
   - **📱 围绕物体走** (`phone_walkaround`) — 推荐, 8 张引导, 30°/45°/60°/90° 高度
   - **🔄 转盘** (`studio_turntable`) — 12 张均匀角度
   - **📸 快速拍** (`quick_snapshot`) — 4 张兜底
4. **浏览器请求摄像头权限**, 允许
5. **把积木放白纸中央**, 周围放尺子 (用作标尺, 阶段三做比例标定)
6. **看 "✓ 清晰, 可以拍"** 绿色提示就按 "拍照", 模糊的话等一下再拍
7. **拍够 ≥8 张** (走 COLMAP 真 SfM 路径) 或 至少 4 张 (走 Open3D fallback)
8. **可以删照片**: 4 列网格里每张右上角垃圾桶图标, 删掉模糊的那张
9. **点 "提交 (N 张)"**, 实时看 5 阶段进度条
10. **10-60 秒后** 状态变 "✅ 完成", 3D viewer 出现
11. **点 "重置 / 线框"** 切换查看模式, 或点 3D viewer 拖动旋转
12. **下载 GLB**: dev 模式下点 viewer 旁的 download 链接, 拖到
    [Three.js Online Viewer](https://threejs.org/editor/) 或 Blender 验证

### 模型丢了 / 重建失败

| 现象 | 原因 | 修法 |
|---|---|---|
| 状态卡 "上传中" 很久 | Celery worker 没起 | 查 `docker compose ps` 看 `worker` healthy |
| 状态到 "失败" | 缺纹理 / 纯色 / 拍摄角度太近 | 删掉模糊的几张, 重新拍 (背景别用纯色, 加点花纹) |
| 状态完成但 mesh 是球 | 走的 Open3D fallback icosphere, 不是真 SfM | 装 COLMAP + 拍 ≥8 张 (用有阴影的背景) |
| 3D 预览显示 `模型加载失败` / HTTP 403 | `S3_PUBLIC_ENDPOINT` 不是浏览器可访问的 MinIO API 根地址 | 改 `deploy/.env` 为 `http://<LAN-IP>:9000`, 重启 API / worker |
| 拍出来像素 < 8 张提示 | 拍照张数 < 8 | 多拍几张, ≥8 张走真 SfM |
| 拍出来像素 > 20 张报错 | 后端硬限 20 | 删到 20 张以内 |

调试用:

```bash
# 看 job 详情
JOB=<uuid>
curl -sS http://localhost:8000/api/v1/jobs/$JOB | python3 -m json.tool

# 看 celery worker 日志
docker compose -f deploy/docker-compose.yml logs -f worker
```

### 下一步

- 拿 GLB 拖到 [Blender](https://www.blender.org/) 微调 (boolean clean + UV unwrap)
- 阶段三会加零件库 CRUD + 拼搭编辑器, 拼搭出 3D 积木模型
- 阶段四加网格手动编辑 UI (R3F)

---

## 开发者快速开始

> 给贡献者看的 — 5 分钟指南是给终端用户的, 这里讲怎么改代码、跑测试。

### 0. 准备

跟上面 5 分钟指南的前 3 步一样 (装 COLMAP 可选、装 Docker、clone + env)。

### 1. 一键起全栈 (docker compose)

```bash
# 一键起 Postgres / Redis / MinIO / API / Worker / Web / Caddy / migrate
bash scripts/up.sh

# 访问 URL (脚本会一并打印)
#   前端 PWA       http://localhost         (Caddy 反代, 80 端口)
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

支持 `bash scripts/up.sh --dev` (叠加 docker-compose.dev.yml, Vite + HMR + Playwright 镜像),
`--no-migrate`, `--logs` (起完自动追日志)。

局域网调试时同步修改:

```env
PUBLIC_WEB_BASE_URL=http://<LAN-IP>:5173
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,http://<LAN-IP>:5173
S3_PUBLIC_ENDPOINT=http://<LAN-IP>:9000
```

`S3_ENDPOINT` 仍是 API / worker 内部访问 MinIO 的地址, Docker 环境通常保持
`http://minio:9000`; `S3_PUBLIC_ENDPOINT` 才是写入预签名下载 URL 的浏览器可见地址。

### 2. 跑测试

#### 前端契约 (vitest)

```bash
# apps/web vitest
cd apps/web && pnpm test
# 47/47 passed in ~3s
```

#### 后端契约 (pytest)

```bash
# apps/api pytest (假设 stack 起来了, 用 testcontainers 或本地 brew pg/redis/minio)
cd apps/api && uv run --project . pytest
# 30+ passed (几个 pre-existing GLB-size failures 见"已知限制")
```

#### 端到端 (Playwright)

E2E 假定 stack 已经起来 (走 `bash scripts/up.sh` 或 `bash scripts/up-local.sh`):

```bash
# 装 Playwright + Chromium 浏览器 (沙箱外跑一次, 沙箱内已经预装在 dev 镜像里)
pnpm install
pnpm --filter @blocktool/web exec playwright install --with-deps chromium

# 跑全部 E2E
pnpm --filter @blocktool/web exec playwright test
# 或: pnpm e2e

# 单跑 smoke / full-flow
pnpm --filter @blocktool/web exec playwright test tests/e2e/smoke.spec.ts
pnpm --filter @blocktool/web exec playwright test tests/e2e/full-flow.spec.ts
# live library: backend and test must use the same admin token
export LIBRARY_ADMIN_TOKEN=<your-admin-token>
bash scripts/up.sh
pnpm exec playwright test tests/e2e/library-live.spec.ts

# 只跑 8-photo 真实照片用例 (阶段二新增)
pnpm --filter @blocktool/web exec playwright test tests/e2e/full-flow.spec.ts --grep "8 photos"

# 通过 Caddy (默认 80) 跑 full-flow
BLOCKTOOL_E2E_BASE_URL=http://localhost pnpm --filter @blocktool/web exec playwright test
# 通过 Vite dev (5173) 跑
BLOCKTOOL_E2E_BASE_URL=http://localhost:5173 pnpm --filter @blocktool/web exec playwright test
```

跑完会落 3 份产物:
- `tests/e2e/screenshots/final.png` — 4-photo 测试最终 3D 查看器截图
- `tests/e2e/screenshots/final-8photos.png` — 8-photo 测试最终截图 (阶段二新增)
- `tests/e2e/playwright-report/index.html` — HTML 报告 (`--reporter=html`)

> 沙箱无 docker: 沙箱里没有 docker 守护进程, 不能跑 `bash scripts/up.sh`;
> E2E 必须在 docker enabled 的开发机 / CI 上跑。沙箱内可以用
> `scripts/up-local.sh` (走本机 brew 的 Postgres / Redis / MinIO 二进制)
> + `pnpm dev` 验证 smoke + full-flow 都能跑通。

#### 真实照片 fixture 重新生成

```bash
./.venv/bin/python tests/e2e/fixtures/generate_real_bricks.py
# 输出 tests/e2e/fixtures/real-bricks/brick-{00..11}.jpg + preview*.png
# 改 noise / 角度 / 几何见脚本顶部 docstring
```

### 3. 单跑

```bash
# 仅跑 dev overlay (HMR + reload)
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.dev.yml up

# 在 docker 沙箱外起本地 stack (用 brew 的 pg/redis/minio)
bash scripts/up-local.sh
bash scripts/down-local.sh
```

### 4. 目录结构

参见 [docs/design.md](docs/design.md) 第 5 节。

### 5. 设计文档

- [docs/design.md](docs/design.md) - 总体设计与首轮契约
- [docs/design-phase2.md](docs/design-phase2.md) - 阶段二契约 (拍照 UX + 真 3D 重建)
- [docs/api-contract.md](docs/api-contract.md) - API 详细契约
- [docs/data-model.md](docs/data-model.md) - 数据模型
- [docs/ar-capture-recognition-design.md](docs/ar-capture-recognition-design.md) - AR 采集识别设计规格 (v0.5)
- [docs/ar-capture-recognition-plan.md](docs/ar-capture-recognition-plan.md) - AR 采集识别实现计划 (v0.5)
- [docs/ROADMAP.md](docs/ROADMAP.md) - 版本路线图 (v0.1 → v0.5)
- [docs/v0.5-plan.md](docs/v0.5-plan.md) - v0.5 开发计划

### 6. 开发

参见 [docs/design.md](docs/design.md) 各章节的规范。

---

## 已知限制

### COLMAP

- **brew 编译慢**: `brew install colmap` 大依赖 (Ceres, CGAL, LSDSVO), 30-60 分钟。
  第一次 `docker compose up` 镜像里不含 COLMAP (避免镜像臃肿), worker 装在 host
  上, 然后 mount 进容器, 或者 `COLMAP_BIN=` 留空走 Open3D fallback。
- **8+ 张纯色块图片无 SIFT 特征**: COLMAP `mapper` 找不到初始图像对, 100% fallback。
  拍实物没事, 但用 matplotlib 渲染的合成测试图 (e.g. `real-bricks/` fixture) 会
  必触发 fallback。
- **20+ 张照片会非常慢**: 阶段二硬限 20 张 (后端 `reconstruct_max_images=20`)。

### Open3D fallback

- **不是真 photogrammetry**: 0.18 没有真 SfM, Open3D 路径用 trimesh 估位姿 +
  icosahedron 兜底 mesh, 出来的是 12 顶点 20 面的球 (948 字节), **仅证明 pipeline
  跑通了**, 实际出不了真模型。
- **GLB size 阈值**: 设计 §6.1 要求 "文件 > 5KB", 实际 stub icosphere ~1KB。
  colmap-integration 任务已标 out-of-scope; 测试用 `> 256B + glTF magic` 替代。
  阶段三把 stub 换成真 Poisson 重建后, 这条会自然满足。

### 4-7 张 vs 8+ 张 路径差异

| 张数 | 走的 runner | pipeline_used | mesh 大小 | 用户场景 |
|---|---|---|---|---|
| 4-7 | Open3D 阶段一路径 | `open3d_fallback` | ~1 KB (sphere) | 拍得少, 兜底 |
| 8+ 且 COLMAP 可用 | COLMAP 真 SfM | `colmap_sfm` | 10-100 KB (真照片) | 推荐 |
| 8+ 但 COLMAP 不可用 / 失败 | Open3D 多图 | `open3d_pure_photogrammetry` | ~1 KB (sphere) | 没装 colmap / 纯色背景 |

### Docker Desktop 大小

完整 stack (Postgres + Redis + MinIO + API + Worker + Web + Caddy + Playwright)
大约 4-5 GB, 第一次构建 10-15 分钟。嫌大可以走 `bash scripts/up-local.sh` +
`pnpm dev` 替代。

### 拍照 UX 限制

- **手机浏览器 HTTPS**: `localhost` / `https://` 之外 `getUserMedia` 不工作。
  跨设备访问需要 `ngrok http 5173` 或 `cloudflared tunnel`。
- **摄像头权限**: 用户拒绝后 UI 显示 "无摄像头 / 权限被拒", 引导用 "从相册选择"
  file input 兜底。
- **删除单张后引导角标不会自动重排**: 4 列网格直接消失那张, 引导角标要等下次拍同角度才会亮。

### E2E 测试限制

- **单 worker**: `playwright.config.ts` 强制 `workers: 1`, 因为 API 只有一个 Celery worker。
- **10 分钟默认超时**: 8+ 张 + COLMAP 跑可能要几分钟, `BLOCKTOOL_E2E_TIMEOUT_MS` 可覆盖。
- **没装 COLMAP 跑 8-photo 测试**: 必走 `open3d_pure_photogrammetry`, 出来 1KB icosphere, 测试断言 `pipeline_used` match 三个值之一就过。

### 后端契约未对齐 (留给阶段三)

- `JobRead` schema 没暴露 `pipeline_used` 和 `eta_seconds`, 这两个字段只在
  `AssetRead.meta.pipeline_used` 和 SSE 事件 `eta_seconds` 上有。前端
  `useJobStore` 类型有, 但 API 调 `/api/v1/jobs/{id}` 拿不到。
- 阶段三会补: 把 `pipeline_used` + `eta_seconds` 加到 `JobRead`, 前端直接读
  `job.pipelineUsed`, 不再绕到 asset endpoint。

### Meshlab / Meshroom 路径 (实验性)

`pipelines/meshroom.py` 是 AliceVision Meshroom 的 wrapper, **未集成到 Celery
路由**。要启用得自己改 `reconstruct.py` 的 `_select_and_run_pipeline`, 把
`MeshroomRunner` 加到 dispatch 表。Meshroom GUI 跑一遍大概 30-60 分钟, 太慢,
不在阶段二范围。

---

*阶段二 v0.2 · 设计契约见 [docs/design-phase2.md](docs/design-phase2.md) · 端到端交付报告见 [tests/e2e/deliverable-phase2.md](tests/e2e/deliverable-phase2.md)*
