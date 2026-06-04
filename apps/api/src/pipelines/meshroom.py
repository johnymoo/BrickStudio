"""Meshroom (AliceVision) reconstruction pipeline.

Meshroom is the "all-in-one" AliceVision pipeline: it covers feature
extraction, matching, SfM, MVS, meshing and texturing in a single
``aliceVision_run`` invocation. We support it because the design.md §4 list
includes Meshroom, but — per the task brief — we don't require it to be
installed in the worker container: missing binary ⇒ :class:`PipelineUnavailable`
and the worker falls back to the Open3D path.

Usage in production is identical to :class:`pipelines.colmap.ColmapPipeline`.
The worker tries Meshroom → COLMAP → Open3D in that order.
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


def _resolve_meshroom_bin() -> str:
    env = os.environ.get("MESHROOM_BIN")
    if env and Path(env).exists() and os.access(env, os.X_OK):
        return env
    for cand in ("meshroom", "meshroom_photogrammetry", "aliceVision_run"):
        path = shutil.which(cand)
        if path:
            return path
    raise PipelineUnavailable(
        "Meshroom binary not found. Set MESHROOM_BIN or install Meshroom from "
        "https://alicevision.org/. The Open3D-only fallback will run instead."
    )


def _run(cmd: list[str], *, cwd: Path, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("meshroom: %s (cwd=%s)", " ".join(cmd), cwd)
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
            f"Meshroom step failed (rc={proc.returncode}): {' '.join(cmd)}\n--- tail ---\n{tail}"
        )


class MeshroomPipeline(Pipeline):
    """Thin wrapper around ``aliceVision_run`` / ``meshroom`` headless mode.

    Meshroom writes its output to a configurable ``--output`` directory with
    a fixed sub-tree (``texturing/...``, ``meshing/...``). We translate that
    to the same ``mesh.glb`` / ``point_cloud.ply`` contract the worker
    expects.

    NOTE: First-cut implementation. The exact CLI flags depend on the
    Meshroom version, so we keep the call site in a private helper that
    callers can override for unusual installs.
    """

    name = "meshroom"
    version = "0.1.0"

    def __init__(self, *, binary: str | None = None) -> None:
        self._binary_override = binary

    def run(
        self,
        *,
        input_dir: Path,
        output_dir: Path,
        on_progress: ProgressFn,
    ) -> dict[str, Any]:
        self._ensure_dir(output_dir)
        meshroom_dir = self._ensure_dir(output_dir / "meshroom")
        log_path = meshroom_dir / "meshroom.log"

        bin_path = self._binary_override or _resolve_meshroom_bin()

        # Meshroom's CLI is heavy: thousands of node-level flags. For the
        # first-cut wrapper we use the "photogrammetry" preset, which is the
        # canonical entry point. If a future Meshroom version breaks the
        # surface, this is the line that needs updating.
        self._emit(on_progress, 15, "sparse_reconstruction")
        _run(
            [bin_path,
             "--input", str(input_dir),
             "--output", str(meshroom_dir),
             "--pipeline", "photogrammetry"],
            cwd=meshroom_dir,
            log_path=log_path,
        )
        self._emit(on_progress, 70, "point_cloud_cleaning")
        # Meshroom produces an OBJ in ``texturing/``. The conversion to GLB
        # is a job for Open3D — same path as the COLMAP handoff.
        texturing_obj = meshroom_dir / "texturing" / "texturedMesh.obj"
        if not texturing_obj.exists():
            raise RuntimeError(
                f"Meshroom did not produce expected texturing output at {texturing_obj}"
            )

        from pipelines.open3d_runner import Open3DRunner

        runner = Open3DRunner()
        result = runner.process_obj_and_mesh(
            obj_path=texturing_obj,
            output_dir=output_dir,
            on_progress=on_progress,
        )
        result["pipeline_used"] = self.name
        result["pipeline_version"] = self.version
        result["meshroom_binary"] = bin_path
        return result


__all__ = ["MeshroomPipeline"]
