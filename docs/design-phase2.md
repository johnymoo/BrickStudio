# 积木建模工具 — 阶段二设计契约 (拍照 UX + 真 3D 重建)

阶段一 (v0.1) 跑通了"拍照 → 上传 → synthetic sphere GLB → MinIO 下载"最小闭环。
阶段二目标: **用户能自己用起来** — 拍真实积木, 出真 3D 模型, 流程顺。

本文档冻结阶段二所有 worker 必须遵循的契约。

## 1. 范围

**在范围内**:
- 后端: 集成 COLMAP 真 SfM 重建; 拍照任务 status 细化 (含拍照阶段 vs 重建阶段); Open3D-only fallback 走 ≥8 张照片
- 前端: 拍照 UI 智能指引 + 4 张图预览 + 删/重拍单张 + 实时清晰度提示
- 集成: Playwright 用真实照片 fixture (≥8 张合成) 跑全链路; README 实操指南

**不在范围内** (推到阶段三、四):
- 网格手动编辑 UI (R3F)
- 零件库 CRUD
- 拼搭规则引擎
- AI 灵感生成

## 2. 整体架构变化

阶段一:
```
拍照 4 张 → POST captures → Celery → Open3D-only fallback (synthetic sphere) → GLB
```

阶段二 (新加的部分用 **bold**):
```
**智能拍照 N 张 (4-12)** → **预览 + 删/重拍** → POST captures (N jpg) →
Celery →
  **Stage 1: 拍照阶段 status=collecting_photos (前端实时同步)**
  **Stage 2: COLMAP SfM (若 binary 装好; 没装就 Open3D ≥8 张 fallback)**
  **Stage 3: Poisson / Ball-Pivoting 重建**
  Stage 4: cleanup (no-op) + GLB 导出
  Stage 5: MinIO upload + assets 表 + status=completed
前端用 SSE 订阅更细的 progress (含 stage, eta_seconds)
```

## 3. 后端契约 (apps/api)

### 3.1 拍照 API (保持阶段一接口 + 加可选字段)

`POST /api/v1/captures` (multipart/form-data)
- 入参: `part_id` (str, 客户端 UUID), `images` (File[] 至少 4 张, **上限 20**), `capture_mode` (str, enum: `phone_walkaround` | `studio_turntable` | `quick_snapshot`, 默认 `phone_walkaround`)
- 出参: `201 { capture_id, part_id, image_count, status: "pending", job_id }`
- 行为: 保存 N 张原图到 MinIO, 写 captures 表, 派发 Celery 任务 `reconstruct.delay(capture_id)`
- 新增: `image_count` 不再硬限 4, 范围 4-20
- 422 校验: `CAPTURE_INVALID` (image_count < 4)

`GET /api/v1/captures/{capture_id}` (保持阶段一)
- 出参: 阶段一字段 + 新增 `capture_mode`

### 3.2 任务 API (扩展 stage 字段)

`GET /api/v1/jobs/{job_id}` (保持阶段一, 扩展 stage 枚举)
- stage 新增枚举:
  - `collecting_photos` (前端用, 拍照阶段)
  - `downloading_images` (阶段一保留)
  - `sparse_reconstruction` (SfM 阶段, 5-90% 进度)
  - `dense_reconstruction` (MVS 阶段, 30-70% 进度, COLMAP stereo_fusion)
  - `point_cloud_cleaning`
  - `mesh_reconstruction` (Poisson / BPA)
  - `simplification_and_export`
  - `completed` / `failed`
- progress: 0-100
- 新增 `pipeline_used`: `colmap_sfm` | `open3d_fallback` | `open3d_pure_photogrammetry`

`GET /api/v1/jobs/{job_id}/stream` (SSE 保持阶段一, 事件类型不变)
- 新增事件字段 `eta_seconds` (int, 可选, worker 估算剩余时间)

### 3.3 重建 pipeline (apps/api/src/pipelines/)

新增模块: `colmap_runner.py`
- 封装 COLMAP CLI 调用 (自动检测 `/opt/homebrew/bin/colmap` / `$COLMAP_BIN` env / 容器内 `/usr/local/bin/colmap`)
- 主入口: `class ColmapRunner`
  - `reconstruct(input_dir, output_dir, on_progress) -> dict`
  - 内部:
    1. `colmap feature_extractor` (SIFT 特征)
    2. `colmap exhaustive_matcher` (特征匹配)
    3. `colmap mapper` (稀疏重建 → sparse/0/images.bin / points3D.bin)
    4. `colmap image_undistorter` (畸变校正)
    5. `colmap patch_match_stereo` + `colmap stereo_fusion` (稠密重建 → dense.ply)
  - 进度回调: on_progress(pct 0-100, stage "sparse_reconstruction" / "dense_reconstruction" 等)
  - 错误: `ColmapUnavailable` (binary 找不到), `ColmapFailed` (CLI 退出非零), `InsufficientImages` (< 8 张)

