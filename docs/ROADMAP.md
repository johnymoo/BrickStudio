# 积木建模工具 — 路线图 (v0.1 → v0.5)

> 项目代号 **BrickStudio**。本文件维护"接下来要做什么、做到什么程度算完"。

## 状态总览

| 阶段 | 状态 | 产物 | commit |
|---|---|---|---|
| v0.1 基础后端 | ✅ 已发布 | FastAPI + PostgreSQL + MinIO + Celery 骨架, 12/12 测试 | `e34a8b4` |
| v0.2 拍照 UX + SfM 重建 | ✅ 已发布 | 智能拍照 N 张, COLMAP docker 集成, Open3D fallback ≥8 张, Playwright E2E | `cb94a76` |
| v0.3 参数化建模 demo | ✅ 已发布 | BlockGenerator 16/16 GLB (4 kind × 2 system × 2 size), 13 张真照片 baseline, Delaunay fallback, FEILE 体系 + raw 5 字段 CLI | `dd191d3` · `0aa8075` |
| v0.4 建模板块集成 (App 端) | ✅ 已发布 | POST /parametric-blocks, captures.mode 分流 worker, 4 步 wizard (`/parametric`), 53 vitest + 4 playwright, 用户指南 + slope 卡尺图 | `cc9bd29` · `14662cb` · `bc9f642` · `f06e38f` |
| v0.5 AR 采集识别 (后端) | ✅ 已实现 | `POST /ar-captures` 同步识别 + `brick_recognizer` 纯函数 + worker `ar_recognized` 分流 + 17 新测试 | `d6e4f37` |

## 1. v0.1 基础后端 (✅)

FastAPI / SQLAlchemy 2 async / asyncpg / Pydantic 2 / Celery 5 / MinIO。

- 端点: `POST /api/v1/captures`, `GET /api/v1/captures`, `GET /api/v1/captures/{id}`, `GET /api/v1/captures/{id}/images`, `POST /api/v1/ar-captures`, `POST /api/v1/parametric-blocks`, `GET /api/v1/jobs/{id}`, `GET /api/v1/jobs/{id}/stream` (SSE), `GET /api/v1/assets/{id}`。
- 表: `captures` / `jobs` / `assets` 三张 + alembic 迁移。
- 12/12 单测全过, `deliverable.md` 冻结。

## 2. v0.2 拍照 UX + SfM 重建 (✅)

冻结于 `docs/design-phase2.md`。关键变化:

- `POST /captures` 接收 **4-20 张** jpg/webp/heic, `capture_mode` 枚举。
- Celery 任务五阶段 progress (collecting_photos → sparse_reconstruction → dense_reconstruction → cleanup → upload), 前端 SSE 实时同步。
- COLMAP docker 优先; 没装就跑 Open3D-only fallback (≥8 张走 Delaunay 凸包)。
- Playwright E2E 用真照片 fixture 跑全链路, 8-photo run 截图在 `tests/e2e/screenshots/`。

**实测遇到的坑** (写在这里避免后面再踩):

- macOS Open3D 0.18.0 Poisson / Ball-Pivoting 在 glibc 2.36+ 上 SIGSEGV — 改用 scipy Delaunay 凸包 (10 行代码), watertight + 5KB+ 满足。
- COLMAP incremental mapper 在 Osmo 纯旋转环绕 + DUPLO 简单纹理上 register 只到 2 张图, 重建退化到 34 点的菱形凸包 — 因此走参数化路线。
- HEIC 不能直接给 PIL, 需要 `pillow-heif` 注册 decoder (后端在 Mac 上可以, docker 镜像要补一行 apt)。

## 3. v0.3 参数化建模 (✅ 已发布)

**核心想法**: DUPLO / LEGO 是公开规格, 4 个核心比例 + grid spacing 就能 scale 出所有变体, SfM 没必要。照片只用作"实物对比验证", 不是反推尺寸的数据源。

**已完成** (v0.3 demo):

- `apps/api/src/services/block_generator.py` — `BlockSpec` dataclass + `generate()` + `export_glb()`, 4 kind (brick/plate/tile/slope) × 3 system (duplo/lego/feile) × N size (1x1 / 2x2 / 2x4 验证过) = **16 GLB 全过 5KB + 全 watertight**。
- 13 张真照片 baseline: 9.5KB GLB (243v/482f), 单图 4 张 baseline 24KB GLB。
- `test_reconstruction.py` 6/6 全过, `pipeline_used` 集合扩到 5 个值。
- `open3d_runner.py` 接 scipy Delaunay 路径绕开 mac SIGSEGV。
- 13 张真照片**不进 git** (21MB), 走 `tests/e2e/fixtures/real-photos/.gitignore` + 本地 README。pytest 用 `synth_cube` 合成图, 不依赖真照片。
- FEILE 体系 (国产大颗粒, 16mm 节距) + `tools/measure_block.py` raw 5 字段 CLI + 0.5mm stud_Ø cross-check (`cross_check_raw` / `derive_spec_from_raw` 在 CLI + API 双实现)。

