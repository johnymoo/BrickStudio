# 积木建模工具 - 共享设计文档

本设计文档是 `拍照采集 → 照片转模型` 首轮交付的契约基础,所有 worker 必须遵循。

## 1. 产品目标

帮助用户(无专业建模背景)用手机拍照 → 自动生成可复用的标准 3D 零件模型 → 沉淀为零件库 → 后续做拼搭设计与灵感生成。

## 2. 范围 (首轮交付)

| 在范围内 | 不在范围内 |
|---|---|
| 项目骨架与开发工具链 | 网格手动编辑 UI |
| 拍照采集 (PWA) | 零件库 CRUD |
| 照片上传 + 任务调度 | 3D 拼搭编辑器 |
| 3D 重建管线 (Meshroom + COLMAP) | 拼搭规则引擎 |
| 3D 查看器 (Three.js, 仅查看) | AI 组合推荐 |
| E2E 最小闭环 | 用户/账号/权限 |

## 3. 整体架构

```
[移动 Web PWA]  ──拍照/上传──>  [FastAPI 后端]  ──入队──>  [Celery Worker]
       │                              │                          │
       │                              │                          ▼
       │                              │                [Meshroom / COLMAP]
       │                              │                          │
       │                              │<───────任务状态推送──────┘
       │<───SSE/WebSocket 任务进度───┘
       │
       └──────<── 轮询 / 拉取 ──────> [MinIO/S3] (存储原始照片 + 重建产物)
```

## 4. 技术栈(冻结)

| 层 | 技术 | 版本 |
|---|---|---|
| 前端框架 | React + TypeScript | React 18+ |
| 前端构建 | Vite | 5+ |
| 前端样式 | TailwindCSS | 3+ |
| 前端状态 | Zustand | 4+ |
| 前端 3D | Three.js + @react-three/fiber + @react-three/drei | three 0.16x |
| 前端 PWA | vite-plugin-pwa | 0.20+ |
| 前端 HTTP | fetch + 自封装 client | - |
| 后端框架 | FastAPI | 0.110+ |
| 后端语言 | Python | 3.11+ |
| 后端任务 | Celery + Redis | celery 5+ |
| 后端数据库 | PostgreSQL | 16+ |
| 后端 ORM | SQLAlchemy 2.0 (async) + Alembic | - |
| 后端存储 | MinIO (S3 兼容) | latest |
| 后端 3D | Meshroom (AliceVision) + COLMAP + Open3D + Blender(headless) | latest |
| 包管理(前) | pnpm workspace | 9+ |
| 包管理(后) | uv | 0.4+ |
| 容器 | Docker + Docker Compose | - |
| 代码风格(前) | ESLint + Prettier | - |
| 代码风格(后) | Ruff + mypy | - |
| 单测(前) | Vitest | - |
| 单测(后) | pytest + pytest-asyncio | - |
| E2E | Playwright | - |

## 5. 目录结构 (冻结)

```
/Users/chris/Project/积木工具
├── apps/
│   ├── web/                    # 前端 PWA (React + Vite)
│   │   ├── src/
│   │   │   ├── features/       # 按业务功能划分
│   │   │   │   ├── capture/    # 拍照采集
│   │   │   │   ├── viewer/     # 3D 查看器
│   │   │   │   └── jobs/       # 任务状态
│   │   │   ├── components/     # 通用组件
│   │   │   ├── lib/            # api client / 工具
│   │   │   ├── stores/         # zustand stores
│   │   │   ├── App.tsx
│   │   │   └── main.tsx
│   │   ├── public/             # 静态资源
│   │   ├── tests/
│   │   ├── package.json
│   │   ├── tsconfig.json
│   │   ├── vite.config.ts
│   │   ├── tailwind.config.ts
│   │   └── Dockerfile
│   └── api/                    # 后端 FastAPI
│       ├── src/
│       │   ├── app/            # FastAPI app
│       │   │   ├── main.py
│       │   │   ├── deps.py
│       │   │   └── config.py
│       │   ├── api/            # 路由
│       │   │   ├── v1/
│       │   │   │   ├── captures.py
│       │   │   │   ├── jobs.py
│       │   │   │   └── assets.py
│       │   ├── core/           # 配置/日志/异常
│       │   ├── db/             # SQLAlchemy 模型 + session
│       │   ├── models/         # Pydantic schema
│       │   ├── services/       # 业务逻辑
│       │   ├── workers/        # Celery 任务
│       │   │   ├── celery_app.py
│       │   │   └── tasks/
│       │   │       └── reconstruct.py
│       │   ├── pipelines/      # 3D 处理 pipeline
│       │   │   ├── meshroom.py
│       │   │   ├── colmap.py
│       │   │   └── cleanup.py
│       │   └── storage/        # MinIO / S3 客户端
│       ├── alembic/            # 迁移
│       ├── tests/
│       ├── pyproject.toml
│       ├── uv.lock
│       ├── Dockerfile
│       └── alembic.ini
├── packages/                   # 共享代码 (后续放)
├── deploy/
│   ├── docker-compose.yml      # 全栈编排
│   ├── docker-compose.dev.yml  # 开发模式
│   └── .env.example
├── scripts/
│   ├── bootstrap.sh
│   └── seed.py
├── docs/
│   ├── design.md               # 本文档
│   ├── api-contract.md         # API 契约(worker 必读)
│   └── data-model.md           # 数据模型(worker 必读)
├── .github/
│   └── workflows/
│       └── ci.yml
├── .gitignore
├── .editorconfig
├── package.json                # pnpm workspace 根
├── pnpm-workspace.yaml
├── README.md
└── CHANGELOG.md
```