扩展 `open3d_runner.py`:
- 新增 `reconstruct_from_photos_multi(input_dir, output_dir, on_progress)` — 真用 8+ 张照片做 photogrammetry 风格的重建 (但 Open3D 没有真 SfM, 用 trimesh 从图像投影估位姿 + Open3D 重建)。这个是 COLMAP 不可用时的降级路径, 仍优于阶段一的 synthetic sphere。
- 保持 `reconstruct_from_photos` (single-image fallback) 给 ≥4 张但 < 8 张情况 (回到 synthetic sphere)

修改 `reconstruct.py` Celery 任务:
```
if image_count >= 8 and colmap_available:
    pipeline_used = 'colmap_sfm'
    runner = ColmapRunner()
elif image_count >= 8:
    pipeline_used = 'open3d_pure_photogrammetry'
    runner = open3d_runner.reconstruct_from_photos_multi
else:  # 4-7 张
    pipeline_used = 'open3d_fallback'
    runner = open3d_runner.reconstruct_from_photos  # 阶段一路径
```

修改 `reconstruct.py` Celery 任务 — status 更新逻辑:
- `_set_job_running(job_id)` 时: `stage="downloading_images"`, `progress=5`
- 下载完: `stage="sparse_reconstruction"`, `progress=10`
- 重建推进时: 实时更新 progress + stage (5-95%)
- 失败时: 写 `error` 字段, 标记 `stage="failed"`, `progress=0`
- 完成时: 写 `result_asset_id` + `stage="completed"`, `progress=100`

### 3.4 Pyproject 依赖

新增 `pyproject.toml` dependencies (按需):
- COLMAP 集成: 调 CLI 即可, 不需新 Python 依赖
- Open3D 多图重建: 已用 0.18, 不变
- 多视角投影: 复用 trimesh + numpy

### 3.5 配置 (新增 env 变量)

`apps/api/src/app/config.py`:
- `colmap_bin: str | None = Field(default=None)` — 来自 `COLMAP_BIN` env
- `reconstruct_min_images_for_colmap: int = 8` — 走 COLMAP 路径最少照片数

`deploy/.env.example`:
```
COLMAP_BIN=                      # 留空自动检测
RECONSTRUCT_MIN_IMAGES_FOR_COLMAP=8
```

## 4. 前端契约 (apps/web)

### 4.1 拍照页 CapturePage 改造

保留阶段一功能 (getUserMedia + 4 角度引导 + file 兜底), 升级:

新功能:
1. **拍照数量动态**: 不再硬限 4 张, 引导用户拍 ≥8 张 (COLMAP 路径最佳), 但 ≥4 张也可提交
2. **智能清晰度提示**:
   - 用 `<canvas>` 抓当前帧 → 用 Web API 计算 Laplacian variance (类似 OpenCV 边缘检测)
   - variance > 阈值 → "✓ 清晰, 可以拍" (绿色)
   - variance < 阈值 → "✗ 模糊, 调整一下" (黄色)
   - 实时更新, 不阻塞 UI
3. **照片预览网格**:
   - 4 列 × N 行布局, 每张图缩略图
   - 每张图右上角: 删除按钮 (垃圾桶图标)
   - 删除后, "提交" 按钮变红, 引导用户补拍
4. **多模式选择**:
   - 顶部 segmented control: "📱 围绕物体走" / "🔄 转盘" / "📸 快速拍"
   - 不同模式用不同角度引导:
     - `phone_walkaround`: 8 个角度 (4 + 4 补充, 30°/45°/60°/90° 高度)
     - `studio_turntable`: 12 个角度 (每 30° 一张)
     - `quick_snapshot`: 4 张默认
5. **提交后实时进度**:
   - 用 SSE 订阅新 stage 字段
   - 显示 "📥 下载中" → "📐 3D 重建中 (SfM)" → "🧊 网格生成" → "✅ 完成" 五个阶段
   - 每阶段百分比 + 估算剩余时间

### 4.2 API 客户端扩展

`src/lib/api.ts`:
- `createCapture(formData, captureMode?)` 接受新参数
- `subscribeJob(id, onEvent)` 解析新 stage 枚举

`src/stores/useJobStore.ts`:
- 扩展 job 状态类型 (新增 stage 枚举)
- 持久化时, `capture_mode` 字段也存

### 4.3 Vitest 扩展

新增测试 (in `tests/e2e/` 之外, 在 `apps/web/src/` 下):
- `CapturePage.test.tsx` 新增用例:
  - 8 张图时的 disabled 状态
  - 删除照片后状态恢复
  - capture_mode 切换后引导角度数更新
  - 清晰度检测 mock (用假 stream 模拟清晰/模糊帧)
