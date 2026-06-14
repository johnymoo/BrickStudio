# 零件库基础 (Part / Module Library Foundation) — 设计文档

> Issue #5 · 分支 `v0.2-development` · 2026-06-14
> 目标：为后续 FEILE 拼搭规划器准备「可持久、可命名、有状态、可复用」的零件库数据与 UX 基础。
> **非目标**：完整拼搭/装配算法、多用户权限/分享、库存数量、Android 库界面。

## 1. 背景与目标

ScanForge / Android 端正在收敛下一刀 A1+A2（采集可靠性 + 现场标定）。后端并行轨道要为下一产品步做准备：当更多 FEILE 模块能走完 `capture → recognized 或 needs_measurement → GLB` 后，后端应能把这些模块沉淀成可复用的规划输入。

现状（已实现）：

- 三张表 `captures → jobs → assets`。`captures` 已带丰富结构化字段：`mode` ∈ {`photo`, `parametric_block`, `ar_recognized`}、`system`、`kind`、`units_x/y`、`derived_spec_mm`、`recognition_result` 等。
- 每个 capture 派生一个 `reconstruct` job；job 产出的首个 `mesh_gltf` asset 即 GLB（经 `JobRead.result_asset_id` 暴露）。
- `parametric_block`（卡尺）与 `ar_recognized`（AR）capture 都已通过共享 worker 产出 GLB。
- Web：首页 `JobList`、`CaptureDetail`、`JobDetail`、`/parametric` 4 步向导；卡片式 UI + 主题系统。

因此「已完成的 FEILE 模块」的数据其实已经躺在 `captures`/`assets` 行里。本 issue 要做的是让它们成为**可持久、可命名、有状态、可复用**的零件库条目。

## 2. 关键设计决策（已与需求方确认）

| # | 决策 | 选择 |
|---|---|---|
| D1 | 零件粒度 | **每个 capture 一个零件（1:1）**。每次入库新建一条 parts 行，回链源 capture + GLB asset。去重/合并留作未来。 |
| D2 | 入库时机 | **完成即自动入库**。job 完成、GLB 就绪时自动生成 parts 行；无手动「提升」按钮。 |
| D3 | 入库范围 | **所有完成的 capture**（含 photo SfM）。photo 的 system/kind/units 先留空，用户后补。 |
| D4 | Web 承载 | **新建 `/library` 列表 + `/library/:id` 详情**两个独立路由。 |
| D5 | 架构方案 | **方案 A：Worker 端入库 + 快照式去规范化**（见 §4）。 |
| D6 | 状态模型 | **拆分正交**：`status ∈ {pending, verified, rejected}`（评审）+ `source_mode` 快照（来源徽章）。不使用 issue 原文的单字段 4 值枚举（它把来源与评审混在一起）。 |
| D7 | 几何字段 | **不加专门的角度/弧度列**（YAGNI）。slope 角度由 `kind=slope` 隐含（写死 45°）；round/technic 尚不支持。未来几何参数进可扩展的 `derived_spec_mm` JSONB，parts 表零迁移。 |

## 3. 数据模型

新增第 4 张表 `parts`，1:1 关联 capture，**快照式**存规格。

```
parts
─────────────────────────────────────────────────────────────────────
id              UUID  PK
capture_id      UUID  FK→captures.id  UNIQUE  ON DELETE CASCADE   # 1:1 + 幂等键
asset_id        UUID  FK→assets.id    NULLABLE ON DELETE SET NULL # 指向 GLB asset
source_mode     str                                              # 快照 capture.mode
system          str | null                                       # 快照
kind            str | null                                       # 快照
units_x         int | null                                       # 快照
units_y         int | null                                       # 快照
derived_spec_mm JSONB | null                                     # 快照（几何规格袋，未来可扩展）
color           str | null                                       # 可选颜色/材质标签
name            str                                              # 展示名，默认 = capture.part_id
notes           text | null                                      # 用户备注
status          str   DEFAULT 'pending'                          # 评审状态
created_at      timestamptz
updated_at      timestamptz
─────────────────────────────────────────────────────────────────────
index: ix_parts_capture_id (unique), ix_parts_status
```

