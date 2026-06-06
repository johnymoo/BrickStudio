# 实物采集流程 (v0.3 阶段)

> 阶段三的核心变化: **从"拍照 → SfM 反推" 切到"拍实物 → 对比验证 spec"**。模型由 BlockGenerator 参数化生成, 照片只用来证明"生成的 GLB 跟手里的实物一样"。

## 1. 当前采集流程

![实物采集 → spec 验证流程](./capture-procedure.svg)

> 流程图 SVG 源文件: `docs/capture-procedure.svg` (可拖到浏览器看大图, 也可改完用 `mavis mcp call matrix upload_to_cdn` 转发)。

**双路径结构**:
- **左路 (绿/蓝)**: 卡尺量 4 个关键尺寸 → 写 `BlockSpec` → `generate()` → `export_glb()` → `/tmp/*.glb`
- **右路 (黄)**: 拍 4-5 张 reference 照片 → 上传 MinIO
- **合流 (粉)**: 实拍图 vs GLB 并排渲染, 偏差 ≤ 0.2mm 通过, 否则改 `BlockSpec` override (`unit_mm=...` 之类) 重做
- **沉淀 (灰)**: 通过的 spec 写回 `tests/e2e/fixtures/real-bricks/{kind}/{size}.json`

**关键点**:
- 拍 4-5 张就够, **不再追求 13 张 / 多角度 baseline** (那是 v0.2 SfM 思路)。
- 4 个核心尺寸必须卡尺量, **不能靠像素反推** (DUPLO 20mm 节距在 200w 像素图上 ~30 像素, 精度不够)。
- spec 跟 spec 不齐: 用手里的实物改 `BlockSpec` 的 override (`unit_mm=...`), 不改 `LEGO_UNIT_MM` 这些公开常量。
- 并排渲染输出到 `docs/validation/{part_id}-side-by-side.png`, 跟 GLB 一起 commit, **当 spec 验证的可视化证据**。

## 1.1 测量示意 (4 个核心尺寸)

量之前先看这张图, 5 个编号对应 5 个**卡尺可直接卡**的原始测量（不是 4 个 spec 中心距——中心距量不准, 改成外径+内径反算）:

![测量示意](./measure-block-annotated.svg)

- **1A** `outer_pitch_mm` — 砖块总长 (跨两端 stud 圆周, 大尺寸好量)
- **1B** `inner_pitch_mm` — 两 stud 圆周之间空隙 (4mm 量程内, 0.05 卡尺可量)
- **②** `brick_height_net_mm` — 砖块净高 (底面 → 砖顶, 不含凸点)
- **③** `stud_diameter_mm` — 单个 stud 直径 (任选一个测外径)
- **④** `brick_height_total_mm` — 砖块总高 (底面 → 凸点顶, 含凸点)

**推导**（`tools/measure_block.py` 自动算, 不用手算）:

| BlockSpec 字段 | 公式 | 来源 |
|---|---|---|
| `unit_mm` | `(1A + 1B) / 2` | stud_Ø 误差互消 |
| `knob_diameter_mm` | `③` | 直接用 |
| `height_mm` | `②` | 直接用 |
| `knob_height_mm` | `④ - ②` | 总高 - 净高 |

**冗余校验**: `stud_diameter_mm` 跟 `(1A - 1B) / 2` 应一致, 差 > 0.5mm 自动报警 (说明 1B 量错了)。

量完把 5 个数字发回来, 格式任选 (默认单位 mm):

```
1A=36.05
1B=3.95
2=17.02
3=16.10
4=24.05
```

或一句话:

```
1A=36.05, 1B=3.95, 2=17.02, 3=16.10, 4=24.05
```

拿到数字我跑 `tools/measure_block.py` 出 GLB + spec.json + 3-view preview, 如果校验通过会显示 "derived unit_mm = 20.000", 不通过会出 ⚠。

## 2. 采集设备建议

| 设备 | 用途 | 替代方案 |
|---|---|---|
| 游标卡尺 (0.05mm 精度) | 4 个核心尺寸 | 直尺 (只能验 brick h) |
| 手机 (iPhone 14+) | 顶视 + 侧视 4 张 | DJI Osmo Pocket 3 |
| 拍摄环境 | 自然光 + A4 白纸当背景, 阴影角度 30-45° | — |

不需要: 转盘 / 测距仪 / 校色卡 (这些是 SfM 用的, 我们不做 SfM)。

## 3. 进度跟踪

| 实物 | kind | system | size | 卡尺 | 4 照片 | GLB | 对比图 | spec.json |
|---|---|---|---|---|---|---|---|---|
| DUPLO 2x2 brick | brick | duplo | 2x2 | ⬜ | ✅ 13 张 | ✅ 9.5KB | ⬜ | ⬜ |
| LEGO 2x4 brick | brick | lego | 2x4 | ⬜ | ⬜ | ✅ | ⬜ | ⬜ |
| DUPLO 2x2 plate | plate | duplo | 2x2 | ⬜ | ⬜ | ✅ | ⬜ | ⬜ |
| DUPLO 2x2 tile | tile | duplo | 2x2 | ⬜ | ⬜ | ✅ 10KB | ⬜ | ⬜ |
| DUPLO 2x2 slope | slope | duplo | 2x2 | ⬜ | ⬜ | ✅ | ⬜ | ⬜ |
| ... | | | | | | | | |

## 4. 如果照片显示跟 GLB 不一致

按下面顺序排查:

1. **网格朝向** — 默认 brick 底面在 Z=0, 顶面在 Z=height+knob_h。Three.js 加载时注意相机 up vector。
2. **stud 间距** — `BlockSpec.units_x × unit_mm` 算出来对不对。比如 2x2 DUPLO = 40×40×17mm, 不是 32×32 (那是错把 unit 当 16)。
3. **拍照缩放** — 并排渲染时把照片缩到 GLB 的 bbox 框大小, 不要按原图大小对齐。
4. **spec 漂移** — 手里这批货可能跟 LEGO.com 公开规格有 ±0.2mm 偏差, 用 `BlockSpec(unit_mm=19.8)` 之类 override, **别改 `LEGO_UNIT_MM` / `DUPLO_UNIT_MM` 常量**。
