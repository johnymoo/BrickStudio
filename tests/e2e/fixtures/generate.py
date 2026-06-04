#!/usr/bin/env python3
"""Generate 4 synthetic cube PNGs for E2E tests.

The brief asks for ``tests/e2e/fixtures/cube-front.jpg`` (and the other
3 angles). We use the same trimesh + matplotlib path the backend's
``synth_cube`` fixture uses, but write to the E2E directory as JPEG
files (so the test data is committed once, not regenerated on every
Playwright run — saves ~3 seconds per run).

Usage:
    uv run --project apps/api python tests/e2e/fixtures/generate.py

Re-run this only if you change the test images (e.g. you want a sphere
instead of a cube). The generated files are committed; tests don't
regenerate them at runtime.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

# Make sure we use the headless backend before pyplot is imported.
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import trimesh  # noqa: E402
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401,E402
from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # noqa: E402

# Match the back-end's MIN_IMAGES=4 contract.
N_VIEWS = 4
WIDTH = 320
HEIGHT = 240

# Camera angles (azimuth in degrees, 0 = front of the object).
# We pick 4 angles that look "intentional" so the resulting 3D model
# is recognisable as a cube.
ANGLES = [
    ("front", 0),
    ("side", 90),
    ("back", 180),
    ("angle", 315),
]

OUT_DIR = Path(__file__).resolve().parent
OUT_DIR.mkdir(parents=True, exist_ok=True)


def render(angle_deg: float) -> Path:
    """Render a single 320x240 PNG of a unit cube from ``angle_deg``."""
    mesh = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
    fig = plt.figure(figsize=(WIDTH / 80, HEIGHT / 80), dpi=80)
    ax: Axes3D = fig.add_subplot(111, projection="3d")
    face_verts = mesh.vertices[mesh.faces]
    poly = Poly3DCollection(
        face_verts,
        facecolors=(0.95, 0.55, 0.20, 1.0),  # orange — easy to spot in viewer
        edgecolors="black",
        linewidths=0.5,
    )
    ax.add_collection3d(poly)
    ax.set_xlim(-1, 1)
    ax.set_ylim(-1, 1)
    ax.set_zlim(-1, 1)
    ax.view_init(elev=20, azim=angle_deg)
    ax.set_axis_off()
    fig.tight_layout(pad=0)
    # Save to a temp PNG, then convert to JPEG (the test files are
    # called ``.jpg`` per the brief; matplotlib's jpg backend works
    # but PIL gives us explicit control over quality).
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    fig.savefig(tmp_path, dpi=80, bbox_inches="tight", pad_inches=0)
    plt.close(fig)
    return tmp_path


def png_to_jpg(png_path: Path, jpg_path: Path, quality: int = 90) -> None:
    import PIL.Image

    with PIL.Image.open(png_path) as img:
        # Ensure RGB (some matplotlib outputs have an alpha channel).
        if img.mode in ("RGBA", "LA"):
            bg = PIL.Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        img.save(jpg_path, format="JPEG", quality=quality, optimize=True)


def main() -> int:
    written: list[Path] = []
    for name, angle in ANGLES:
        png = render(angle)
        jpg = OUT_DIR / f"cube-{name}.jpg"
        png_to_jpg(png, jpg)
        png.unlink(missing_ok=True)
        written.append(jpg)
        print(f"  wrote {jpg.relative_to(OUT_DIR.parent.parent)} ({jpg.stat().st_size} bytes)")
    # Also write a copy as the first/canonical fixture name the brief uses.
    canonical = OUT_DIR / "cube-front.jpg"
    if not canonical.exists() and written:
        shutil.copyfile(written[0], canonical)
    print(f"Generated {len(written)} fixtures under {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
