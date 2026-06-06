# 建模板块用户指南 (v0.4)

> 4 步 wizard · 卡尺优先 · 5 个 caliper 数字直接拼 GLB
>
> 本指南面向**在浏览器里走完 `/parametric` 流程**的用户。CLI 路径
> (`tools/measure_block.py`) 仍可独立使用, 但 App 端把 5 个数字的
> 报数、提交、预览、对比都打包好了, 跟手算 GLB 等价。
>
> 关联文档:
> - 卡尺 5 点定义 + brick/plate/tile 推导表 — `docs/measure-block-annotated.svg`
> - **斜面 (slope) 5 点** — `docs/measure-block-slope.svg` (v0.4 新增)
> - 完整采集流程 — `docs/capture-procedure.md`
> - 设计契约 — `docs/design-phase2.md` (path 沿用)
> - 后端实现 — `apps/api/src/api/v1/parametric_blocks.py` + `apps/api/src/services/block_generator.py`

## 0. TL;DR — 30 秒上手

1. 打开 `/parametric`
2. 上传 0-20 张实物照片 (或跳过, 不影响建模)
3. 选体系 (duplo / lego / feile / generic) + 类型 (brick/plate/tile/slope) + `units_x × units_y` (1-16)
4. 输入 5 个卡尺数字 (mm), 提交
5. 在 4 步 ladder 里看 GLB 生成, 失败时直接改 5 个数字重提

背后调用: `POST /api/v1/parametric-blocks` → 5 数字 → `BlockGenerator.generate()` → `export_glb()` → MinIO → R3F 预览。

---

## 1. 4 步 wizard 用户流程

### 步骤 1 / 4 — 照片 (可选)

| 字段 | 用户动作 | 表单值 |
|---|---|---|
| 0 张 | 点 "下一步: 选择规格" | `photos: []` |
| 1-20 张 | 拖拽 / 点选 / 删除 | `photos: File[1..20]` |

照片**不参与建模** (worker 不读 `image_keys` 跑 SfM, 直接 `mode="parametric_block"` 走 `BlockSpec` 路径)。照片只用作"实物校准" — 步骤 4 预览时跟生成的 GLB 并排对齐看偏差。

走 0 张也合法 — 建模路径对 SfM 数据无依赖。

### 步骤 2 / 4 — 规格 (system + kind + units)

```
┌────────────────────┬──────────────────────┐
│ 体系 (system)      │ 类型 (kind)          │
├────────────────────┼──────────────────────┤
│ 得宝 (DUPLO)       │ 🧱 brick 标准砖     │
│ 乐高 (LEGO)        │ ▭ plate 板          │
│ 费乐 (FEILE)       │ ◼ tile 瓦片         │
│ 通用 / 自定义      │ ◣ slope 斜面        │
└────────────────────┴──────────────────────┘

尺寸: units_x × units_y, 每轴 1-16
   2×2 = (2 × unit)² (单位 mm, 1 unit ≈ 20/8/16/10 按体系)
```

**注意**:
- 4 体系 × 4 类型 = 16 种组合, **当前全开** (per `SYSTEM_KIND_MATRIX` in `apps/web/src/features/parametric/schema.ts`)。
- `units_x × units_y` 只标 stud 排数, 最终公制尺寸由步骤 3 的 5 个 caliper 数字反算 (`unit = (1A + 1B) / 2`)。
- 提示语里的 `1 unit ≈ N mm` 是公开规格参考, 真实 unit 由 caliper 数字决定。

### 步骤 3 / 4 — 5 个 caliper 测量

5 个输入框 + 卡尺示意 SVG, 字段名跟后端 `_RAW_KEYS` 一一对应:

| 编号 | 字段名 (`raw_measurements_mm`) | 中文 | 卡尺位置 |
|---|---|---|---|
| 1A | `outer_pitch_mm` | 外径距 | 砖块总长, 跨两端 stud 圆周最远点 |
| 1B | `inner_pitch_mm` | 内径距 | 两 stud 圆周之间空隙 (卡尺插入) |
| 2 | `brick_height_net_mm` | 砖块净高 | 底面 → 砖顶, **不含**凸点 |
| 3 | `stud_diameter_mm` | 凸点直径 | 单个 stud 外径 (任选一个) |
| 4 | `brick_height_total_mm` | 砖块总高 | 底面 → 凸点顶, **含**凸点 |

