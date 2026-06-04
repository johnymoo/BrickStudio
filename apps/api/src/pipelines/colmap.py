"""COLMAP-based reconstruction pipeline.

This module shells out to the COLMAP CLI binary. The binary path is resolved
in this order:

1. ``COLMAP_BIN`` environment variable (e.g. ``/usr/local/bin/colmap``).
2. ``colmap`` resolved via :func:`shutil.which` on ``$PATH``.
3. Otherwise: raise :class:`PipelineUnavailable` so the worker can fall back
   to the Open3D-only path.

Why CLI and not the Python bindings? The Python bindings (``pycolmap``) are
a separate optional install and pin to specific COLMAP versions; shelling
out works against any binary >= 3.6 and is what the official docs use.

COLMAP stages invoked (matches design.md §8):

* ``feature_extractor`` — SIFT features.
* ``exhaustive_matcher`` — O(N²) matching, fine for ≤30 images.
* ``mapper`` — incremental SfM, produces the sparse model.
* ``image_undistorter`` — prepares the dense-reconstruction workspace.
* ``stereo_fusion`` — fuses multi-view stereo into the dense point cloud.

We keep every intermediate under ``output_dir/colmap/{sparse,dense}`` so a
failed retry can resume without redoing work.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from pipelines import Pipeline, PipelineUnavailable, ProgressFn

logger = logging.getLogger(__name__)

# Minimum image count — COLMAP needs a meaningful number of views to recover
# camera poses reliably. Below this we don't even try COLMAP and let the
# worker fall through to the Open3D path.
MIN_IMAGES_FOR_COLMAP = 8


def _resolve_colmap_bin() -> str:
    """Return the absolute path to ``colmap`` or raise :class:`PipelineUnavailable`."""
    env = os.environ.get("COLMAP_BIN")
    if env and Path(env).exists() and os.access(env, os.X_OK):
        return env
    on_path = shutil.which("colmap")
    if on_path:
        return on_path
    raise PipelineUnavailable(
        "COLMAP binary not found. Set COLMAP_BIN env var or `brew install colmap` "
        "(see https://colmap.github.io/install.html). The Open3D-only fallback "
        "will run instead."
    )


def _run(cmd: list[str], *, cwd: Path, log_path: Path) -> None:
    """Run ``cmd`` in ``cwd``; tee stdout/stderr to ``log_path``.

    We always log the full command + output so a failed run is debuggable
    from the job row's error field + the on-disk log. The worker keeps the
    input dir around on failure (see ``workers/tasks/reconstruct.py``).
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
        raise RuntimeError(
            f"COLMAP step failed (rc={proc.returncode}): {' '.join(cmd)}\n--- tail ---\n{tail}"
        )


def _list_images(input_dir: Path) -> list[Path]:
    exts = {".jpg", ".jpeg", ".png", ".webp"}
    return sorted(p for p in input_dir.iterdir() if p.suffix.lower() in exts)


class ColmapPipeline(Pipeline):
    """Run the full COLMAP SfM+MVS pipeline on a directory of photos.

    The class is intentionally stateless — instantiate it once per Celery
    task; everything heavy is per-call.
    """

    name = "colmap"
    version = "0.2.0"

    def __init__(self, *, binary: str | None = None) -> None:
        # ``binary`` lets the test suite inject a fake script (e.g. one that
        # synthesizes the expected output files) without touching the env.
        # In production we read COLMAP_BIN at call time so a config change
        # between task launches is picked up.
        self._binary_override = binary

    def run(
        self,
        *,
        input_dir: Path,
        output_dir: Path,
        on_progress: ProgressFn,
    ) -> dict[str, Any]:
        self._ensure_dir(output_dir)
        colmap_dir = self._ensure_dir(output_dir / "colmap")
        sparse_dir = self._ensure_dir(colmap_dir / "sparse")
        dense_dir = self._ensure_dir(colmap_dir / "dense")
        log_path = colmap_dir / "colmap.log"

        bin_path = self._binary_override or _resolve_colmap_bin()
        images = _list_images(input_dir)
        if len(images) < MIN_IMAGES_FOR_COLMAP:
            raise PipelineUnavailable(
                f"need at least {MIN_IMAGES_FOR_COLMAP} images for COLMAP, got {len(images)}"
            )

        database_path = colmap_dir / "database.db"

        # ---- Stage 1: feature extraction ----------------------------------
        self._emit(on_progress, 10, "sparse_reconstruction")
        _run(
            [bin_path, "feature_extractor",
             "--database_path", str(database_path),
             "--image_path", str(input_dir)],
            cwd=colmap_dir,
            log_path=log_path,
        )

        # ---- Stage 2: exhaustive matching ---------------------------------
        self._emit(on_progress, 20, "sparse_reconstruction")
        _run(
            [bin_path, "exhaustive_matcher",
             "--database_path", str(database_path)],
            cwd=colmap_dir,
            log_path=log_path,
        )

        # ---- Stage 3: incremental mapper (sparse SfM) ---------------------
        self._emit(on_progress, 35, "sparse_reconstruction")
        _run(
            [bin_path, "mapper",
             "--database_path", str(database_path),
             "--image_path", str(input_dir),
             "--output_path", str(sparse_dir)],
            cwd=colmap_dir,
            log_path=log_path,
        )

        # Identify the largest sparse reconstruction; mapper writes
        # 0/, 1/, ... subdirs depending on how many partial models survived.
        sparse_subdirs = sorted(
            (p for p in sparse_dir.iterdir() if p.is_dir() and p.name.isdigit()),
            key=lambda p: int(p.name),
            reverse=True,
        )
        if not sparse_subdirs:
            raise RuntimeError("COLMAP mapper produced no sparse model")
        main_sparse = sparse_subdirs[0]

        # ---- Stage 4: undistort for dense MVS -----------------------------
        self._emit(on_progress, 45, "dense_reconstruction")
        _run(
            [bin_path, "image_undistorter",
             "--image_path", str(input_dir),
             "--input_path", str(main_sparse),
             "--output_path", str(dense_dir),
             "--output_type", "COLMAP"],
            cwd=colmap_dir,
            log_path=log_path,
        )

        # ---- Stage 5: stereo fusion → fused.ply ----------------------------
        self._emit(on_progress, 60, "dense_reconstruction")
        fused_ply = dense_dir / "fused.ply"
        _run(
            [bin_path, "stereo_fusion",
             "--workspace_path", str(dense_dir),
             "--output_path", str(fused_ply)],
            cwd=colmap_dir,
            log_path=log_path,
        )
        if not fused_ply.exists() or fused_ply.stat().st_size == 0:
            raise RuntimeError(f"COLMAP fusion produced empty {fused_ply}")

        # Hand off the fused point cloud to Open3D for cleanup + meshing
        # (that's a separate module so the worker can run the Open3D path
        # standalone when COLMAP is unavailable). We import lazily to keep
        # COLMAP-only environments import-clean.
        from pipelines.open3d_runner import Open3DRunner

        self._emit(on_progress, 70, "point_cloud_cleaning")
        runner = Open3DRunner()
        result = runner.process_point_cloud_and_mesh(
            point_cloud_path=fused_ply,
            output_dir=output_dir,
            on_progress=on_progress,
        )
        result["pipeline_used"] = self.name
        result["pipeline_version"] = self.version
        result["colmap_binary"] = bin_path
        return result


__all__ = ["MIN_IMAGES_FOR_COLMAP", "ColmapPipeline"]


# Re-export ``_list_images`` for tests that want to assert ordering.
def _list_images_for_tests() -> list[Path]:
    return _list_images(Path("."))  # pragma: no cover - test helper
