#!/usr/bin/env python3
"""Measure a real brick and emit a parametric GLB + spec.json + 3-view preview.

This is the user-side of the v0.3 workflow. You bring the caliper numbers,
this script turns them into a watertight GLB that matches the physical
part to within spec tolerance.

Two input modes
---------------

1. **Raw measurements (recommended)** — 5 caliper-friendly numbers, no
   "find the center" needed::

       raw_measurements_mm:
         outer_pitch_mm:           35.95  # 1A: brick 总长 (跨两端 stud 圆周)
         inner_pitch_mm:            4.05  # 1B: 两 stud 圆周之间空隙
         brick_height_net_mm:      17.05  # ②: 砖块净高 (底面 → 砖顶, 不含凸点)
         stud_diameter_mm:         16.10  # ③: 单个 stud 直径
         brick_height_total_mm:    24.10  # ④: 砖块总高 (底面 → 凸点顶, 含凸点)

   Derivation: ``unit = (1A + 1B) / 2``, ``knob_h = ④ - ②``, etc. See
   ``docs/measure-block-annotated.svg`` for the 5 calliper points.

   Cross-check: ``stud_diameter_mm`` should match ``(1A - 1B) / 2`` within
   0.5 mm; a bigger gap means 1B was misread.

2. **Spec override (legacy / direct)** — pass the 4 BlockSpec fields
   directly. Useful when you already have a trusted caliper number for
   ``unit_mm`` itself::

       measured:
         unit_mm:           20.0
         height_mm:         17.0
         knob_diameter_mm:  16.0
         knob_height_mm:     7.0

Usage
-----

JSON config (recommended)::

    ./.venv/bin/python tools/measure_block.py \\
        --config examples/duplo-2x2-brick.json \\
        --output-dir tests/e2e/fixtures/real-bricks/

Inline CLI (raw measurements)::

    ./.venv/bin/python tools/measure_block.py \\
        --system duplo --kind brick --units-x 2 --units-y 2 \\
        --outer-pitch-mm 35.95 --inner-pitch-mm 4.05 \\
        --stud-diameter-mm 16.10 \\
        --brick-height-net-mm 17.05 --brick-height-total-mm 24.10 \\
        --output-dir /tmp/measure-test/

What it produces
----------------
- ``{output_dir}/{part_id}.glb`` — watertight mesh
- ``{output_dir}/{part_id}.json`` — full BlockSpec + raw measurements +
  derived spec + deviation from public spec
- ``{output_dir}/{part_id}-preview.png`` — top / front / side ortho render
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

# Make apps.api.src importable without `pip install -e`.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "apps" / "api" / "src"))

from services.block_generator import (  # type: ignore  # noqa: E402
    DUPLO_BRICK_HEIGHT_MM,
    DUPLO_KNOB_DIAMETER_MM,
    DUPLO_KNOB_HEIGHT_MM,
    DUPLO_UNIT_MM,
    FEILE_BRICK_HEIGHT_MM,
    FEILE_KNOB_DIAMETER_MM,
    FEILE_KNOB_HEIGHT_MM,
    FEILE_UNIT_MM,
    LEGO_BRICK_HEIGHT_MM,
    LEGO_KNOB_DIAMETER_MM,
    LEGO_KNOB_HEIGHT_MM,
    LEGO_UNIT_MM,
    BlockSpec,
    export_glb,
    generate,
)


# Per-system public spec baseline (brick kind only). FEILE values come
# from the first user 2x2 measurement 2026-06-06 (knob_Ø=9.4 mm,
# unit=16 mm 节距) — see apps/api/src/services/block_generator.py for
# the full FEILE_* constants and provenance.
PUBLIC_SPEC = {
    "duplo": {
        "unit_mm": DUPLO_UNIT_MM,
        "height_mm": DUPLO_BRICK_HEIGHT_MM,
        "knob_diameter_mm": DUPLO_KNOB_DIAMETER_MM,
        "knob_height_mm": DUPLO_KNOB_HEIGHT_MM,
    },
    "lego": {
        "unit_mm": LEGO_UNIT_MM,
        "height_mm": LEGO_BRICK_HEIGHT_MM,
        "knob_diameter_mm": LEGO_KNOB_DIAMETER_MM,
        "knob_height_mm": LEGO_KNOB_HEIGHT_MM,
    },
    "feile": {
        "unit_mm": FEILE_UNIT_MM,
        "height_mm": FEILE_BRICK_HEIGHT_MM,
        "knob_diameter_mm": FEILE_KNOB_DIAMETER_MM,
        "knob_height_mm": FEILE_KNOB_HEIGHT_MM,
    },
}

# Tolerance (mm) for the stud_Ø cross-check between the direct stud
# diameter reading and the (1A - 1B) / 2 derivation. 0.5 mm is roughly
# the worst case when 1B (~4 mm gap) is read at the caliper's 0.05 mm
# resolution twice on each side, plus a little hand-shake.
CROSS_CHECK_TOL_MM: float = 0.5


def _render_3view(mesh, out_png: Path) -> None:
    """Top / front / side ortho projections → 1 PNG."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import PatchCollection
    from matplotlib.patches import Polygon

    fig, axes = plt.subplots(1, 3, figsize=(15, 5))

    views = [
        ("top (x→y)",   0, 1),
        ("front (x→z)", 0, 2),
        ("side (y→z)",  1, 2),
    ]
    ax_letters = ["x", "y", "z"]

    for ax, (title, ax_a, ax_b) in zip(axes, views):
        polys = []
        for tri in mesh.faces:
            pts = mesh.vertices[tri][:, [ax_a, ax_b]]
            polys.append(Polygon(pts, closed=True))
        pc = PatchCollection(
            polys, alpha=0.7, edgecolor="#0f172a",
            facecolor="#bfdbfe", linewidth=0.4,
        )
        ax.add_collection(pc)
        ax.autoscale()
        ax.set_aspect("equal")
        ax.set_title(title, fontsize=11)
        ax.set_xlabel(f"{ax_letters[ax_a]} (mm)")
        ax.set_ylabel(f"{ax_letters[ax_b]} (mm)")
        ax.grid(True, alpha=0.3)

    fig.suptitle(
        f"{mesh.metadata.get('system', '?')} "
        f"{mesh.metadata.get('kind', '?')} "
        f"{mesh.metadata.get('units_x', '?')}x{mesh.metadata.get('units_y', '?')}",
        fontsize=13, fontweight="bold",
    )
    fig.tight_layout()
    fig.savefig(out_png, dpi=120)
    plt.close(fig)