**状态模型（D6）**

- `status`：评审生命周期。默认 `pending`（待核验）→ 用户改为 `verified` 或 `rejected`。
- `source_mode`：来源快照（`photo` / `parametric_block` / `ar_recognized`），UI 作只读徽章（「照片扫描」/「卡尺测量」/「AR 识别」）。
- 两者正交：一个零件可以「AR 识别 **且** 已核验」，单字段枚举会丢失这层信息。issue 原文的 `recognized`/`measured` 映射为 source_mode 徽章，`verified`/`rejected` 映射为 status。

**快照语义**

- 入库时从 capture 拷贝 `source_mode`/`system`/`kind`/`units_x`/`units_y`/`derived_spec_mm`；之后 capture 改动**不回灌**（零件是独立可复用单元，规划器查一张表即可）。

**ORM 关系**

- `Capture.part: Mapped[Part | None] = relationship(back_populates="capture", uselist=False, lazy="selectin")`
- `Part.capture: Mapped[Capture] = relationship(back_populates="part", lazy="selectin")`

**迁移**：`0005_create_parts.py`，纯加表（`down_revision = "0004_captures_ar_recognized"`），可干净 `downgrade`（drop table）。不改动现有任何表。

## 4. 自动入库流程（方案 A）

**钩子位置**：`workers/tasks/reconstruct.py` 的 `_finalize_mesh_pipeline`，在 `_write_asset()` 之后、发 `completed` 事件之前。三条流水线（`_run_pipeline` / `_run_parametric_pipeline` / `_run_ar_recognized_pipeline`）都汇到此处，一处插入即全覆盖（满足 D3）。

**新增 service**：`apps/api/src/services/part_promoter.py`，函数：

```python
async def promote_capture_to_part(session, capture_id: UUID, asset_id: UUID) -> None:
    """
    1. 读 capture 行（mode / system / kind / units_x / units_y /
       derived_spec_mm / part_id）。
    2. 按 capture_id UPSERT parts：
       - 不存在 → INSERT：
           name = capture.part_id, status = "pending",
           source_mode = capture.mode,
           system/kind/units_x/units_y/derived_spec_mm 从 capture 快照,
           asset_id = 新 GLB。
       - 已存在（worker 重跑）→ UPDATE 仅 asset_id + 规格快照 + source_mode；
           name / notes / status 原样保留（不覆盖用户编辑）。
    """
```

**调用方式**：与 worker 内其它 DB 操作一致，`asyncio.run(_promote(...))` 包一层 sync。

**错误处理（关键）**：入库整段包 `try/except`，失败只 `logger.warning`，**不让 job 标记失败**。GLB 已成功产出并写了 asset，入库是完成后的附加动作，不应因它出错把一次成功的重建判成失败；下次 worker 重跑会再 upsert 补上。

**幂等**：`parts.capture_id` 上的 UNIQUE 约束 + UPSERT，保证 worker 重试（`max_retries=1`）不产生重复零件行。

**为什么放 finalize 而非 `_set_job_completed`**：finalize 处直接有 `capture_uuid` + `asset_id` 在手，无需再查；且 asset 行刚写完，FK 必然有效。

**`needs_measurement` AR 采集不入库**：低置信度 AR 采集不派 job、不产 GLB，永远不会到 `_finalize_mesh_pipeline`，因此不会自动入库——直到用户补卡尺测量走 parametric 路径完成后才入库。这与 D2「完成即自动入库」一致（未完成的采集不算「完成」）。

## 5. API 契约

新增 `apps/api/src/api/v1/library.py`，挂 `/api/v1/library`，注册进 `api/v1/__init__.py` + `app/main.py`。