**BlockGenerator 公开规格** (LEGO.com product specs, ±0.1mm; FEILE 来自首份 2x2 实测):

| 维度 | LEGO | DUPLO | FEILE |
|---|---|---|---|
| 节距 (unit) | 8.0 mm | 20.0 mm | 16.0 mm |
| 凸点直径 (knob Ø) | 4.8 mm | 16.0 mm | 9.4 mm |
| 凸点高度 (knob h) | 1.7 mm | 7.0 mm | 5.4 mm |
| 砖块高度 (brick) | 9.6 mm | 17.0 mm | 19.2 mm |
| 板高度 (plate) | 3.2 mm (= brick/3) | 6.0 mm | 6.4 mm |
| 内壁直径 (tube Ø) | 6.2 mm | 12.0 mm | 11.2 mm |
| 内壁深度 (tube h) | 8.4 mm | 14.0 mm | 15.5 mm |
| 楔形 (slope) | 45°, 沿 X 方向降低 | 45°, 沿 X 方向降低 | 45°, 沿 X 方向降低 |

任意 size (units_x × units_y) 都能从以上常量直接 `export_glb()`。

## 4. v0.4 建模板块集成 (✅ 已发布) + 后续开发计划

v0.4 把 v0.3 离线 demo 接到 App 端: 用户走 `/parametric` 4 步 wizard, 后端 `POST /parametric-blocks` 收 5 数字, worker 端 `if capture.mode == "parametric_block"` 分流到 `BlockGenerator`, 跟 v0.2 拍照路径共享一张 `captures` 表。

### 4.1 v0.4 release notes (4 commits)

| commit | 范围 | 关键产物 |
|---|---|---|
| `cc9bd29` | db-schema | alembic `0003_captures_parametric_block.py`: 8 列加到 captures (`mode` NOT NULL+default 'photo' · `system` · `kind` · `units_x` · `units_y` · `raw_measurements_mm` · `derived_spec_mm` · `cross_check_warnings` 全 JSONB) |
| `14662cb` | api-endpoint | `POST /api/v1/parametric-blocks` (multipart, photos 0-20 可选, system/kind/units/raw_measurements_mm 必填) + `workers/tasks/reconstruct.py` 分流 + `_run_parametric_pipeline` (复用 `_finalize_mesh_pipeline`); `models/schemas.py` `ParametricBlockRequest/Read`; 14 个新单测全过 |
| `bc9f642` | web-ui | `apps/web/src/features/parametric/` 4 步 wizard (PhotoUpload/KindSelect/Measurements/Preview) + api.ts + schema.ts; 6 vitest + 4 Playwright e2e (含 `parametric-preview.png` 截图) |
| `f06e38f` | web-ui fix | 5 测量 key 改 `_mm` 后缀对齐后端 `_RAW_KEYS`, `ParametricBlockResponse` 收窄对齐 `ParametricBlockRead`, photo FormData field `images` → `photos`; 后端真 POST 链路打通 |

### 4.2 关键设计决策

- **共享 Capture 行** — `captures.mode` 字段 (`"photo"` / `"parametric_block"`) 一行分流, 不为建模路径新开表。`image_count` 字段在建模路径下复用为"参考照片张数"。
- **照片可选** — 0 张合法, 走纯 caliper 路径; 1-20 张是"参考照片"路径 (SSE pipeline 跳过 SfM, 只为步骤 4 并排对比服务)。
- **字段名一致性契约** — 前端 `MEASUREMENT_FIELDS[*].key` = 后端 `_RAW_KEYS` = CLI argparse = DB JSONB key, **5 个 key 全部以 `_mm` 结尾**, 改名需改 4 处 (`schema.ts` / `api.ts` / `parametric_blocks.py` / `measure_block.py`)。
- **Worker 复用** — 不为建模路径写新 Celery task, `reconstruct` task 入口处 `if capture.mode == "parametric_block"`, 复用 `collecting_photos` (空跳过) → `sparse_reconstruction` (准备 spec) → `dense_reconstruction` (BlockGenerator.generate) → `cleanup` → `upload` 五阶段 SSE, 跟拍照路径同步动画。
- **照片不进 SfM** — 建模 worker **不读** `image_keys` 跑 COLMAP, 直接走 `BlockSpec` → `export_glb` → MinIO。