## 6. API 契约 (冻结)

### 6.1 拍照采集

`POST /api/v1/captures` (multipart/form-data)
- 入参: `part_id` (str, 临时零件标识, 客户端生成 UUID), `images` (File[] 至少 4 张)
- 出参: `201 { capture_id, part_id, image_count, status: "pending" }`
- 行为: 保存原图到 MinIO, 写 `captures` 表, 调度 Celery 任务

`GET /api/v1/captures?limit=20`
- 出参: `CaptureRead[]`, 最近采集按 `created_at desc` 排序
- 用途: 首页展示后端已有采集, 尤其是 AR 识别失败后的 `needs_measurement` 项

`GET /api/v1/captures/{capture_id}`
- 出参: `{ capture_id, part_id, status, image_count, created_at, updated_at, job_id?, image_keys, capture_mode, mode, system?, kind?, units_x?, units_y?, recognition_result?, needs_measurement? }`

`GET /api/v1/captures/{capture_id}/images`
- 出参: `{ capture_id, images: [{ key, url, expires_at? }] }`
- 只为该 capture 的 `image_keys` 生成原图预签名 URL, 用于采集详情页查看上传照片

### 6.2 任务 (重建)

`GET /api/v1/jobs/{job_id}`
- 出参: `{ job_id, status: "pending|running|completed|failed", progress: 0-100, stage, error?, result_asset_id? }`
- `status`:
  - `pending` - 已入队未开始
  - `running` - 处理中
  - `completed` - 成功
  - `failed` - 失败 (`error` 字段有原因)

`GET /api/v1/jobs/{job_id}/stream` (Server-Sent Events)
- 事件: `progress` / `stage_change` / `completed` / `failed`
- 客户端订阅以显示实时进度

### 6.3 资源 (下载重建产物)

`GET /api/v1/assets/{asset_id}`
- 普通浏览器/下载调用: `302 Location: <MinIO presigned URL>`
- SPA / SDK 调用: 请求头含 `Accept: application/json` 时返回 `200` JSON
- JSON 出参: `{ asset_id, job_id, kind: "mesh_gltf|mesh_obj|mesh_stl|point_cloud_ply", url, size_bytes?, meta?, expires_at }`
- 预签名 URL 必须用浏览器可达的 S3 API 根地址签名 (`S3_PUBLIC_ENDPOINT`), 不能先签内部 host 再改写

### 6.4 健康

`GET /api/v1/health`
- 出参: `{ status: "ok", version, db: "ok"|"down", storage: "ok"|"down" }`

## 7. 数据模型 (冻结)

### 7.1 `captures` 表
| 字段 | 类型 | 约束 |
|---|---|---|
| id | UUID | PK |
| part_id | VARCHAR(64) | NOT NULL, INDEX |
| status | VARCHAR(32) | NOT NULL, DEFAULT 'pending' |
| image_count | INT | NOT NULL |
| image_keys | JSONB | NOT NULL, MinIO 对象 key 列表 |
| capture_mode | VARCHAR(32) | NOT NULL, DEFAULT 'phone_walkaround' |
| mode | VARCHAR(32) | NOT NULL, DEFAULT 'photo'; `photo` / `parametric_block` / `ar_recognized` |
| system | VARCHAR(32) | NULL |
| kind | VARCHAR(32) | NULL |
| units_x | INT | NULL |
| units_y | INT | NULL |
| raw_measurements_mm | JSONB | NULL, 参数化 5 个卡尺原始值 |
| derived_spec_mm | JSONB | NULL, 参数化/AR 推导规格 |
| cross_check_warnings | JSONB | NULL |
| ar_metadata | JSONB | NULL, ARCore 上传元数据 |
| recognition_result | JSONB | NULL, AR 识别结果与失败原因 |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT now() |
| updated_at | TIMESTAMPTZ | NOT NULL, DEFAULT now() |