- 智能清晰度计算函数提为 util, 单独单元测试 (`src/lib/sharpness.test.ts`)

## 5. 端到端测试契约

### 5.1 真实照片 fixture (新增)

`tests/e2e/fixtures/real-bricks/` (新):
- 8-12 张合成照片 (matplotlib 生成简单几何体, 多个角度)
- 用于 Playwright 真实端到端测试 (不限于 4 张)
- 命名: `brick-{angle:02d}.jpg` (0-11)

生成脚本: `tests/e2e/fixtures/generate_real_bricks.py`
- 用 trimesh + matplotlib 生成简单 L 形 / 阶梯 / 立方体组合
- 12 个角度 (0°/30°/60°/.../330°)
- 模拟手机拍: 略带噪声 + 模糊
- 输出 8-12 张 jpg

### 5.2 Playwright 测试 (扩展)

`tests/e2e/full-flow.spec.ts` 新增用例:
- "upload 8 photos → COLMAP pipeline (or fallback) → 3D model renders"
  - 用 `real-bricks/` fixture (8-12 张)
  - 验证 capture_mode 默认 + 可选
  - 验证 SSE 收到至少 3 个不同 stage 事件
  - 验证 result_asset_id 存在 + asset URL 可下载 + 文件 > 5KB (非 synthetic sphere)

`tests/e2e/smoke.spec.ts` 保留 (健康检查 + 4 张 quick snapshot 路径)

### 5.3 README 实操指南 (新增)

根 `README.md` 替换"快速开始"段为:

```markdown
## 自己用起来 — 5 分钟指南

### 准备 (一次性)
- 装 COLMAP: `brew install colmap` (~ 30-60 分钟, 大依赖)
- 装 Docker: 用 [Docker Desktop](https://docker.docker.com/) 或 OrbStack
- 启动全栈: `bash scripts/up.sh` (8 个服务, 第一次构建慢)

### 拍积木
1. 手机/电脑打开 http://localhost
2. 点 "📷 开始拍摄"
3. 浏览器请求摄像头权限 (允许)
4. 把积木放白纸中央, 周围放尺子 (用作标尺)
5. 围绕积木走, 每 30°-45° 拍一张, 至少 8 张
6. 拍完点 "提交", 实时看进度 (10-60 秒)
7. 完成后点 "📥 下载 GLB", 拖到 Three.js Online Viewer 或 Blender 验证

### 模型丢了 / 重建失败
- COLMAP 没装 / 装错: worker 自动 fallback 到 Open3D 路径
- 检查 job_id 的 `error` 字段 (用 GET /api/v1/jobs/{id})
- 重拍: 删掉模糊的那张, 补一张

### 下一步
- 把 GLB 拖到 Blender 微调 (boolean clean + UV unwrap)
- 拼搭: 进入阶段三后, 用零件库 CRUD + 拼搭编辑器
```

## 6. 验收标准 (阶段二收官)

| 项 | 标准 |
|---|---|
| COLMAP 路径 | brew install colmap 后, 8+ 张照片能跑真 SfM → Poisson → 真 GLB (>50 KB, 非 synthetic sphere) |
| Open3D fallback | 8+ 张照片但 COLMAP 不可用时, 仍能出 mesh (虽然不是真 SfM) |
| 4-7 张照片 | 走阶段一 synthetic sphere 路径, 行为兼容 |
| 拍照 UI | 智能清晰度提示 + 多模式 + 删/重拍 + 实时进度 |
| SSE stage 字段 | 至少 3 个 stage 事件能在前端订阅到 |
| 端到端 Playwright | 真实 8 张 fixture → 200 status=completed + GLB 文件 > 5KB |
| README | 用户能跟 5 分钟指南走一遍, 不需查其他文档 |

## 7. 已知风险

- **COLMAP brew 编译慢**: 30-60 分钟, 第一次 docker build 时镜像不含 (避免镜像臃肿)
- **Open3D 真 photogrammetry 不存在**: 0.18 没有真 SfM, fallback 仍靠 trimesh 估位姿
- **手机浏览器 HTTPS**: localhost/HTTPS 之外 getUserMedia 不工作, 提示用户用 ngrok/cloudflared 或本地 IP
- **拍照数量上限**: COLMAP 12+ 张足够, 20+ 张会非常慢, 阶段二限 20
- **模型质量**: 真照片 + COLMAP 出 mesh 仍有 噪声, 阶段三手动编辑 UI 解决

## 8. 后续阶段 (供 worker 了解背景, 不在阶段二范围)

- 阶段三: 拼搭编辑器 + 零件库 CRUD
- 阶段四: 网格手动编辑 UI (Blender 集成)
- 阶段五: AI 灵感生成
- 阶段六: 用户/账号/协作
