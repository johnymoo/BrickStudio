# Design Spec: AR 采集识别 + 参数化建模（采集建模 · 识别/建模后端）

- **Date:** 2026-06-07
- **Repo:** `BrickStudio`（识别/建模后端 · Python·FastAPI·Celery）
- **配对规格:** `ARCore-prototype/docs/superpowers/specs/2026-06-07-arcore-capture-client-design.md`（采集端）
- **Status:** 已批准设计，待实现计划

## 0. 背景与定位

这是"采集建模"子项目的**识别/建模后端**。整体走 **方案 C（识别优先，混合分阶段）**：ARCore app 做最少操作的引导采集并上传；本后端**自动识别**标准积木（system + kind + 凸点网格 `units_x×units_y`），用 app 携带的 AR 公制信息算出真实节距、判别体系，**零卡尺**复用 `BlockGenerator` 出精确参数化 GLB；置信度不足时返回 `needs_measurement`，走现有 5 测量参数化兜底。

**职责切分**：`ARCore-prototype` = 智能采集客户端；本仓库 = **识别 + 建模（权威）**。集成缝是第 6 节的**上传契约**。

> 本后端的识别/拟合是纯 Python/OpenCV/numpy，**本机可充分开发与自动化测试**，并提供**离线夹具 e2e**（无需手机即可验证整个后端闭环）。

## 1. 目标与范围

### In scope
- 新接入口 `POST /api/v1/ar-captures` + 新采集模式 `mode = "ar_recognized"`。
- 识别服务 `brick_recognizer`：数凸点、反投影算公制节距、按节距判别 system、置信度。
- 拟合门（precision gate）：高置信走标准规格出 GLB；低置信返回 `needs_measurement`。
- 新 worker 分支 `_run_ar_recognized_pipeline`，复用 `BlockGenerator.export_glb()`。
- DB 扩展 + 一条 alembic 迁移。

### Out of scope（Deferred）
- 任意工件/工装的真实公制重建（方案 B）。
- AR 卡尺实时高亮叠加（端侧未来）。
- 把识别下沉到端上（本期权威在后端）。

## 2. 与现有架构的关系

复用既有 `jobs / assets / SSE / BlockGenerator / parametric` 全套，新增面最小：
- 现有 `captures.mode` 已是 `photo | parametric_block` → **加枚举值 `ar_recognized`**。
- 现有 `BlockSpec(system, kind, units_x, units_y[, overrides])` + `export_glb()`：标准件不传 override 即用**公开标准规格**生成 → 这正是"零卡尺"路径。
- 现有 `POST /parametric-blocks`（5 测量）= 低置信兜底，**不改**。

## 3. 新接入口与响应

**`POST /api/v1/ar-captures`**（multipart，见第 6 节契约）→ 创建 `Capture(mode="ar_recognized")` + `Job(kind="reconstruct")`，入 Celery。

**响应**（201）：
```json
{
  "capture_id": "uuid", "job_id": "uuid",
  "recognized": {"system": "feile", "kind": "brick", "units_x": 2, "units_y": 4,
                 "pitch_mm": 16.1, "confidence": 0.93},
  "status": "recognized",
  "needs_measurement": null
}
```
- `status`: `"recognized"` | `"needs_measurement"`。
- `needs_measurement` 非空时形如 `{"fields": ["outer_pitch_mm", ...], "guidance": "卡两端外边缘…"}`。
- GLB 仍走现有 `GET /assets/{asset_id}`；进度走现有 `GET /jobs/{id}/stream`。

## 4. 识别 + 尺度 + 拟合门算法

新模块 `apps/api/src/services/brick_recognizer.py`，全部为可单测纯函数：

1. **`detect_studs(rgb) -> StudGrid`**：检测俯视面凸点圆心（霍夫圆 / blob），按规则网格拟合出 `units_x × units_y` 与各凸点像素坐标。
2. **`metric_pitch(stud_centers_px, depth16, image_intrinsics, depth_dims) -> pitch_mm`**：
   - 把凸点像素坐标缩放到深度图坐标，读 DEPTH16（毫米）。
   - 用内参反投影成相机系 3D 点：`X = (u-cx)/fx * Z`，`Y = (v-cy)/fy * Z`，`Z = depth_mm`。
   - 相邻凸点 3D 距离的中位数 = `pitch_mm`。测**相邻差值**抵消深度共模误差。
3. **`classify_system(pitch_mm) -> (system, confidence)`**：贴近哪个已知体系单位（带容差），落空则 `unknown`。
4. **kind**：取上传的用户确认值（默认 `brick`）。
5. **拟合门**：
   - 高置信（节距入某体系容差 + 凸点网格清晰）→ `BlockSpec(system, kind, units)` 标准规格 → `export_glb()`。
   - 低置信（节距不匹配 / 凸点不清 / `system_hint=unknown`）→ `Job` 标 `needs_measurement` + 指引，不产 GLB。

### 已知体系单位节距（`classify_system` 基准，源自 `block_generator.py` 公开规格表）
| system | unit (mm) | knob_Ø (mm) | brick_h (mm) |
|---|---|---|---|
| `lego` | 8.0 | 4.8 | 9.6 |
| `feile` | 16.0 | 9.4 | 19.2 |
| `duplo` | 20.0 | 16.0 | 17.0 |

> 三者单位（8/16/20）相距较远，即便 AR 节距有 ±1–2mm 噪声也可稳健判别。容差与置信度阈值做成配置项（见第 9 节）。

### 真值校验
识别出的体系有**已知标准节距**；"AR 实测节距 vs 标准节距"的差异即采集质量指标，写入 `recognition_result.warnings`。这实现"仅当拟合精度不够才要求更多测量"。

