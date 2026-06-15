#!/usr/bin/env python3
"""Generate 12 realistic-looking brick photos for the Phase 2 E2E test.

Per `docs/design-phase2.md` §5.1, the 8-photo E2E test needs a
``real-bricks/`` fixture directory that holds 8-12 synthetic images of a
recognisable multi-brick shape. Unlike the 4-angle synthetic cube
(``generate.py``), this script:

1. Combines 3 simple trimesh primitives (L-shape, staircase, stacked
   cubes) into one scene. The combined mesh has ~200-500 vertices —
   dense enough that the COLMAP feature-extractor finds real
   correspondences, sparse enough that matplotlib renders it in <1s.
2. Renders the scene from 12 evenly spaced angles (0°, 30°, ..., 330°)
   using matplotlib's 3D projection. This is good enough for the
   ``open3d_pure_photogrammetry`` fallback; the COLMAP SfM pipeline
   will also converge (features are very synthetic — straight edges,
   flat colour faces — so success depends on the worker's intrinsics
   heuristics).
3. Adds mild Gaussian noise + slight blur so the images look "phone
   shot" rather than "perfectly rendered". A real photogrammetry
   pipeline is tolerant to this; the noise is mostly a smoke test that
   the loader doesn't crash on non-ideal JPEGs.

Usage::

    ./.venv/bin/python tests/e2e/fixtures/generate_real_bricks.py
    # or:
    uv run --project . python tests/e2e/fixtures/generate_real_bricks.py

Outputs::

    tests/e2e/fixtures/real-bricks/brick-{00..11}.jpg
    tests/e2e/fixtures/real-bricks/preview.png          # one-angle preview
    tests/e2e/fixtures/real-bricks/preview-grid.png     # 4x3 contact sheet

Tweaking the output
-------------------

* **More/different bricks** — edit ``build_scene()`` below; combine
  more trimesh primitives (cones, cylinders, even another
  ``trimesh.creation.box``). Keep vertex count in the 200-1000 range
  so matplotlib 3D stays snappy.
* **More/fewer angles** — change ``N_ANGLES``. The default 12 covers a
  full 360° turn in 30° steps; values 8-20 all work.
* **Camera height** — change the ``elev=`` argument in
  ``render_angle()``. ``elev=20`` is a "phone held at chest height"
  tilt; ``elev=45`` would be a "phone held lower" angle.
* **Noise / blur strength** — tweak ``noise_std`` and ``blur_px`` in
  ``render_angle()``. Defaults (noise_std=8, blur_px=0.6) produce
  "clean but not sterile" photos. Bump to noise_std=20 for a
  "low-light phone shot" look.
* **Resolution** — change ``WIDTH`` / ``HEIGHT``. 320×240 is the
  same as ``generate.py`` and good enough for the E2E test; bump
  to 640×480 if you want prettier preview PNGs.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Iterable, List, Tuple

import matplotlib

matplotlib.use("Agg")  # headless backend, must be set before pyplot import

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import trimesh  # noqa: E402
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401,E402
from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # noqa: E402

# ---- tunables (also documented in the module docstring) --------------------

N_ANGLES = 12
WIDTH = 320
HEIGHT = 240
NOISE_STD = 8.0  # Gaussian noise sigma in 0-255 units
BLUR_PX = 0.6  # Gaussian blur radius (0 = no blur)

OUT_DIR = Path(__file__).resolve().parent / "real-bricks"

# Colours (RGB, 0-1) for the 3 brick primitives — picked for high
# contrast so SIFT features fire on every face transition.
COLOUR_L = (0.86, 0.30, 0.20)  # red — L-shape
COLOUR_STAIRCASE = (0.20, 0.50, 0.85)  # blue — staircase
COLOUR_CUBE = (0.95, 0.80, 0.20)  # yellow — stacked cube


# ---- scene construction ----------------------------------------------------


def build_scene() -> trimesh.Trimesh:
    """Return one combined trimesh scene for rendering.

    The scene is built in three trimesh primitives, translated into a
    loose cluster, then concatenated. Each primitive is *subdivided*
    once so the combined mesh sits in the 200-500 vertex range called
    out in the brief — SIFT / COLMAP finds more feature matches on a
    denser mesh, but matplotlib's 3D rendering also slows down, so we
    stop at one subdivision.
    """
    # L-shape: two boxes sharing a face, forming a 90° angle.
    a = trimesh.creation.box(extents=(1.0, 0.4, 0.4))
    b = trimesh.creation.box(extents=(0.4, 1.0, 0.4))
    a.apply_translation((0.0, 0.0, 0.0))
    b.apply_translation((0.3, 0.5, 0.0))
    l_shape = trimesh.util.concatenate([a, b])

    # Staircase: three boxes of decreasing size, stacked.
    s0 = trimesh.creation.box(extents=(1.0, 0.3, 0.3))
    s1 = trimesh.creation.box(extents=(0.8, 0.3, 0.6))
    s2 = trimesh.creation.box(extents=(0.6, 0.3, 0.9))
    s0.apply_translation((-1.0, 0.0, 0.0))
    s1.apply_translation((-0.9, 0.0, 0.3))
    s2.apply_translation((-0.8, 0.0, 0.6))
    staircase = trimesh.util.concatenate([s0, s1, s2])

    # Stacked cube: two boxes, one on top of the other.
    c0 = trimesh.creation.box(extents=(0.6, 0.6, 0.6))
    c1 = trimesh.creation.box(extents=(0.5, 0.5, 0.5))
    c0.apply_translation((1.0, 0.0, 0.0))
    c1.apply_translation((1.0, 0.0, 0.7))
    cube_stack = trimesh.util.concatenate([c0, c1])

    # Subdivide each part once. Subdivision on a box is Loop-style edge
    # splitting: 8 vertices → 26, 12 faces → 48. After concatenating 7
    # boxes (L=2, staircase=3, stack=2) and subdividing each, we land
    # in the 150-200 vertex range; the COLMAP feature extractor gets
    # plenty of SIFT keypoints to match. We subdivide the L-shape a
    # second time (50 vertices) to land in the 200-500 vertex range
    # called out in the brief.
    l_shape_sub = l_shape.subdivide().subdivide()
    staircase_sub = staircase.subdivide()
    cube_stack_sub = cube_stack.subdivide()

    scene = trimesh.util.concatenate([l_shape_sub, staircase_sub, cube_stack_sub])
    # Persist the *un*-subdivided parts so colour_faces() can match the
    # subdivided mesh's face ordering. After subdivision, each input
    # face becomes 4 child faces (in the same order), so the per-part
    # face count is multiplied by 4 in the subdivided mesh.
    return scene


def _l_shape_raw() -> trimesh.Trimesh:
    a = trimesh.creation.box(extents=(1.0, 0.4, 0.4))
    b = trimesh.creation.box(extents=(0.4, 1.0, 0.4))
    a.apply_translation((0.0, 0.0, 0.0))
    b.apply_translation((0.3, 0.5, 0.0))
    return trimesh.util.concatenate([a, b])


def _staircase_raw() -> trimesh.Trimesh:
    s0 = trimesh.creation.box(extents=(1.0, 0.3, 0.3))
    s1 = trimesh.creation.box(extents=(0.8, 0.3, 0.6))
    s2 = trimesh.creation.box(extents=(0.6, 0.3, 0.9))
    s0.apply_translation((-1.0, 0.0, 0.0))
    s1.apply_translation((-0.9, 0.0, 0.3))
    s2.apply_translation((-0.8, 0.0, 0.6))
    return trimesh.util.concatenate([s0, s1, s2])


def _cube_stack_raw() -> trimesh.Trimesh:
    c0 = trimesh.creation.box(extents=(0.6, 0.6, 0.6))
    c1 = trimesh.creation.box(extents=(0.5, 0.5, 0.5))
    c0.apply_translation((1.0, 0.0, 0.0))
    c1.apply_translation((1.0, 0.0, 0.7))
    return trimesh.util.concatenate([c0, c1])


def color_faces(scene: trimesh.Trimesh) -> List[Tuple[float, float, float, float]]:
    """Return per-face RGBA colours for the *subdivided* scene.

    After ``trimesh.subdivide()``, each input face becomes 4 child
    faces in order. So if part X has ``n`` raw faces and is subdivided
    ``k`` times, it contributes ``4**k * n`` faces in the subdivided
    mesh — one colour per child. The L-shape is subdivided twice, the
    other two parts are subdivided once.
    Faces are coloured by which primitive they came from. We rely on
    trimesh's per-face order being stable across ``concatenate``.
    """
    raw = [_l_shape_raw(), _staircase_raw(), _cube_stack_raw()]
    subdiv = [2, 1, 1]  # L-shape subdivided twice, others once
    palette = [COLOUR_L, COLOUR_STAIRCASE, COLOUR_CUBE]
    colours: List[Tuple[float, float, float, float]] = []
    for src, k, rgb in zip(raw, subdiv, palette):
        colours.extend([(*rgb, 1.0)] * (len(src.faces) * (4 ** k)))
    assert len(colours) == len(scene.faces), (
        f"colour count {len(colours)} != face count {len(scene.faces)}; "
        "did you change build_scene() / raw() without updating color_faces()?"
    )
    return colours


# ---- rendering -------------------------------------------------------------


def render_angle(
    scene: trimesh.Trimesh,
    colours: List[Tuple[float, float, float, float]],
    azim_deg: float,
    elev_deg: float = 20.0,
) -> np.ndarray:
    """Render the scene to a (H, W, 3) uint8 RGB array.

    Returns the array *after* noise + blur so the caller can save it
    straight to disk.
    """
    fig = plt.figure(figsize=(WIDTH / 80, HEIGHT / 80), dpi=80)
    ax: Axes3D = fig.add_subplot(111, projection="3d")
    face_verts = scene.vertices[scene.faces]
    poly = Poly3DCollection(
        face_verts,
        facecolors=colours,
        edgecolors="black",
        linewidths=0.4,
    )
    ax.add_collection3d(poly)
    # Auto-fit the bounding box. The scene spans ~2.4 units on X.
    ax.set_xlim(-1.4, 1.4)
    ax.set_ylim(-0.6, 1.4)
    ax.set_zlim(-0.3, 1.4)
    ax.view_init(elev=elev_deg, azim=azim_deg)
    ax.set_axis_off()
    fig.tight_layout(pad=0)

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    fig.savefig(tmp_path, dpi=80, bbox_inches="tight", pad_inches=0)
    plt.close(fig)

    # Re-read with PIL and degrade (noise + blur) to look "phone shot".
    from PIL import Image, ImageFilter

    with Image.open(tmp_path) as img:
        if img.mode in ("RGBA", "LA"):
            bg = Image.new("RGB", img.size, (240, 240, 240))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        if BLUR_PX > 0:
            img = img.filter(ImageFilter.GaussianBlur(radius=BLUR_PX))
        arr = np.asarray(img, dtype=np.float32)
    tmp_path.unlink(missing_ok=True)

    if NOISE_STD > 0:
        # Seed with the integer azim index (not the float degree) so
        # numpy's SeedSequence is happy and the noise is deterministic
        # per angle — re-runs produce bit-identical fixtures.
        rng = np.random.default_rng(seed=int(azim_deg) or 1)
        noise = rng.normal(loc=0.0, scale=NOISE_STD, size=arr.shape).astype(np.float32)
        arr = np.clip(arr + noise, 0.0, 255.0)

    return arr.astype(np.uint8)


def save_jpg(arr: np.ndarray, path: Path, quality: int = 90) -> int:
    """Write the array as a JPEG, return the file size in bytes."""
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr, mode="RGB").save(path, format="JPEG", quality=quality, optimize=True)
    return path.stat().st_size


# ---- preview helpers -------------------------------------------------------


def make_contact_sheet(images: Iterable[np.ndarray], out: Path) -> None:
    """Write a 4×3 contact sheet of the generated frames."""
    from PIL import Image

    frames = [Image.fromarray(im, mode="RGB") for im in images]
    cell_w, cell_h = frames[0].size
    sheet = Image.new("RGB", (cell_w * 4, cell_h * 3), (255, 255, 255))
    for idx, frame in enumerate(frames):
        r, c = divmod(idx, 4)
        sheet.paste(frame, (c * cell_w, r * cell_h))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, format="PNG", optimize=True)


# ---- main ------------------------------------------------------------------


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    scene = build_scene()
    colours = color_faces(scene)
    print(
        f"scene: {len(scene.vertices)} vertices, "
        f"{len(scene.faces)} faces, bbox {scene.bounds.tolist()}"
    )

    frames: List[np.ndarray] = []
    written: List[Path] = []
    for i in range(N_ANGLES):
        azim = i * (360 / N_ANGLES)
        arr = render_angle(scene, colours, azim_deg=azim)
        out = OUT_DIR / f"brick-{i:02d}.jpg"
        size = save_jpg(arr, out)
        written.append(out)
        frames.append(arr)
        rel = out.relative_to(OUT_DIR.parent.parent)
        print(f"  wrote {rel} ({size} bytes, azim={azim:5.1f}°)")

    # Preview: first frame at full resolution, and a 4×3 contact sheet.
    preview = OUT_DIR / "preview.png"
    from PIL import Image

    Image.fromarray(frames[0], mode="RGB").save(preview, format="PNG", optimize=True)
    print(f"  wrote {preview.name} ({preview.stat().st_size} bytes)")

    contact = OUT_DIR / "preview-grid.png"
    make_contact_sheet(frames, contact)
    print(f"  wrote {contact.name} ({contact.stat().st_size} bytes)")

    print(f"Generated {len(written)} fixtures under {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
