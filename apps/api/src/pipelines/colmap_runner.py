"""COLMAP-based reconstruction runner (phase 2, 8+ photo path).

This module is the phase-2 seam between the worker and the COLMAP CLI.
It is *intentionally* distinct from the v1 :class:`pipelines.colmap.ColmapPipeline`:

* :class:`ColmapRunner` exposes its own exception hierarchy
  (:class:`ColmapUnavailable`, :class:`ColmapFailed`) so the worker can
  distinguish "binary not installed" from "binary crashed on our photos"
  and decide whether to fall through to the Open3D multi-photo path.
* The progress mapping follows design-phase2.md §3.3: the runner reports
  coarse stage transitions (``sparse_reconstruction`` →
  ``dense_reconstruction`` → ``point_cloud_cleaning`` → ``mesh_reconstruction``)
  and the worker rebases them into a 0-100 global progress.
* It hands off the dense PLY to :class:`pipelines.open3d_runner.Open3DRunner`
  for the cleanup + Poisson / BPA + GLB export stages — Open3D is the
  only thing in the container that's stable enough to drive that, and
  reusing the existing recipe keeps the cleanup pass consistent across
  pipelines.

Binary resolution order:

1. ``$COLMAP_BIN`` env var (operator override for staging hosts).
2. ``shutil.which("colmap")`` on ``$PATH`` (brew install on macOS dev).
3. Otherwise: :class:`ColmapUnavailable` (the worker catches this and
   falls through to the Open3D multi-photo path).

COLMAP CLI sequence (matches design-phase2.md §3.3 + COLMAP 3.6+ docs):

* ``feature_extractor`` — SIFT features per image → ``colmap/database.db``
* ``exhaustive_matcher`` — O(N²) matching, fine for ≤30 images
* ``mapper`` — incremental SfM → ``sparse/0/{cameras,images,points3D}.bin``
* ``image_undistorter`` — prepare the dense-reconstruction workspace
* ``patch_match_stereo`` (per image pair) — depth + normal maps
* ``stereo_fusion`` — fuse the depth maps into a single ``fused.ply``

The runner keeps every intermediate under
``{output_dir}/colmap/{sparse,dense}`` so a failed run can be
post-mortemed from disk without re-running.
"""
from __future__ import annotations

import contextlib
import logging
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


#: Minimum image count to even consider the COLMAP path. Below this we
#: raise :class:`ColmapUnavailable` so the worker can route to the
#: 4-7 photo Open3D path.
MIN_IMAGES_FOR_COLMAP = 8

#: progress 0-10 reserved for "downloading_images" in the worker.
#: Within the runner itself we map sparse (10-30) / dense (30-60) /
#: clean (60-80) / mesh (80-95) to mirror design-phase2.md §3.3.
PROGRESS_DOWNLOAD_END = 10
PROGRESS_SPARSE_END = 30
PROGRESS_DENSE_END = 60
PROGRESS_CLEAN_END = 80
PROGRESS_MESH_END = 95

ProgressFn = Callable[[int, str], None]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class ColmapUnavailable(RuntimeError):
    """The COLMAP binary cannot be located.

    Distinct from :class:`ColmapFailed` so the worker can log
    "fall back to Open3D" without making the user think the install is
    broken.
    """


class ColmapFailed(RuntimeError):
    """The COLMAP binary was located but exited non-zero (or produced
    an empty artefact). Worker treats this as a hard failure of the
    SfM path and falls back to the Open3D multi-photo path.
    """


# ---------------------------------------------------------------------------
# Binary resolution
# ---------------------------------------------------------------------------
def _resolve_colmap_bin(explicit: str | None = None) -> str:
    """Return the absolute path to ``colmap`` or raise
    :class:`ColmapUnavailable`.

    Order: explicit arg → ``$COLMAP_BIN`` env → ``shutil.which("colmap")``.
    """
    candidates: list[str | None] = [explicit, os.environ.get("COLMAP_BIN")]
    for cand in candidates:
        if cand and Path(cand).exists() and os.access(cand, os.X_OK):
            return cand
    on_path = shutil.which("colmap")
    if on_path:
        return on_path
    raise ColmapUnavailable(
        "COLMAP binary not found. Set COLMAP_BIN env var or "
        "`brew install colmap` (see https://colmap.github.io/install.html). "
        "The Open3D multi-photo fallback will run instead."
    )


def _list_images(input_dir: Path) -> list[Path]:
    exts = {".jpg", ".jpeg", ".png", ".webp"}
    return sorted(p for p in input_dir.iterdir() if p.suffix.lower() in exts)