图示: `docs/measure-block-annotated.svg` (顶视 + 前视 + 侧视 + 立体 4 子图)。

**前端实时校验** (`crossCheckMeasurements` in `schema.ts`):
- 凸点直径反算 `(1A - 1B) / 2` 跟填写 ③ 差 > 0.5 mm 时给黄色警告 (说明 1B 错位)。
- 5 个数字必须全部 finite 且 > 0, "下一步" 按钮才可点。

### 步骤 4 / 4 — 预览 (SSE-driven)

| 阶段 | 含义 | 触发事件 |
|---|---|---|
| 📥 下载照片 | 0 张跳过, 1+ 张走 MinIO download | `stage: "download"` |
| 📐 准备生成 | 解析 5 数字 + 校验 + 推 spec | `stage: "sparse"` (mapped from "init" / "prepare") |
| 🧊 参数化生成 | `BlockGenerator.generate()` | `stage: "dense"` (mapped from "generate" / "parametric") |
| ✨ 网格导出 | `export_glb()` + 上传 MinIO | `stage: "mesh"` (mapped from "export" / "upload") |
| ✅ 完成 | R3F 加载 GLB | `event: "completed"` |

5 步 ladder 动画 + 进度条 + 阶段文字 (e.g. `阶段: parametric_generate · 67%`)。完成后展开
`<details>` 看到 `derived_spec_mm` (4 个反算 spec 数字, 跟 `BlockGenerator` 的
`resolved_mm()` 等价)。

失败时显示 "⚠ 1B 错位 0.1 mm 会让反算 stud_Ø 偏 0.05 mm" 类提示, 引导用户回步骤 3 改数字重提。

截图: `tests/e2e/screenshots/parametric-preview.png` (4 步 ladder 全绿 + GLB viewer 区域)。

---

## 2. 上传照片规范 (跟 v0.2 对齐)

照片在建模路径下**只用于校准**, 沿用 v0.2 拍照 UX 的硬约束:

| 项 | 取值 |
|---|---|
| 数量 | 0-20 张 (0 张合法) |
| 格式 | `image/jpeg` · `image/png` · `image/webp` · `image/heic` |
| 单张大小 | 无硬限, 但建议 ≤ 5 MB (上传到 MinIO 不慢) |
| 分辨率 | 建议 1024×1024 起, 步骤 4 并排对比需要可读细节 |
| 拍摄角度 | 顶视 1 张 + 侧视 3 张 (120° 间隔) 即可, 跟 `docs/capture-procedure.md §1.1` 一致 |
| 背景 | A4 白纸 + 自然光, 阴影 30-45°, 详情 `docs/shoot-priorities.md` |

**不需要** 13 张 baseline / 转盘 / 校色卡 — 那是 v0.2 SfM 思路, v0.4 建模路径对 SfM 数据无依赖。

---

## 3. 5 测量对 kind 的适用性表

5 个 caliper 字段对 4 种 kind 的**适用性** (✓ = 直接测 / 派生 / N/A):

| 测量 (`raw_measurements_mm`) | 🧱 brick | ▭ plate | ◼ tile | ◣ slope | 备注 |
|---|:---:|:---:|:---:|:---:|---|
| `outer_pitch_mm` (1A) | ✓ | ✓ | ✓ | → 改名 `slope_length_mm` | 砖块 X 方向总长 (slope 改成"高边到低边水平距离") |
| `inner_pitch_mm` (1B) | ✓ | ✓ | **—** (无 stud) | → 改名 `slope_depth_mm` | stud 圆周空隙; tile 平顶无 stud; slope 改成"高边 Y 方向深度" |
| `stud_diameter_mm` (3) | ✓ | ✓ | **—** (无 stud) | ✓ (仅高边 stud) | 单个 stud 外径 |
| `brick_height_net_mm` (2) | ✓ | ✓ | ✓ | → 改名 `slope_height_high_mm` | **高边**净高 (slope 改成"高边净高", 不再是均匀高度) |
| `brick_height_total_mm` (4) | ✓ | ✓ | = ② (无凸点) | → 改名 `slope_height_total_high_mm` | **高边**总高; tile 因无凸点, ④ = ② |
| 派生: 低边高度 | — | — | — | = 0 (45° 楔形) | slope 的低边自然为 0, 不需测量 |