### 5.1 `GET /api/v1/library` — 列出零件

- query：`status`（可选，精确匹配 `pending`/`verified`/`rejected`）、`limit`（1–100，默认 20）。
- 不传 `status` 返回全部（含 rejected），按 `created_at desc`。后端不做隐式过滤；「默认隐藏 rejected」是前端行为（前端按需带 `status` 或客户端过滤）。
- 返回 `list[LibraryPartRead]`。

### 5.2 `GET /api/v1/library/{id}` — 单个零件

- 404 → 新 error `PartNotFound`（照 `CaptureNotFound`/`AssetNotFound` 模式，落标准 error envelope）。
- 返回 `LibraryPartRead`。

### 5.3 `PATCH /api/v1/library/{id}` — 改展示元数据/状态

- body `LibraryPartUpdate`：`{ name?, notes?, status? }`，全部可选，部分更新。
- `status` 校验 ∈ {`pending`, `verified`, `rejected`}（非法 → 422）。
- 命中即 bump `updated_at`，返回更新后的 `LibraryPartRead`。

> 写端点仅 PATCH——「create-from-capture」已由 §4 自动入库覆盖，无 `POST /library`。

### 5.4 Pydantic schema（加到 `models/schemas.py`）

```python
class LibraryPartRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    part_id: UUID = Field(validation_alias="id")
    capture_id: UUID
    asset_id: UUID | None = None        # 前端用现有 GET /assets/{asset_id} 拿预签名 GLB URL
    source_mode: str
    system: str | None = None
    kind: str | None = None
    units_x: int | None = None
    units_y: int | None = None
    derived_spec_mm: dict[str, Any] | None = None
    color: str | None = None
    name: str
    notes: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime

class LibraryPartUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    notes: str | None = None
    status: Literal["pending", "verified", "rejected"] | None = None
```

**复用**：列表不逐行预签名 GLB（浪费），只回 `asset_id`；详情页用现成 `getAssetUrl(asset_id)` 走已有 `/assets/{id}` 预签名流程 → issue 的「fetch linked GLB asset」零新代码。

## 6. Web

**路由**（`apps/web/src/App.tsx` 加 2 条）：`/library` → `LibraryList`，`/library/:id` → `LibraryDetail`。

**入口**（`apps/web/src/components/AppHeader.tsx`）：`NAV_ITEMS` 加 `{ path: "/library", label: "零件库", testId: "header-library-link" }`。

**`api.ts` 新增**：

```ts
export interface LibraryPart {
  part_id: string; capture_id: string; asset_id: string | null;
  source_mode: string;
  system: string | null; kind: string | null;
  units_x: number | null; units_y: number | null;
  derived_spec_mm: Record<string, number> | null;
  color: string | null; name: string; notes: string | null;
  status: "pending" | "verified" | "rejected";
  created_at: string; updated_at: string;
}
listLibraryParts(status?, limit?, signal?): Promise<LibraryPart[]>
getLibraryPart(id, signal?): Promise<LibraryPart>
updateLibraryPart(id, { name?, notes?, status? }, signal?): Promise<LibraryPart>  // PATCH
```

**`LibraryList`**（`features/library/LibraryList.tsx`）：

- 顶部 status tab：`待核验 / 已核验 / 已拒绝`。**默认展示非 rejected**（待核验 + 已核验合并），点「已拒绝」tab 才显示被拒零件。（避免「全部却隐藏拒绝」的歧义。）
- 卡片列表复用现有 `card` 样式：显示 `name`、`source_mode` 徽章、`system kind units_x×units_y`、`status` 徽章、相对时间。
- 每张卡 `Link` 到 `/library/:id`。空态复用现有 EmptyState 风格。

**`LibraryDetail`**（`features/library/LibraryDetail.tsx`）：