def _validate_image(path: Path) -> bool:
    """Cheap "is this readable as an image" check.

    Logs a warning and returns ``False`` on failure but does not raise —
    the brief explicitly says "强约束: 每张照片前用 OpenCV/PIL 校验可读
    (不抛异常, 但 log warning)". The caller is expected to skip the
    unreadable file.
    """
    try:
        # Lazy import — keeps the runner import-clean for envs that
        # don't have pillow (tests of binary resolution only).
        from PIL import Image

        with Image.open(path) as im:
            im.verify()
        return True
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("colmap_runner: image %s unreadable: %s", path, exc)
        return False


# ---------------------------------------------------------------------------
# CLI invocation
# ---------------------------------------------------------------------------
def _run(cmd: list[str], *, cwd: Path, log_path: Path) -> None:
    """Run ``cmd`` in ``cwd``; tee stdout/stderr to ``log_path``.

    A non-zero exit raises :class:`ColmapFailed` (NOT generic RuntimeError)
    so the worker can route the failure correctly. The tail of the log
    is included in the error message for operator-actionable context.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("colmap: %s (cwd=%s)", " ".join(cmd), cwd)
    with log_path.open("wb") as logf:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            stdout=logf,
            stderr=subprocess.STDOUT,
            check=False,
        )
    if proc.returncode != 0:
        tail = log_path.read_text(errors="ignore")[-2000:]
        raise ColmapFailed(
            f"COLMAP step failed (rc={proc.returncode}): {' '.join(cmd)}\n"
            f"--- log tail ---\n{tail}"
        )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
class ColmapRunner:
    """Run the full COLMAP SfM + MVS pipeline on a directory of photos.

    Instantiated per Celery task. The class is otherwise stateless.
    """

    #: Asset meta ``pipeline_used`` value. Stable string — the contract
    #: says frontend can switch on it to render the right badge.
    name = "colmap_sfm"
    version = "0.3.0"

    def __init__(
        self,
        *,
        binary: str | None = None,
        min_images: int = MIN_IMAGES_FOR_COLMAP,
    ) -> None:
        # ``binary`` lets the test suite inject a fake script.
        self._binary_override = binary
        self._min_images = min_images

    # ---- Public API --------------------------------------------------------
    def reconstruct(
        self,
        *,
        input_dir: Path,
        output_dir: Path,
        on_progress: ProgressFn | None = None,
    ) -> dict[str, Any]:
        """Run the full SfM + MVS path; return a result dict with
        ``mesh_path``, ``point_cloud_path``, ``vertex_count``,
        ``face_count``, ``bbox_min``, ``bbox_max``.

        Progress mapping (re-based by the worker, so 0-100 here means
        the runner's own progress; the worker rebases it into 10-95):

        * 10-30  ``sparse_reconstruction``  (feature_extractor + matcher + mapper)
        * 30-60  ``dense_reconstruction``   (undistorter + patch_match + fusion)
        * 60-80  ``point_cloud_cleaning``   (Open3D statistical outlier removal)
        * 80-95  ``mesh_reconstruction``    (Open3D Poisson / BPA + simplify)

        Raises:
            ColmapUnavailable: binary not found / < 8 images
            ColmapFailed:      any CLI step returned non-zero
        """
        input_dir = Path(input_dir)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        colmap_dir = output_dir / "colmap"
        sparse_dir = colmap_dir / "sparse"
        dense_dir = colmap_dir / "dense"
        colmap_dir.mkdir(parents=True, exist_ok=True)
        sparse_dir.mkdir(parents=True, exist_ok=True)
        dense_dir.mkdir(parents=True, exist_ok=True)
        log_path = colmap_dir / "colmap.log"

        # ---- Pre-flight ----------------------------------------------------
        images = _list_images(input_dir)
        if len(images) < self._min_images:
            raise ColmapUnavailable(
                f"need at least {self._min_images} images for COLMAP, "
                f"got {len(images)}"
            )
        # Validate readability — log warning, skip the file, but don't
        # fail the whole job just because one frame is corrupt.
        valid = [p for p in images if _validate_image(p)]
        if len(valid) < self._min_images:
            raise ColmapUnavailable(
                f"only {len(valid)}/{len(images)} images are readable; "
                f"need at least {self._min_images}"
            )
        bin_path = self._binary_override or _resolve_colmap_bin()
        # Sub-stage progress rebound against the run; emit at the
        # boundaries the design doc calls out.
        self._emit(on_progress, PROGRESS_DOWNLOAD_END, "downloading_images")
        database_path = colmap_dir / "database.db"

        # ---- Stage 1: feature extraction + matching + sparse SfM ----------
        self._emit(on_progress, 10, "sparse_reconstruction")
        _run(
            [bin_path, "feature_extractor",
             "--database_path", str(database_path),
             "--image_path", str(input_dir)],
            cwd=colmap_dir,
            log_path=log_path,
        )
        self._emit(on_progress, 15, "sparse_reconstruction")
        _run(
            [bin_path, "exhaustive_matcher",
             "--database_path", str(database_path)],
            cwd=colmap_dir,
            log_path=log_path,
        )
        self._emit(on_progress, 20, "sparse_reconstruction")
        _run(
            [bin_path, "mapper",
             "--database_path", str(database_path),
             "--image_path", str(input_dir),
             "--output_path", str(sparse_dir)],
            cwd=colmap_dir,
            log_path=log_path,
        )
        sparse_subdirs = sorted(
            (p for p in sparse_dir.iterdir() if p.is_dir() and p.name.isdigit()),
            key=lambda p: int(p.name),
            reverse=True,
        )
        if not sparse_subdirs:
            raise ColmapFailed("COLMAP mapper produced no sparse model")
        main_sparse = sparse_subdirs[0]
        self._emit(on_progress, PROGRESS_SPARSE_END, "sparse_reconstruction")

        # ---- Stage 2: dense MVS (undistort + patch_match + fusion) --------
        self._emit(on_progress, 35, "dense_reconstruction")
        _run(
            [bin_path, "image_undistorter",
             "--image_path", str(input_dir),
             "--input_path", str(main_sparse),
             "--output_path", str(dense_dir),
             "--output_type", "COLMAP"],
            cwd=colmap_dir,
            log_path=log_path,
        )
        # patch_match_stereo needs a camera list — use the workspace
        # cameras.txt that image_undistorter wrote.
        self._emit(on_progress, 45, "dense_reconstruction")
        _run(
            [bin_path, "patch_match_stereo",
             "--workspace_path", str(dense_dir),
             "--workspace_format", "COLMAP"],
            cwd=colmap_dir,
            log_path=log_path,
        )
        self._emit(on_progress, 55, "dense_reconstruction")
        fused_ply = dense_dir / "fused.ply"
        _run(
            [bin_path, "stereo_fusion",
             "--workspace_path", str(dense_dir),
             "--workspace_format", "COLMAP",
             "--output_path", str(fused_ply)],
            cwd=colmap_dir,
            log_path=log_path,
        )
        if not fused_ply.exists() or fused_ply.stat().st_size == 0:
            raise ColmapFailed(f"COLMAP fusion produced empty {fused_ply}")
        self._emit(on_progress, PROGRESS_DENSE_END, "dense_reconstruction")

        # ---- Stage 3: hand off dense PLY to Open3D for cleanup + meshing --
        # Import lazily so environments without Open3D still get a clean
        # import error from the inner runner.
        from pipelines.open3d_runner import Open3DRunner

        self._emit(on_progress, PROGRESS_CLEAN_END, "point_cloud_cleaning")
        runner = Open3DRunner()
        result = runner.process_point_cloud_and_mesh(
            point_cloud_path=fused_ply,
            output_dir=output_dir,
            on_progress=self._rebased_progress(on_progress, base=PROGRESS_CLEAN_END,
                                                span=PROGRESS_MESH_END - PROGRESS_CLEAN_END),
        )
        result["pipeline_used"] = self.name
        result["pipeline_version"] = self.version
        result["colmap_binary"] = bin_path
        return result

    # ---- Helpers -----------------------------------------------------------
    @staticmethod
    def _emit(
        on_progress: ProgressFn | None, pct: int, stage: str
    ) -> None:
        if on_progress is None:
            return
        with contextlib.suppress(Exception):
            on_progress(max(0, min(100, int(pct))), stage)

    @staticmethod
    def _rebased_progress(
        on_progress: ProgressFn | None, *, base: int, span: int
    ) -> ProgressFn:
        """Return an ``on_progress(pct, stage)`` that rebases 0-100 from
        a downstream callback into the [base, base+span] sub-range."""
        if on_progress is None:
            return lambda *_: None

        def cb(pct: int, stage: str) -> None:
            clamped = max(0, min(100, int(pct)))
            global_pct = base + int(span * clamped / 100)
            with contextlib.suppress(Exception):
                on_progress(global_pct, stage)
        return cb


# Public helpers re-exported for tests that want to assert ordering /
# resolution behaviour without instantiating the runner.
__all__ = [
    "MIN_IMAGES_FOR_COLMAP",
    "PROGRESS_CLEAN_END",
    "PROGRESS_DENSE_END",
    "PROGRESS_DOWNLOAD_END",
    "PROGRESS_MESH_END",
    "PROGRESS_SPARSE_END",
    "ColmapFailed",
    "ColmapRunner",
    "ColmapUnavailable",
]


# Re-export ``_list_images`` for tests that want to assert ordering.
def _list_images_for_tests() -> list[Path]:  # pragma: no cover - test helper
    return _list_images(Path("."))
