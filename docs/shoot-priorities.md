# 下一批拍摄清单 (v0.3 spec validation)

测一块拍一块, 跑 `tools/measure_block.py` 出 GLB + spec.json, 跟真实积木对比看 spec 偏不偏。
当前 v0.3 demo (16/16 GLB) 全是合成 spec, 还没拿真实积木挨个验证过 — 这张清单是优先级。

## 优先 — 跨 system × kind 覆盖 (把 4 系统 × 4 kind 拼图补全)

| # | 实物 | system | kind | size | 5 测量 | spec.json |
|---|---|---|---|---|---|---|
| ✅ | 费乐 2x2 brick | feile | brick | 2x2 | ✅ done | ✅ `feile-2x2-brick-measured/` |
| 1 | 费乐 1x1 brick | feile | brick | 1x1 | ⬜ | ⬜ |
| 2 | 费乐 2x4 brick | feile | brick | 2x4 | ⬜ | ⬜ |
| 3 | 费乐 2x2 plate | feile | plate | 2x2 | ⬜ | ⬜ |
| 4 | 费乐 2x2 tile | feile | tile | 2x2 | ⬜ | ⬜ |
| 5 | 费乐 2x2 slope | feile | slope | 2x2 | ⬜ | ⬜ |
| 6 | DUPLO 2x2 brick (实拍) | duplo | brick | 2x2 | ⬜ | ⬜ |
| 7 | LEGO 2x4 brick (实拍) | lego | brick | 2x4 | ⬜ | ⬜ |

## 不优先 (v0.4+ 或 看心情)

- Round 1x1 tile (圆顶)
- Technic cross-axle brick (cross hole)
- Baseplate 16x16
- 异形 size (1x2 / 1x3 / 2x3 / 4x4) — BlockGenerator 已支持, 拍不拍随你

## 拍摄 + 跑命令 流程

1. **量 5 个数** — `docs/measure-block-annotated.svg` 看图
2. **拍 4-5 张照片** — 顶视 1 + 侧视 3 (120° 间隔) + 45° 斜视 1, 存 `tests/e2e/fixtures/real-photos/IMG_<part_id>_<angle>.JPG`
3. **写 JSON** — 复制 `tools/examples/{system}-{size}-{kind}.json` → `{system}-{size}-{kind}-measured.json`, 改 5 个数
4. **跑命令**:
   ```bash
   ./.venv/bin/python tools/measure_block.py \
     --config tools/examples/{system}-{size}-{kind}-measured.json \
     --output-dir tests/e2e/fixtures/real-bricks/{part_id}/
   ```
5. **看输出** — `deviation from public spec` 全在 0.2mm 内 = spec 对, 超出就改 BlockGenerator public spec

## 跨项目记号

- 公差 ±0.2mm 是 user 卡尺 + BlockSpec 工程余量的合取
- cross-check warning (stud_Ø 跟 (1A-1B)/2 差 > 0.5mm) 说明 1B 量错或砖不平
- 1A 跟 1B 必须 1A > 1B, 反了是 stud 圆周读反了