def _load_config(args) -> dict:
    if args.config:
        return json.loads(Path(args.config).read_text(encoding="utf-8"))
    return {}


# ---- Raw → spec derivation -------------------------------------------------
def derive_spec_from_raw(raw: dict[str, float]) -> dict[str, float]:
    """Map 5 raw calliper measurements to the 4 BlockSpec override fields.

    Geometry:
        outer_pitch     = unit + stud_Ø
        inner_pitch     = unit - stud_Ø
        height_total    = brick_net_height + knob_height
        stud_diameter   = knob_diameter
        brick_net       = height
    Solving:
        unit            = (outer_pitch + inner_pitch) / 2
        stud_Ø derived  = (outer_pitch - inner_pitch) / 2
        height          = brick_height_net
        knob_height     = brick_height_total - brick_height_net
        knob_diameter   = stud_diameter (direct)
    """
    unit = (raw["outer_pitch_mm"] + raw["inner_pitch_mm"]) / 2.0
    knob_d = raw["stud_diameter_mm"]
    height = raw["brick_height_net_mm"]
    knob_h = raw["brick_height_total_mm"] - raw["brick_height_net_mm"]
    return {
        "unit_mm": unit,
        "knob_diameter_mm": knob_d,
        "height_mm": height,
        "knob_height_mm": knob_h,
    }


def cross_check_raw(raw: dict[str, float]) -> list[str]:
    """Sanity-check the 5 raw measurements against each other.

    Returns a list of warning strings. Empty list = all good.
    """
    warnings: list[str] = []
    if raw["outer_pitch_mm"] <= raw["inner_pitch_mm"]:
        warnings.append(
            f"1A ({raw['outer_pitch_mm']}) should be > 1B "
            f"({raw['inner_pitch_mm']}); got 1A <= 1B."
        )
    if raw["inner_pitch_mm"] <= 0:
        warnings.append(f"1B ({raw['inner_pitch_mm']}) must be > 0.")
    if raw["brick_height_total_mm"] <= raw["brick_height_net_mm"]:
        warnings.append(
            f"④ ({raw['brick_height_total_mm']}) should be > ② "
            f"({raw['brick_height_net_mm']}); got ④ <= ② "
            f"(凸点 can't have negative or zero height)."
        )
    derived_stud_d = (raw["outer_pitch_mm"] - raw["inner_pitch_mm"]) / 2.0
    diff = abs(derived_stud_d - raw["stud_diameter_mm"])
    if diff > CROSS_CHECK_TOL_MM:
        warnings.append(
            f"stud_Ø cross-check: ③ ({raw['stud_diameter_mm']}) vs "
            f"(1A-1B)/2 ({derived_stud_d:.3f}) differ by {diff:.3f} mm "
            f"(> {CROSS_CHECK_TOL_MM}); check 1B reading."
        )
    return warnings


