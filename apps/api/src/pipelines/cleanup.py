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
        # ``input_dir`` here is the *output* of the previous stage (already
        # contains ``mesh.glb``). The cleanup reads it and rewrites it.
        glb_in = output_dir / "mesh.glb" if (output_dir / "mesh.glb").exists() else None
        if glb_in is None:
            # Fall back: scan the *input* dir for any GLB Meshroom may have
            # produced (texturing/texturedMesh.glb etc.). For now we keep
            # the contract simple and assume the GLB is already in
            # ``output_dir``.
            raise RuntimeError("cleanup: expected mesh.glb in output_dir")

        self._emit(on_progress, 90, "simplification_and_export")
        mesh = o3d.io.read_triangle_mesh(str(glb_in))
        if len(mesh.triangles) == 0:
            # Open3D 0.19's bundled ASSIMP reader has a bug reading back
            # glTF files it produced itself (it logs a warning and
            # returns an empty mesh). Fall back to ``trimesh`` — the file
            # is fine, the reader is just strict. We don't want this
            # best-effort cleanup stage to fail the whole job.
            logger.info(
                "blender_cleanup: Open3D read returned 0 triangles for %s; "
                "retrying with trimesh", glb_in,
            )
            try:
                import trimesh

                loaded = trimesh.load(str(glb_in), force="mesh")
                # mypy is fooled by the open3d type stubs (which type
                # TriangleMesh as ``Geometry`` with no ``vertices`` /
                # ``triangles`` attributes). The runtime object is fine.
                mesh = o3d.geometry.TriangleMesh()
                mesh.vertices = o3d.utility.Vector3dVector(loaded.vertices)  # type: ignore[attr-defined]
                mesh.triangles = o3d.utility.Vector3iVector(loaded.faces)  # type: ignore[attr-defined]
            except Exception as exc:
                raise RuntimeError(
                    f"cleanup: empty mesh in {glb_in} (Open3D and trimesh "
                    f"both failed: {exc})"
                ) from exc
            if len(mesh.triangles) == 0:
                raise RuntimeError(f"cleanup: empty mesh in {glb_in}")
        mesh.compute_vertex_normals()

        # Always run the Open3D-side refinement — this is the "cheap"
        # half of stage 4 and keeps the contract stable even when Blender
        # is missing.
        from pipelines.open3d_runner import MAX_FACES, Open3DRunner

        Open3DRunner._normalize_in_place(mesh)
        if len(mesh.triangles) > MAX_FACES:
            mesh = mesh.simplify_quadric_decimation(target_number_of_triangles=MAX_FACES)
            mesh.compute_vertex_normals()

        # Try the Blender refinement; on success it overwrites the GLB
        # with a higher-quality (UV-unwrapped, boolean-cleaned) version.
        blender_bin = _resolve_blender_bin()
        if blender_bin is not None:
            try:
                self._blender_refine(mesh, output_dir, blender_bin, on_progress)
            except Exception as exc:
                # Don't fail the job just because the polish step blew up.
                # The Open3D output is the source of truth.
                logger.warning("blender refine failed, keeping Open3D output: %s", exc)
        else:
            logger.info(
                "blender_cleanup: BLENDER_BIN not set / not found; "
                "TODO: ship a Blender headless script for boolean cleanup + UV "
                "unwrapping once the worker image has Blender installed"
            )

        # Re-export the (possibly-refined) GLB.
        o3d.io.write_triangle_mesh(str(glb_in), mesh, write_ascii=False)
        aabb = mesh.get_axis_aligned_bounding_box()
        import numpy as np

        return {
            "mesh_path": str(glb_in),
            "point_cloud_path": str(output_dir / "cleaned.ply"),
            "vertex_count": len(mesh.vertices),
            "face_count": len(mesh.triangles),
            "bbox_min": [float(x) for x in np.asarray(aabb.get_min_bound()).tolist()],
            "bbox_max": [float(x) for x in np.asarray(aabb.get_max_bound()).tolist()],
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
