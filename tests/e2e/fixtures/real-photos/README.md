# real-photos/ — 真实积木照片 (不入 git)

这个目录放实物拍摄的照片, 用来做 v0.3 spec 验证 (跟 BlockGenerator 生成的 GLB
并排对比)。照片本身**不进 git** (21MB 量级 + 拍出来一次就够)。

## 用途

- **手工跑分**: `apps/api/src/services/block_generator.py` 生成的 GLB 跟这里
  的照片并排对比, 验证 spec 准确性 (DUPLO 2x2 / LEGO 2x4 等)。
- **手工回归**: 改 `BlockSpec` override 后, 用同一批照片再跑一次确认偏差。
- **不要**被 pytest 用 — pytest 跑的是 `apps/api/tests/fixtures/synth_cube.py`
  生成的合成图。

## 文件命名约定

| 模式 | 含义 |
|---|---|
| `DJI_*.JPG` | DJI Osmo Pocket 3 拍, 环绕模式 |
| `IMG_*.HEIC` | iPhone 拍, 半顶视 (HEIC 格式, 后端需 `pillow-heif`) |
| `IMG_*.JPG` | iPhone 拍, 转码后的 JPG (可选) |

## 怎么把照片放进来

直接拷进这个目录即可, **不需要 commit**:

```bash
# 拍完拷过来
cp ~/Pictures/DJI_*.JPG tests/e2e/fixtures/real-photos/
cp ~/Pictures/IMG_*.HEIC tests/e2e/fixtures/real-photos/

# 验证 .gitignore 生效
git status tests/e2e/fixtures/real-photos/
# → 应该只显示 .gitignore + README.md, 不会列照片
```

## v0.3 当前这批 (13 张, ~21MB)

| 数量 | 来源 | 用途 |
|---|---|---|
| 11 张 DJI | DJI Osmo Pocket 3, 环绕模式 | 13 张 baseline: 9.5KB GLB (243v/482f) |
| 2 张 iPhone HEIC | iPhone, 半顶视 | 单图 4 张 baseline: 24KB GLB |

这批照片是 v0.2 → v0.3 切换期间拍的, 用于:
1. 验证 COLMAP 路线不行 (纯旋转 + DUPLO 简单纹理 → 退化到 34 点)
2. 触发参数化建模路线决策
3. 给 BlockGenerator 做第一次 spec 验证

新拍的照片 (按 `docs/capture-procedure.md`) 也放这里, 命名 `IMG_<part_id>_<angle>.JPG`。