### 4.3 后续开发计划 (v0.5+)

按 `docs/design.md` 阶段 5 + 用户在新功能上的优先级, 暂列:

| 优先级 | 功能 | 复杂度 | 备注 |
|---|---|---|---|
| P0 | 实物 brick 4 视角照片 vs GLB 并排渲染对比 | 中 | v0.4 release notes 第二项已完成 API 集成, 视觉对比 UI 待做; 截图产物 `docs/validation/{part_id}-side-by-side.png` |
| P1 | 零件库 CRUD (Part / Variant / Color) | 中 | 用户存自己的 brick 库, 跨 capture 复用; 依赖 schema 演化 |
| P1 | 网格手动编辑 (R3F) | 中-高 | design §5.3 范围, 复杂 |
| P1 | round / technic brick 支持 | 中-高 | 把 `block_generator.py` 扩到 6+ kind, manifold3d boolean cut 圆柱 / 十字轴 |
| P2 | 拼搭规则引擎 | 高 | 接触面匹配 / 卡扣力 / 稳定性分析 |
| P2 | AI 灵感生成 (text → assembly) | 高 | 依赖零件库 |
| P3 | 多用户 / 协作 | 高 | 看用户规模决定 |

**当前最该做的** (用户原话): 接 P1 零件库 (Part / Variant / Color), 让用户能存自己的 brick 库, 跨 capture 复用。

## 5. v0.5 AR 采集识别 — 后端 (✅ 已实现)

ARCore 手机上传 top-down 凸点照片 + 16-bit 深度 + 相机内参, 后端同步识别积木 (system / kind / stud grid), 置信度够时直接生成标准 GLB (零卡尺输入); 不够时返回 `needs_measurement` 回退到 `/parametric-blocks` 卡尺路径。

**已完成**:
- `services/brick_recognizer.py` — 7 个纯函数 (`classify_system` / `detect_studs` / `fit_grid` / `metric_pitch` / `recognize_brick` / `encode_depth16_png` / `load_depth16_png`), 无手机/GPU/DB 依赖, 14 个单元测试。
- `POST /api/v1/ar-captures` — multipart 上传 (RGB + depth + ar_metadata JSON + 角度照片), 同步识别, 201 返回 `recognized` 或 `needs_measurement`。
- Worker `_run_ar_recognized_pipeline` — 从 Capture 行读 system/kind/units → `BlockSpec` → `export_glb` → `_finalize_mesh_pipeline`, 复用 parametric 路径的 mesh pipeline。
- DB: `captures` 表加 `ar_metadata` + `recognition_result` JSONB 列 (migration `0004`)。
- Web/API visibility follow-up: `GET /api/v1/captures` + `GET /api/v1/captures/{id}/images` 支持把 `needs_measurement` AR 采集显示在首页和 `/captures/:id` 详情页。
- Asset delivery follow-up: `GET /api/v1/assets/{id}` 对 SPA 的 `Accept: application/json` 返回 JSON envelope, 直接打开仍 302 到 MinIO 预签名 URL。
- Config: `ar_pitch_tolerance_mm` (3.0mm) + `ar_min_confidence` (0.6)。
- Tests: 14 单元 + 3 端点校验 + 1 worker + 1 e2e (POST → worker → GLB), MinIO 集成测试受环境影响。

## 6. 文档地图

- `docs/design.md` — 总体设计 (阶段一契约)
- `docs/design-phase2.md` — 阶段二契约 (拍照 UX + SfM)
- `docs/capture-procedure.md` — 采集流程 (CLI 卡尺路径 + App 端 `/parametric` 路径并列)
- `docs/parametric-block-guide.md` — 建模板块用户指南 (v0.4, 4 步 wizard + 5 测量适用性表)
- `docs/measure-block-annotated.svg` — brick/plate/tile 卡尺 5 点
- `docs/measure-block-slope.svg` — slope 卡尺 5 点 (v0.4 新增)
- `docs/capture-procedure.svg` — 采集流程图
- `deliverable.md` / `apps/api/deliverable-pipeline.md` / `apps/web/deliverable.md` — 阶段交付物
- `docs/ar-capture-recognition-design.md` — AR 采集识别设计规格 (v0.5)
- `docs/ar-capture-recognition-plan.md` — AR 采集识别实现计划 (v0.5)
- `apps/api/README.md` / `apps/web/README.md` — 模块自述
