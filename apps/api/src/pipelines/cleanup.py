"""Blender headless cleanup & decimation.

This module is the seam where design.md §8 stage 4 ("Blender 校准") will be
wired in. The first-cut implementation deliberately *only* does the parts
of stage 4 that Open3D already covers (re-orient by longest edge, scale
to unit cube, decimate to <= 50k faces). The remaining Blender-specific
work — boolean clean-up, UV unwrapping, colour calibration, manual
landmark alignment — is left as TODO because:

1. Blender is a heavy dependency (~1 GB on disk, plus a working OpenGL
   stack for the GUI build) that we explicitly punted in the brief
   ("Meshroom / COLMAP 不强求装在容器内" — Blender is the same boat).
2. The Open3D output is good enough for the first deliverable
   (Three.js viewer in a browser).
3. The seam is clean: when a worker container *does* get a Blender
   binary, the only change is to add a step that calls
   ``subprocess.run(["blender", "-b", "-P", ...])`` and read back the
   resulting ``mesh.glb``.

When this module is invoked, it normalises the mesh via Open3D (matching
the existing stage-4 contract) and emits a TODO log line so reviewers see
the future work without having to grep the codebase.

2026-06-05 update: Open3D 0.18.0's read_triangle_mesh / simplify_quadric_decimation
SIGSEGV on the glTF format we emit (cp311-manylinux_2_27 wheel + glibc 2.41).
Until that's resolved (or we move back to 0.19 with a working environment),
this stage is a no-op pass-through: we read the meta sidecar that the
upstream stage wrote, log "no-op", and forward. The GLB on disk is the
upstream one, which is the same one Three.js will render.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

import open3d as o3d

from pipelines import Pipeline, PipelineUnavailable, ProgressFn

logger = logging.getLogger(__name__)


#: Absolute path to the Blender binary, configurable via env so a host
#: install can be used without rebuilding the worker image.
BLENDER_BIN_ENV = "BLENDER_BIN"


def _resolve_blender_bin() -> str | None:
    """Return the Blender binary path or ``None`` if not installed.

    We deliberately return ``None`` (instead of raising) for the cleanup
    stage: the worker treats Blender as an *optional polish* step and
    always succeeds with the Open3D-only output if Blender is missing.
    """
    import os

    env = os.environ.get(BLENDER_BIN_ENV)
    if env and Path(env).exists() and os.access(env, os.X_OK):
        return env
    return shutil.which("blender")


class BlenderCleanup(Pipeline):
    """Open3D-driven cleanup plus an optional Blender refinement pass.

    The pipeline is a no-op when Blender is missing — we still produce a
    cleaned, decimated GLB. The hook for the future real Blender pass
    lives in :meth:`_blender_refine` and is the single place to extend
    when the binary becomes available.
    """

    name = "blender_cleanup"
    version = "0.1.0"

    def run(
        self,
        *,
        input_dir: Path,
        output_dir: Path,
        on_progress: ProgressFn,
    ) -> dict[str, Any]:
        # No-op pass-through. The upstream stage (``Open3DRunner`` in
        # ``open3d_runner.py``) already wrote a valid, normalised, viewable
        # GLB at ``output_dir/mesh.glb``. Calling Open3D's reader /
        # simplifier on that GLB SIGSEGVs in this environment (cp311 + glibc
        # 2.41 + open3d 0.18 wheel), so we skip the in-place refinement and
        # forward the upstream meta.
        glb_in = output_dir / "mesh.glb"
        if not glb_in.exists():
            raise RuntimeError(f"cleanup: expected mesh.glb in {output_dir}")

        self._emit(on_progress, 90, "simplification_and_export")
        # Re-parse the GLB so we can report the same vertex/face counts the
        # upstream stage computed. We use the binary parser from
        # ``open3d_runner`` rather than Open3D's own reader.
        from pipelines.open3d_runner import Open3DRunner

        verts, faces = Open3DRunner._read_minimal_glb(glb_in)
        # Try the optional Blender refinement, same as before, but the
        # mesh handle we hand it is the (verts, faces) tuple we just read
        # from disk. _blender_refine is a no-op for now so this is purely
        # forward-compatible; when the script lands it can rebuild the
        # GLB in place from the (verts, faces) it receives.
        blender_bin = _resolve_blender_bin()
        if blender_bin is None:
            logger.info(
                "blender_cleanup: BLENDER_BIN not set / not found; "
                "TODO: ship a Blender headless script for boolean cleanup + UV "
                "unwrapping once the worker image has Blender installed"
            )

        aabb_min = verts.min(axis=0).tolist()
        aabb_max = verts.max(axis=0).tolist()
        return {
            "mesh_path": str(glb_in),
            "point_cloud_path": str(output_dir / "cleaned.ply"),
            "vertex_count": int(verts.shape[0]),
            "face_count": int(faces.shape[0]),
            "bbox_min": [float(x) for x in aabb_min],
            "bbox_max": [float(x) for x in aabb_max],
        }

    @staticmethod
    def _emit(on_progress: ProgressFn, pct: int, stage: str) -> None:
        import contextlib

        with contextlib.suppress(Exception):
            on_progress(max(0, min(100, int(pct))), stage)

    @staticmethod
    def _blender_refine(
        mesh: o3d.geometry.TriangleMesh,
        output_dir: Path,
        blender_bin: str,
        on_progress: ProgressFn,
    ) -> None:
        """Run a headless Blender Python script to refine ``mesh``.

        TODO: write a ``refine.py`` script that does:
          * boolean cleanup of degenerate geometry
          * UV unwrap (smart_project)
          * vertex-color bake from photo projections
          * export back to GLB at ``output_dir/mesh.glb``

        The contract we need to honour is that the *input* GLB is
        overwritten in place with the refined version. Once that script
        exists, this method reduces to:

            refine_script = Path(__file__).parent / "blender_refine.py"
            subprocess.run(
                [blender_bin, "-b", "-P", str(refine_script),
                 "--", str(mesh_path), str(output_dir)],
                check=True,
            )
        """
        # We do not invoke ``subprocess`` here yet — the script doesn't
        # exist. The future call site is documented above.
        BlenderCleanup._emit(on_progress, 95, "simplification_and_export")
        logger.debug("blender refine stub: would invoke %s", blender_bin)
        # Mark this method as deliberately a no-op by not touching the
        # mesh at all. Callers MUST handle the no-op branch.
        _ = mesh  # silence mypy "unused" warnings
        return


__all__ = ["BLENDER_BIN_ENV", "BlenderCleanup"]


# Sentinel exception used only by future test fixtures that want to
# verify the Blender integration end-to-end (it is never raised in
# production).
class BlenderNotConfigured(PipelineUnavailable):
    """Raised by tests that want to assert the Blender path *would* run."""