# ---- Config / CLI merging --------------------------------------------------
def _merge_raw(args, cfg: dict) -> dict[str, float] | None:
    """Return the 5 raw measurements if the user provided them, else None."""
    raw_cfg: dict = dict(cfg.get("raw_measurements_mm", {}))
    cli = {
        "outer_pitch_mm":        args.outer_pitch_mm,
        "inner_pitch_mm":        args.inner_pitch_mm,
        "stud_diameter_mm":      args.stud_diameter_mm,
        "brick_height_net_mm":   args.brick_height_net_mm,
        "brick_height_total_mm": args.brick_height_total_mm,
    }
    for k, v in cli.items():
        if v is not None:
            raw_cfg[k] = v
    if len(raw_cfg) == 5:
        # All 5 must be present for the raw path.
        return {k: float(raw_cfg[k]) for k in cli}
    if raw_cfg:
        incomplete = set(cli) - set(raw_cfg)
        raise SystemExit(
            f"raw_measurements_mm provided but missing: {sorted(incomplete)}"
        )
    return None


def _merge_measured(args, cfg: dict) -> dict[str, float]:
    """Return the 4 spec override fields (legacy path)."""
    m_cfg: dict = dict(cfg.get("measured", {}))
    cli = {
        "unit_mm":           args.unit_mm,
        "height_mm":         args.height_mm,
        "knob_diameter_mm":  args.knob_diameter_mm,
        "knob_height_mm":    args.knob_height_mm,
    }
    for k, v in cli.items():
        if v is not None:
            m_cfg[k] = v
    return {k: float(v) for k, v in m_cfg.items()}


def _merge_meta(args, cfg: dict) -> dict:
    system  = args.system  if args.system  is not None else cfg.get("system", "duplo")
    kind    = args.kind    if args.kind    is not None else cfg.get("kind", "brick")
    units_x = args.units_x if args.units_x is not None else cfg.get("units_x", 2)
    units_y = args.units_y if args.units_y is not None else cfg.get("units_y", 2)
    part_id = args.part_id if args.part_id is not None else cfg.get(
        "part_id", f"{system}-{units_x}x{units_y}-{kind}",
    )
    return {
        "system": system, "kind": kind,
        "units_x": units_x, "units_y": units_y,
        "part_id": part_id, "note": cfg.get("note", ""),
    }


