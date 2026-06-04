"""Test fixtures for the 3D reconstruction pipeline.

The brief asks for "4 张从不同角度拍的合成图" — synthetic images of a
known geometry, taken from 4 different camera angles. trimesh gives us
the geometry; matplotlib's Agg backend (already a transitive dep of
open3d) gives us the rasterisation. We end up with 4 real PNGs that
look like a multi-view capture session.

Why synthesize rather than commit pre-made PNGs? Because:

1. A synthetic generator is deterministic — same output every run, no
   "I forgot to commit the file" mystery.
2. The geometry can be tweaked to test specific edge cases (flat
   normals, hollow objects, etc.) without shipping more binaries.
3. It exercises the *actual* photogrammetry code path because the
   worker has to treat the inputs as real images.

Why matplotlib and not Open3D's OffscreenRenderer? Open3D 0.19's
Filament-based renderer needs a working EGL/headless display server,
which is not available in CI containers or macOS dev boxes without
extra setup. matplotlib's Agg backend is pure CPU and ships in the
open3d transitive dep tree already.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless; must run before pyplot import
import matplotlib.pyplot as plt
import numpy as np
import pytest
import trimesh
from mpl_toolkits.mplot3d import Axes3D
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

# Default image dimensions — 256x256 is enough to drive a real SfM
# pipeline through COLMAP while staying fast for the Open3D-only
# fallback. The brief allows tests to use any image count >= 4, so we
# keep 4 as the default to match the minimum enforced by the API.
DEFAULT_VIEWS: int = 4
DEFAULT_WIDTH: int = 256
DEFAULT_HEIGHT: int = 256
DEFAULT_OBJECT: str = "cube"


def _make_object(name: str = DEFAULT_OBJECT) -> trimesh.Trimesh:
    """Return a small, well-conditioned mesh for synthetic rendering.

    A unit cube is the simplest possible object — clearly distinguishable
    from any direction, no transparency, no thin structures. Real tests
    that want harder cases can override the fixture.
    """
    if name == "cube":
        return trimesh.creation.box(extents=(1.0, 1.0, 1.0))
    if name == "sphere":
        return trimesh.creation.icosphere(subdivisions=2)
    raise ValueError(f"unknown synthetic object: {name!r}")


def _camera_angles(n_views: int) -> list[tuple[float, float]]:
    """Return ``(azimuth_deg, elevation_deg)`` for ``n_views`` cameras.

    Layout: ``n_views`` equally spaced on a ring around the Y axis at
    30° elevation. Picking the *front* of the object at azimuth=0 so
    the tests are reproducible.
    """
    elevation = 30.0
    return [(360.0 * i / n_views, elevation) for i in range(n_views)]


def _render_view(
    mesh: trimesh.Trimesh,
    *,
    azimuth_deg: float,
    elevation_deg: float,
    width: int,
    height: int,
) -> Path:
    """Render ``mesh`` from one camera position. Saves to a temp file.

    We use matplotlib's Poly3DCollection because it's the simplest
    approach that gives a recognisable silhouette from every angle.
    Returns the path of the saved PNG so callers don't have to think
    about file naming.
    """
    fig = plt.figure(figsize=(width / 80, height / 80), dpi=80)
    ax: Axes3D = fig.add_subplot(111, projection="3d")
    face_verts = mesh.vertices[mesh.faces]
    poly = Poly3DCollection(
        face_verts,
        facecolors=(0.95, 0.55, 0.20, 1.0),  # orange — easy to eyeball
        edgecolors="black",
        linewidths=0.4,
    )
    ax.add_collection3d(poly)
    ax.set_xlim(-1, 1)
    ax.set_ylim(-1, 1)
    ax.set_zlim(-1, 1)
    # matplotlib's view_init takes elev (above XZ plane) + azim (around Z).
    # We want 30° elevation and the requested azimuth.
    ax.view_init(elev=elevation_deg, azim=azimuth_deg)
    ax.set_axis_off()
    fig.tight_layout(pad=0)
    out = Path(tempfile_for_render(width, height, azimuth_deg, elevation_deg))
    fig.savefig(out, dpi=80, bbox_inches="tight", pad_inches=0)
    plt.close(fig)
    return out


def _save_png_directly(arr: np.ndarray, path: Path) -> None:
    """Write an (H, W, 3) uint8 numpy array to a PNG file. Kept for
    the rare test that wants a flat-colour image without 3D shading."""
    import PIL.Image

    PIL.Image.fromarray(arr.astype(np.uint8)).save(path, format="PNG")


# Helper — render into a deterministic path. We use a module-level temp
# dir so pytest's tmp_path machinery can still clean up the *test* outputs.
def tempfile_for_render(width: int, height: int, az: float, el: float) -> str:
    """Return a deterministic path under the system temp dir.

    Each (w, h, az, el) tuple gets its own file. The directory is
    named so it's obvious in ``ls /tmp`` what's going on.
    """
    import tempfile

    d = Path(tempfile.gettempdir()) / "blocktool_synth_renders"
    d.mkdir(parents=True, exist_ok=True)
    return str(d / f"w{width}_h{height}_az{az:.0f}_el{el:.0f}.png")


def render_synthetic_capture(
    target_dir: Path,
    *,
    n_views: int = DEFAULT_VIEWS,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    object_name: str = DEFAULT_OBJECT,
) -> list[Path]:
    """Render ``n_views`` synthetic photos of the named object into ``target_dir``.

    Returns the list of image paths, in capture order. The filenames are
    zero-padded (``000.png``, ``001.png``) so the worker's natural sort
    yields a deterministic order.
    """
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    mesh = _make_object(object_name)
    paths: list[Path] = []
    for i, (az, el) in enumerate(_camera_angles(n_views)):
        src = _render_view(
            mesh, azimuth_deg=az, elevation_deg=el, width=width, height=height
        )
        dst = target_dir / f"{i:03d}.png"
        # Use shutil.copy so we own the file (the source lives in a
        # shared temp dir, the destination is per-test).
        import shutil

        shutil.copyfile(src, dst)
        paths.append(dst)
    return paths


# ---- Pytest fixtures -------------------------------------------------------
@pytest.fixture(scope="session")
def synth_cube_dir(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Session-scoped fixture: 4 PNGs of a unit cube in ``<tmp>/synth_cube``.

    One render per test session keeps the suite fast; the brief allows
    reusing the same fixture for every test in this module.
    """
    target = tmp_path_factory.mktemp("synth_cube")
    render_synthetic_capture(target, n_views=DEFAULT_VIEWS)
    yield target


@pytest.fixture
def synth_cube_dir_fresh(tmp_path: Path) -> Iterator[Path]:
    """Function-scoped variant — gives each test its own render.

    Use this when a test mutates the directory (deletes files, runs
    the pipeline, etc.). Most tests can use the cheaper session-scoped
    ``synth_cube_dir`` instead.
    """
    target = tmp_path / "synth_cube"
    render_synthetic_capture(target, n_views=DEFAULT_VIEWS)
    yield target