### 7.2 `jobs` 表
| 字段 | 类型 | 约束 |
|---|---|---|
| id | UUID | PK |
| capture_id | UUID | FK→captures, NOT NULL, INDEX |
| kind | VARCHAR(32) | NOT NULL, DEFAULT 'reconstruct' |
| status | VARCHAR(32) | NOT NULL, DEFAULT 'pending' |
| progress | INT | NOT NULL, DEFAULT 0 |
| stage | VARCHAR(64) | NULL |
| error | TEXT | NULL |
| started_at | TIMESTAMPTZ | NULL |
| finished_at | TIMESTAMPTZ | NULL |
| created_at | TIMESTAMPTZ | NOT NULL |
| updated_at | TIMESTAMPTZ | NOT NULL |

### 7.3 `assets` 表
| 字段 | 类型 | 约束 |
|---|---|---|
| id | UUID | PK |
| job_id | UUID | FK→jobs, NOT NULL, INDEX |
| kind | VARCHAR(32) | NOT NULL |
| storage_key | VARCHAR(512) | NOT NULL |
| size_bytes | BIGINT | NULL |
| meta | JSONB | NULL, 例如顶点数/面数/包围盒 |
| created_at | TIMESTAMPTZ | NOT NULL |

## 8. 3D 处理流水线 (冻结)

```
[输入: 多角度照片] (4-30 张, MinIO keys)
       │
       ▼
[阶段 1] COLMAP 稀疏重建 (SfM + MVS)
       │  - 提取特征
       │  - 匹配
       │  - 稀疏重建
       │  - 稠密重建 (可选)
       │  - 输出 point-cloud.ply
       ▼
[阶段 2] Open3D 点云预处理
       │  - 统计离群点去除
       │  - 法向量估计
       │  - 输出 cleaned.ply
       ▼
[阶段 3] Open3D 网格重建 (Poisson / Ball Pivoting)
       │  - 输出 mesh_raw.ply / mesh_raw.obj
       ▼
[阶段 4] Open3D / Blender 简化 + 校准
       │  - 网格简化到 ≤ 50k 面
       │  - 归一化坐标系(以最长边定向)
       │  - 输出 mesh.glb
       ▼
[阶段 5] 落库
       │  - 创建 assets 记录
       │  - 标记 job 为 completed
       ▼
[输出: 标准化 GLB 模型, 浏览器可见]
```

## 9. 错误模型

- 后端统一返回 `{ "error": { "code": "...", "message": "...", "details": {...} } }`
- HTTP code 反映类别: 4xx 客户端问题, 5xx 服务端问题
- 关键 code:
  - `CAPTURE_INVALID` - 照片张数 < 4 / 格式错
  - `RECONSTRUCT_FAILED` - 重建失败
  - `JOB_NOT_FOUND`
  - `ASSET_NOT_FOUND`

## 10. 验收标准 (首轮)

| 项 | 标准 |
|---|---|
| 启动 | `docker compose up` 一键拉起 Postgres / Redis / MinIO / API / Worker / Web |
| 拍照 | 移动浏览器打开 PWA, 调起摄像头, 能拍 ≥4 张并上传 |
| 重建 | 上传后能进入 `running`, 进度推到 100, 最终 `completed` |
| 查看 | 点击 `completed` 任务, Three.js 查看器加载 GLB, 可旋转/缩放 |
| 健康 | `GET /api/v1/health` 返回 200 |
| 单测 | 前端 Vitest 通过 / 后端 pytest 通过 |
| E2E | Playwright 跑通 "打开首页 → 上传测试照片 → 等待完成 → 看到 3D 模型" |

## 11. 风险与约束

- Meshroom 跑一次可能 5-30 分钟 (取决于照片数和机器配置), 首轮 E2E 测试用最少 4 张
- 容器内 Meshroom 需要 GUI 依赖, 使用 `alicevision/alicevision` 官方 Docker 镜像或在容器内安装无头版本
- 浏览器摄像头需要 HTTPS 或 localhost
- MinIO 桶需要启动时自动创建

## 12. 后续阶段(供 worker 了解背景, 不在首轮范围)

- 阶段二: 3D 拼搭编辑器 (React Three Fiber) + 零件库 CRUD + 拼搭规则引擎
- 阶段三: 网格手动编辑 UI (Blender 集成)
- 阶段四: 拼搭组合推荐 + AI 灵感
- 阶段五: 用户系统 + 协作 + 分享