- `<Viewer assetId={part.asset_id} />` 看 GLB（`asset_id` 为空则占位提示）。`Viewer` 只需 `assetId` 一个 prop。
- 可编辑 `name`（行内输入）、`notes`（textarea），保存调 `updateLibraryPart`。
- 状态操作按钮：`标记已核验` / `标记拒绝` / `重置为待核验`，调 PATCH `status`。
- 规格只读展示：source_mode 徽章 + system/kind/units + derived_spec_mm。
- `Link` 回源 capture（`/captures/:id`）保留 provenance。

**徽章**：新增轻量 `<PartBadge>`（或内联 Tailwind class）渲染 status/source_mode，不改动现有 `StatusBadge`（专给 job）以免波及 job 列表。

## 7. 测试策略

对齐验收标准。

**后端 pytest**

1. `tests/test_part_promoter.py`（service 单测）：
   - parametric capture → 入库行快照正确（source_mode/system/kind/units/derived_spec_mm，默认 name=part_id，status=pending，asset_id 指向 GLB）。
   - photo capture → 入库行 system/kind/units 为 null，source_mode=photo。
   - 幂等：调两次只更新 asset_id + 规格，保留用户编辑过的 name/notes/status。
2. `tests/test_library.py`（API 端点）：
   - `GET /library` 列表 + `status` 过滤；`GET /library/{id}` 200 与 404(`PartNotFound`)；`PATCH` 改 name/notes/status，非法 status→422，缺失→404。
3. **promote-from-capture 集成流程**（验收点）：复用 `test_reconstruction_parametric.py` / `test_ar_recognized_worker.py` 的 worker 夹具，跑完 finalize 后断言 parts 行出现且正确挂到 capture + asset。
4. 回归：现有 `test_captures.py` / `test_assets.py` 不改也应全过——证明加表+钩子不破坏既有 capture 列表/详情/asset 下载流程。

**前端 vitest**

5. `LibraryList.test.tsx`：渲染零件卡、status tab 过滤、默认隐藏 rejected。
6. `LibraryDetail.test.tsx`：渲染、编辑 name/notes 触发 PATCH、状态按钮触发 PATCH。
7. `api.test.ts` 补：3 个新 helper（list/get/update）的请求路径与参数。

**E2E（Playwright，必选）**

8. `tests/e2e/library.spec.ts`：完成一个 parametric 采集 → 跳 `/library` → 看到该零件 → 点「标记已核验」→ 断言 status 徽章变更。落截图 `tests/e2e/screenshots/library-verified.png`。照 `full-flow.spec.ts` / 现有 parametric e2e 写法，跑在 docker 全栈上。

**迁移**：`0005_create_parts.py` 走现有 conftest 的 alembic upgrade，纯加表、可干净 downgrade。

## 8. 验收标准映射

| issue 验收标准 | 本设计覆盖 |
|---|---|
| 完成的 AR/parametric capture 可被提升为可复用零件，不丢 capture provenance | §4 自动入库 + `parts.capture_id` FK 回链；§6 详情页回链源 capture |
| FEILE brick/plate/tile/slope 模块有足够结构化数据喂未来规划器 | §3 快照 system/kind/units/derived_spec_mm；§2 D7 几何可扩展 |
| 现有 capture 列表/详情 + asset 下载流程仍工作 | §3 纯加表不改现有表；§7.4 回归测试 |
| 后端测试覆盖 schema/API + ≥1 条 promote-from-capture 流程 | §7.1–7.3 |
| Web 能展示库状态以核验模块（即便规划器未实现） | §6 列表+详情+状态操作；§7.5–7.8 |

## 9. 范围边界（Out of Scope）

- 完整拼搭/装配算法。
- 多用户权限 / 分享。
- 库存数量。
- Android 库界面（除非后端契约稳定后需要）。
- 零件去重/合并（D1：1:1，去重未来再做）。
- round / technic / 任意角度斜面几何（D7：未来 P1，进 `derived_spec_mm`）。