**slope 用不同的 5 字段** — 因为楔形几何不像 brick 那样有均匀高度 + 完整 stud 阵列。
完整 slope 卡尺点见 `docs/measure-block-slope.svg` (v0.4 新增, 顶视 + 前视 + 立体 3 子图)。

**round / technic 暂不支持** — `docs/design-phase2.md` 明确把这两类 out of scope
("`block_generator.py` 注释: round bricks / technic (cross-axle) 4 个 kind 已覆盖
3-5 岁 90% 实际碰到的零件")。强行提交会在后端 `services/block_generator.py:200` 抛
`ValueError: unknown kind`, 前端不会暴露这两种类型 (UI dropdown 不含)。

---

## 4. 报数模板 (跟 `measure_block` CLI 字段名一致)

把 5 个数字发回 App (步骤 3 直接填表单), 或用 CLI 离线跑 `tools/measure_block.py`:

```
# Web 表单: 5 个 number input (单位 mm, step=0.05)
outer_pitch_mm        = 35.95    # 1A
inner_pitch_mm        = 4.05     # 1B
brick_height_net_mm   = 17.05    # ②
stud_diameter_mm      = 16.10    # ③
brick_height_total_mm = 24.10    # ④
```

CLI 等价 (用于 `tools/measure_block.py`, 见其 docstring §"Raw measurements"):

```bash
./.venv/bin/python tools/measure_block.py \
    --system duplo --kind brick --units-x 2 --units-y 2 \
    --outer-pitch-mm 35.95 --inner-pitch-mm 4.05 \
    --stud-diameter-mm 16.10 \
    --brick-height-net-mm 17.05 --brick-height-total-mm 24.10 \
    --output-dir tests/e2e/fixtures/real-bricks/
```

**字段名一致性契约**: 前端 `apps/web/src/features/parametric/schema.ts`
`MEASUREMENT_FIELDS[*].key` = 后端 `apps/api/src/api/v1/parametric_blocks.py:_RAW_KEYS`
= CLI `tools/measure_block.py --outer-pitch-mm ...` = 数据库
`captures.raw_measurements_mm` JSONB 字段, **5 个 key 全部以 `_mm` 结尾**, 改名需
改 4 处 (前端 schema · 前端 api.ts · 后端 _RAW_KEYS · CLI argparse)。

---

## 5. "卡尺友好"原则 (cross-ref user memory)

> **不要让用户做"目测找中心"**, 让卡尺直接卡"可夹位置"。

DUPLO 20mm 节距在 200w 像素图上 ≈ 30 像素, 用像素反推 stud 中心误差 0.2-0.3mm (~2%);
用 0.05mm 卡尺直接卡 "外径+内径" 两个 0.05 量程内的位置, 误差 0.05mm。
`(outer + inner) / 2` 公式互消 stud_Ø 误差, 拿到的 unit 比直接量 unit 还准。

本设计在 5 个字段上的体现:

| 字段 | "卡尺友好" 体现 |
|---|---|
| 1A outer_pitch | 卡尺**跨外**, 从 brick 最左 → 最右, 卡尺外径贴两端 stud 圆周最远点 — 量程内 (≥ 36mm) |
| 1B inner_pitch | 卡尺**插入**两 stud 之间, 0.05mm 量程内 (~4mm 空隙), 0.05 卡尺可读 |
| 2 brick_height_net | 卡尺**卡底 → 顶**, 不含凸点, 量程内 |
| 3 stud_diameter | 卡尺**卡外径**单个 stud, 量程内 (~16mm) |
| 4 brick_height_total | 卡尺**卡底 → 凸点顶**, 含凸点, 量程内 |

**不要求**用户:
- ❌ 找 stud 中心 (像素法不准)
- ❌ 量 stud 间距 (跟 1A + 1B 等价但多一次量)
- ❌ 量凸点高 (从 4-2 反算更准, 避免直接量 0.05 量程外的小数)
- ❌ 量节距 (从 1A+1B 反算)

推导全在服务端 (`derive_spec_from_raw` 在 `parametric_blocks.py` + `tools/measure_block.py` 双实现, 3 行 trivial), 用户永远不需要算中间数。

---

## 6. 端到端请求-响应契约

### POST `/api/v1/parametric-blocks`

```http
POST /api/v1/parametric-blocks HTTP/1.1
Content-Type: multipart/form-data; boundary=----X

------X
Content-Disposition: form-data; name="system"

duplo
------X
Content-Disposition: form-data; name="kind"

brick
------X
Content-Disposition: form-data; name="units_x"

2
------X
Content-Disposition: form-data; name="units_y"

2
------X
Content-Disposition: form-data; name="raw_measurements_mm"

{"outer_pitch_mm":35.95,"inner_pitch_mm":4.05,"stud_diameter_mm":16.10,"brick_height_net_mm":17.05,"brick_height_total_mm":24.10}
------X--
```

→ 201 Created, `application/json`:

```json
{
  "capture_id": "...",
  "part_id": "duplo-brick-2x2",
  "status": "pending",
  "mode": "parametric_block",
  "system": "duplo",
  "kind": "brick",
  "units_x": 2,
  "units_y": 2,
  "raw_measurements_mm": { ...5 fields... },
  "derived_spec_mm": {
    "unit_mm": 20.0,
    "knob_diameter_mm": 16.10,
    "height_mm": 17.05,
    "knob_height_mm": 7.05
  },
  "cross_check_warnings": [],
  "job_id": "...",
  "created_at": "2026-06-06T..."
}
```

→ 422 (字段缺失 / JSON 错 / 数字 ≤ 0 / 越界 1..16)。

### GET `/api/v1/jobs/{job_id}/stream` (SSE)

```
event: progress
data: {"progress": 67, "stage": "parametric_generate"}

event: stage_change
data: {"stage": "mesh_export"}

event: completed
data: {"result_asset_id": "..."}

event: failed
data: {"error": "..."}
```

完整 worker 逻辑见 `apps/api/src/workers/tasks/reconstruct.py:_run_parametric_pipeline`。

---

## 7. 跟 v0.2 拍照路径的差异 (不会踩坑的对照)

| 维度 | v0.2 拍照路径 | v0.4 建模路径 |
|---|---|---|
| 触发 | `POST /captures` 4-20 张图 | `POST /parametric-blocks` 0-20 张图 + 5 数字 |
| `capture.mode` | `"photo"` (默认) | `"parametric_block"` |
| Worker 分支 | COLMAP → Open3D multi → Open3D fallback | `BlockGenerator.generate()` → `export_glb()` |
| 失败模式 | SfM register 退化 (Osmo 纯旋转 → 34 点凸包) | 5 数字超出范围 / cross-check 不通过 |
| 修复方法 | 改拍摄策略 (≥8 张 + 多 baseline) | 改 5 个 caliper 数字, 重提 |
| GLB 体积 | 9.5-24 KB (Delaunay 凸包) | 5-10 KB (BlockGenerator 几何) |
| 适合场景 | 复杂异形 / 公开规格未覆盖 | 公开规格积木 (DUPLO/LEGO/FEILE) |

两条路径**共享**一张 `captures` 表 (通过 `mode` 字段区分) + 一个 Celery 任务
(`reconstruct` 在 worker 端 `if capture.mode == "parametric_block"` 分支)。
**不要**给两个 endpoint 各写一套 schema — 共用 `ParametricBlockRead` / `ParametricBlockRequest`。

---

## 8. 常见问题 (FAQ)

**Q: 我只有 1 张照片能提交吗?**
A: 能。`photos` 字段 0-20 张任意数量, 0 张走纯 caliper 路径, 1+ 张走 "caliper + 校准" 路径。

**Q: 1B 内径距量不出来 (空隙太窄) 怎么办?**
A: 0.05mm 卡尺量程内 (~4mm) 应可量; 实在量不出, 把 1A 留空 + 2/3/4 都填, App 会把
`inner_pitch_mm = outer_pitch_mm - 2 × stud_diameter` 反推回来 (前端 `crossCheckMeasurements`
已包含此校验, 后端 `derive_spec_from_raw` 等价)。

**Q: 提交后 GLB 跟实物对不上?**
A: 按顺序排查 (跟 `docs/capture-procedure.md §4` 一致):
1. 网格朝向 (默认 brick 底面 Z=0, 顶面 Z=height+knob_h)
2. stud 间距 (检查 `unit_mm = (1A + 1B) / 2`, 2×2 DUPLO 应 = 40×40×17mm)
3. 拍照缩放 (并排渲染缩到 GLB bbox 大小, 不按原图)
4. spec 漂移 (用 `BlockSpec(unit_mm=19.8)` override, **别改** `LEGO_UNIT_MM` / `DUPLO_UNIT_MM`)

**Q: slope 怎么量?**
A: 见 `docs/measure-block-slope.svg` — 5 字段名跟 brick 不同:
`slope_length_mm` / `slope_depth_mm` / `stud_diameter_mm` / `slope_height_high_mm` /
`slope_height_total_high_mm`。低边天然为 0, 不需测量。

**Q: round / technic 为什么不支持?**
A: 4 个 kind (brick / plate / tile / slope) 已覆盖 3-5 岁 90% 实际碰到的零件; round 圆柱
跟 technic 十字轴在 `block_generator.py` 设计注释里明确 out of scope (会让 mesh boolean
复杂度跳一档, 跟 v0.4 阶段性目标不符)。见 `ROADMAP.md §v0.5` 排期。

---

## 9. 截图引用

| 截图 | 路径 | 内容 |
|---|---|---|
| 步骤 4 预览 | `tests/e2e/screenshots/parametric-preview.png` | 4 步 ladder + SSE 进度 + R3F viewer + 推导 spec 折叠 |
| 卡尺 5 点 | `docs/measure-block-annotated.svg` | brick/plate/tile 的 1A/1B/2/3/4 |
| 斜面 5 点 (新) | `docs/measure-block-slope.svg` | slope 的 slope_length/depth/height_high/stud_Ø/height_total_high |
| 采集流程 | `docs/capture-procedure.svg` | "卡尺 + 照片" 双路径合流图 |

---

## 10. 实现位置速查

| 关注点 | 路径 |
|---|---|
| 前端 wizard 入口 | `apps/web/src/features/parametric/ParametricPage.tsx` |
| 步骤 1 (照片) | `apps/web/src/features/parametric/PhotoUploadStep.tsx` |
| 步骤 2 (规格) | `apps/web/src/features/parametric/KindSelectStep.tsx` |
| 步骤 3 (5 测量) | `apps/web/src/features/parametric/MeasurementsStep.tsx` |
| 步骤 4 (预览) | `apps/web/src/features/parametric/PreviewStep.tsx` |
| 5 字段定义 + 校验 | `apps/web/src/features/parametric/schema.ts` (MEASUREMENT_FIELDS, crossCheckMeasurements) |
| API 客户端 | `apps/web/src/features/parametric/api.ts` |
| 后端路由 | `apps/api/src/api/v1/parametric_blocks.py` (_RAW_KEYS, _derive_spec_from_raw, _cross_check_raw) |
| BlockGenerator | `apps/api/src/services/block_generator.py` (BlockSpec, generate, export_glb) |
| Worker 分发 | `apps/api/src/workers/tasks/reconstruct.py` (_run_parametric_pipeline) |
| CLI 离线工具 | `tools/measure_block.py` |
| 数据库字段 | `apps/api/src/db/models.py:Capture` (mode, system, kind, units_x, units_y, raw_measurements_mm, derived_spec_mm, cross_check_warnings) — alembic `0003_captures_parametric_block.py` |