# ---- Main command ----------------------------------------------------------
def cmd_measure(args) -> int:
    cfg = _load_config(args)
    meta = _merge_meta(args, cfg)
    raw = _merge_raw(args, cfg)
    measured = _merge_measured(args, cfg)

    system, kind, units_x, units_y = (
        meta["system"], meta["kind"], meta["units_x"], meta["units_y"],
    )
    part_id = meta["part_id"]

    cross_warnings: list[str] = []
    if raw is not None:
        cross_warnings = cross_check_raw(raw)
        measured_from_raw = derive_spec_from_raw(raw)
        # Raw-derived wins; CLI/JSON spec overrides are only a fallback.
        measured = {**measured_from_raw, **measured}
    # else: no raw → use measured{} or public spec (handled by BlockSpec default).

    # ---- Build BlockSpec ----
    spec = BlockSpec(
        system=system, kind=kind,
        units_x=units_x, units_y=units_y,
        **measured,
    )
    resolved = spec.resolved_mm()

    # ---- Deviation from public spec ----
    public = PUBLIC_SPEC[system].copy()
    # For non-brick kinds the public "height" is brick height; resolved
    # carries the kind's height. Compare against brick public for the
    # "are we off the LEGO.com number?" signal.
    deviation: dict[str, float] = {}
    for k_pub, k_resolved in (
        ("unit_mm", "unit"),
        ("knob_diameter_mm", "knob_d"),
        ("knob_height_mm", "knob_h"),
        ("height_mm", "height"),
    ):
        pub = public.get(k_pub)
        actual = resolved.get(k_resolved)
        if pub is not None and actual is not None:
            deviation[k_pub] = round(float(actual) - float(pub), 4)

    # ---- Export GLB ----
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    glb_path = out_dir / f"{part_id}.glb"
    result = export_glb(spec, glb_path)

    # ---- spec.json (audit trail) ----
    spec_json = {
        "part_id": part_id,
        "system": system,
        "kind": kind,
        "units_x": units_x,
        "units_y": units_y,
        "raw_measurements_mm": raw,
        "derived_spec_mm": derive_spec_from_raw(raw) if raw else None,
        "cross_check_warnings": cross_warnings,
        "block_spec": asdict(spec),
        "resolved_mm": resolved,
        "public_spec_mm": public,
        "deviation_from_public_mm": deviation,
        "glb": {
            "path": str(glb_path),
            "vertex_count": result["vertex_count"],
            "face_count": result["face_count"],
            "watertight": result["watertight"],
            "size_bytes": result["size_bytes"],
            "bbox_min": [round(v, 3) for v in result["bbox_min"]],
            "bbox_max": [round(v, 3) for v in result["bbox_max"]],
        },
        "note": meta["note"],
    }
    spec_json_path = out_dir / f"{part_id}.json"
    spec_json_path.write_text(
        json.dumps(spec_json, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # ---- 3-view preview ----
    mesh = generate(spec)
    mesh.metadata["system"] = system
    mesh.metadata["kind"] = kind
    mesh.metadata["units_x"] = units_x
    mesh.metadata["units_y"] = units_y
    preview_png = out_dir / f"{part_id}-preview.png"
    _render_3view(mesh, preview_png)

    # ---- Print summary ----
    print(f"[measure_block] {part_id}")
    if raw is not None:
        print("  raw measurements:")
        print(f"    1A outer_pitch       = {raw['outer_pitch_mm']:.3f} mm")
        print(f"    1B inner_pitch       = {raw['inner_pitch_mm']:.3f} mm")
        print(f"    2  brick_height_net  = {raw['brick_height_net_mm']:.3f} mm")
        print(f"    3  stud_diameter     = {raw['stud_diameter_mm']:.3f} mm")
        print(f"    4  brick_height_total= {raw['brick_height_total_mm']:.3f} mm")
        print("  derived spec:")
        d = derive_spec_from_raw(raw)
        print(f"    unit_mm           = {d['unit_mm']:.3f}  (=(1A+1B)/2)")
        print(f"    knob_diameter_mm  = {d['knob_diameter_mm']:.3f}  (=③)")
        print(f"    height_mm         = {d['height_mm']:.3f}  (=②)")
        print(f"    knob_height_mm    = {d['knob_height_mm']:.3f}  (=④-②)")
        for w in cross_warnings:
            print(f"  ⚠ {w}")
    print(f"  GLB:     {glb_path}")
    print(f"           {result['size_bytes']} B, "
          f"{result['vertex_count']}v / {result['face_count']}f, "
          f"watertight={result['watertight']}")
    print(f"  spec:    {spec_json_path}")
    print(f"  preview: {preview_png}")
    print(f"  resolved: unit={resolved['unit']:.2f}mm  "
          f"height={resolved['height']:.2f}mm  "
          f"knob Ø={resolved['knob_d']:.2f}mm  "
          f"knob h={resolved['knob_h']:.2f}mm")
    if deviation:
        print("  deviation from public spec:")
        for k, d in deviation.items():
            flag = "  ⚠ > 0.2mm" if abs(d) > 0.2 else ""
            print(f"    {k:>20s}: {d:+.3f} mm{flag}")

    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--config", type=Path,
                   help="JSON config (see docstring for schema)")
    p.add_argument("--output-dir", type=Path, required=True,
                   help="Where to drop .glb/.json/-preview.png")

    # Meta
    p.add_argument("--system",  choices=["duplo", "lego", "feile", "generic"])
    p.add_argument("--kind",    choices=["brick", "plate", "tile", "slope"])
    p.add_argument("--units-x", type=int)
    p.add_argument("--units-y", type=int)
    p.add_argument("--part-id", type=str)

    # Raw measurements (recommended — 5 caliper-friendly numbers)
    p.add_argument("--outer-pitch-mm",        type=float,
                   help="1A: brick 总长 (跨两端 stud 圆周), mm")
    p.add_argument("--inner-pitch-mm",        type=float,
                   help="1B: 两 stud 圆周之间空隙, mm")
    p.add_argument("--stud-diameter-mm",      type=float,
                   help="③: 单个 stud 直径, mm")
    p.add_argument("--brick-height-net-mm",   type=float,
                   help="②: 砖块净高 (底面 → 砖顶, 不含凸点), mm")
    p.add_argument("--brick-height-total-mm", type=float,
                   help="④: 砖块总高 (底面 → 凸点顶, 含凸点), mm")

    # Spec override (legacy — pass the 4 BlockSpec fields directly)
    p.add_argument("--unit-mm",           type=float, help="Override unit_mm (节距), mm")
    p.add_argument("--height-mm",         type=float, help="Override height_mm, mm")
    p.add_argument("--knob-diameter-mm",  type=float, help="Override knob_diameter_mm, mm")
    p.add_argument("--knob-height-mm",    type=float, help="Override knob_height_mm, mm")

    args = p.parse_args()
    return cmd_measure(args)


if __name__ == "__main__":
    raise SystemExit(main())