## 5. Worker 分支与 DB

**Dispatch**（`apps/api/src/workers/tasks/reconstruct.py`）：在 `mode` 分流处加 `ar_recognized → _run_ar_recognized_pipeline`：
1. 载入 `Capture`（含 `ar_metadata`、图像 keys）。
2. 从 MinIO 取 `recognition_rgb` + `recognition_depth` → 跑 `brick_recognizer`。
3. 高置信 → 写 `system/kind/units_x/units_y` + `derived_spec_mm`（标准规格）→ `BlockSpec` → `export_glb()` → `_finalize_mesh_pipeline`（与参数化路径同一收尾，写 asset）。
4. 低置信 → 更新 job 状态 `needs_measurement` + 写 `recognition_result`，不产 asset。

**DB**（`captures` 表）：
- 复用已有 `system / kind / units_x / units_y / derived_spec_mm / cross_check_warnings`。
- 新增 `ar_metadata JSONB`（内参/位姿/距离/设备/粗提示原样存档）。
- 新增 `recognition_result JSONB`（`pitch_mm`、`confidence`、检测网格、warnings）。
- 一条 alembic 迁移。
- `Asset.meta` 增加 `pipeline_used = "ar_recognized"` + 识别结果摘要。

## 6. 上传契约（集成缝 · 与采集端共享）

> **这是两仓库的权威集成契约；任何改动必须在配对规格 `ARCore-prototype/.../2026-06-07-arcore-capture-client-design.md` 同步镜像。**

**`POST /api/v1/ar-captures`** · `multipart/form-data`

| 字段 | 类型 | 说明 |
|---|---|---|
| `part_id` | str (1–64) | 客户端生成 |
| `kind` | enum | `brick`/`plate`/`tile`/`slope`（默认 `brick`）|
| `system_hint` | enum? | `lego`/`duplo`/`feile`/`generic`/`unknown`；`unknown` 触发兜底 |
| `images[]` | File[] | 角度照片（≥4，JPEG/PNG）|
| `recognition_rgb` | File | 俯视凸点帧 RGB |
| `recognition_depth` | File | 该帧 DEPTH16，**16-bit PNG（毫米/像素，无损）** |
| `ar_metadata` | str(JSON) | 见下 |

`ar_metadata` JSON：
```json
{
  "device": {"model": "PLG110", "arcore": "1.54.260890493"},
  "recognition_frame": {
    "image_intrinsics": {"fx": 0, "fy": 0, "cx": 0, "cy": 0, "width": 0, "height": 0},
    "depth": {"width": 0, "height": 0, "format": "DEPTH16_MM"},
    "camera_pose": {"t": [0, 0, 0], "q": [0, 0, 0, 1]},
    "distance_m": 0.25
  },
  "coarse_hints": {"rough_units_x": 2, "rough_units_y": 4, "rough_pitch_mm": 16.2}
}
```
约定：
- **深度↔RGB 分辨率不同**：契约同时携带 RGB 内参与深度图尺寸，**后端把凸点像素坐标缩放到深度坐标**再反投影。
- `coarse_hints` 仅作建议/校验，**不权威**。

## 7. 错误处理 + 兜底

- **识别失败**（凸点检测不到 / 网格歧义 / 节距不落任何体系 / `system_hint=unknown`）→ `needs_measurement`（带 `fields` + `guidance`）。
- **兜底**：复用现有 `POST /parametric-blocks`（5 测量）+ 现有 `measure-block-*.svg` 指引图，几乎零新增后端。
- **输入异常**（缺 `recognition_depth`、JSON 不合法、尺寸不一致）→ 400 + 明确错误。
- 阈值/容差全配置化，便于真机调参。

## 8. 测试与验证

- **纯函数 pytest**（本机可全测）：`detect_studs` / `metric_pitch` / `classify_system` 用**合成数据**——程序生成的凸点网格 RGB + 合成 DEPTH16（已知 units/pitch）→ 断言识别正确。
- **离线闭环 e2e（关键去风险）**：把一份**采集包夹具**（`recognition_rgb` + 深度 16-bit PNG + `ar_metadata` JSON + 几张照片）存入 fixtures，写 e2e：`POST /ar-captures` → 断言识别出正确 system/units → 产出 GLB ≥ 阈值。**无需手机即可验证整个后端闭环**；初期合成包，后续替换为真机采集的真实包。
- **契约/迁移**：`/ar-captures` 契约测试、`ar_recognized` worker dispatch 测试、alembic 迁移测试。
- **回归**：现有 `photo` / `parametric_block` 路径不受影响。

## 9. 配置与落地

- 新增配置（`deploy/.env` / settings）：`AR_PITCH_TOLERANCE_MM`（体系判别容差）、`AR_MIN_CONFIDENCE`（拟合门阈值）、各体系单位节距表（默认见第 4 节）。
- 推荐落地顺序（全局）：**本后端 + 离线夹具 e2e 先行**（本机可验证、去风险）→ 采集端真机验证。
- 分支：在 `BrickStudio` 开 feature 分支（当前 `v0.2-development`），不动现有路径。

## 10. 成功标准

- 对一份标准积木采集包，`/ar-captures` 返回正确 system/kind/units 并产出对应 GLB（pipeline_used=`ar_recognized`），**零卡尺**。
- 非标/未知件稳定返回 `needs_measurement`，经 `/parametric-blocks` 出 GLB。
- 纯函数 + 离线夹具 e2e 在本机通过；`photo`/`parametric_block` 回归通过。
