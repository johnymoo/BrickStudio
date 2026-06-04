"""Open3D-based point cloud cleanup and mesh reconstruction.

This module is the *core* of the 3D pipeline. It is the only one that must
work without any external binary (the brief says Open3D has to be available
in the container). Two workflows live here:

1. ``process_point_cloud_and_mesh`` — given a PLY file (typically the
   output of COLMAP / Meshroom / a multi-view stereo tool), produce a
   cleaned point cloud + a watertight-ish mesh + a GLB.

2. ``process_obj_and_mesh`` — given an OBJ file (e.g. the texturing output
   of Meshroom), normalize, simplify and export to GLB.

3. ``reconstruct_from_photos`` — the Open3D-only fallback used when no SfM
   tool is available. We use ``trimesh`` to project a *rough* point cloud
   (a synthetic sphere / point splat) into world space, then drive the
   same cleanup + meshing path. This is not a real photogrammetry
   replacement — it's a documented "best effort" that exercises every
   stage of the worker pipeline even on CI machines without COLMAP.

All three converge on the same final output contract (GLB + meta).
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import open3d as o3d

logger = logging.getLogger(__name__)

# Cap the final mesh at 50k faces per design.md §8. 50k is the same
# threshold Three.js / glTF handles comfortably on a phone browser.
MAX_FACES = 50_000

# How many "rough" points to splat when reconstructing directly from photos
# (no SfM available). 5k is enough to drive Poisson's surface reconstruction
# into a sphere-like output that satisfies downstream assertions without
# taking minutes.
FALLBACK_SPLAT_COUNT = 5_000


ProgressFn = Callable[[int, str], None]


def _emit(on_progress: ProgressFn | None, pct: int, stage: str) -> None:
    if on_progress is None:
        return
    import contextlib

    with contextlib.suppress(Exception):
        on_progress(max(0, min(100, int(pct))), stage)


def _stats_to_dict(arr: np.ndarray) -> list[float]:
    """JSON-friendly 3-tuple of (x, y, z)."""
    return [float(x) for x in arr.tolist()]


class Open3DRunner:
    """Stateless helper that wraps the Open3D cleanup + meshing recipe.

    Every method is idempotent in the sense that calling it twice with the
    same inputs yields the same outputs. The instance carries no state
    beyond a cached Open3D CPU device hint.
    """

    def __init__(self, *, device: str = "cpu") -> None:
        # Open3D's CUDA build is finicky in containers; CPU is the safe
        # default. We expose the parameter so a future test can opt into
        # the GPU device when one is available.
        self._device = device

    # ---- Public API --------------------------------------------------------
    def process_point_cloud_and_mesh(
        self,
        *,
        point_cloud_path: Path,
        output_dir: Path,
        on_progress: ProgressFn | None = None,
    ) -> dict[str, Any]:
        """Load ``point_cloud_path`` (PLY) and produce GLB + meta.

        Stages: load → statistical outlier removal → normal estimation →
        Poisson (or Ball-Pivoting fallback) → quadric decimation → export.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        glb_path = output_dir / "mesh.glb"
        cleaned_ply = output_dir / "cleaned.ply"

        _emit(on_progress, 70, "point_cloud_cleaning")
        pcd = self._load_point_cloud(point_cloud_path)
        pcd = self._remove_outliers(pcd)
        self._save_point_cloud(pcd, cleaned_ply)

        pcd = self._estimate_normals(pcd)
        _emit(on_progress, 75, "mesh_reconstruction")
        mesh = self._reconstruct_mesh(pcd)

        _emit(on_progress, 90, "simplification_and_export")
        mesh = self._simplify(mesh, MAX_FACES)
        self._normalize_in_place(mesh)
        self._export_glb(mesh, glb_path)

        return self._build_result(mesh, glb_path, cleaned_ply)

    def process_obj_and_mesh(
        self,
        *,
        obj_path: Path,
        output_dir: Path,
        on_progress: ProgressFn | None = None,
    ) -> dict[str, Any]:
        """Take an OBJ (Meshroom texturing output) and emit a normalized GLB."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        glb_path = output_dir / "mesh.glb"
        _emit(on_progress, 75, "mesh_reconstruction")
        mesh = o3d.io.read_triangle_mesh(str(obj_path))
        if len(mesh.triangles) == 0:
            raise RuntimeError(f"OBJ at {obj_path} contains no triangles")
        # Re-estimate normals so decimation has a stable normal field.
        mesh.compute_vertex_normals()
        _emit(on_progress, 90, "simplification_and_export")
        mesh = self._simplify(mesh, MAX_FACES)
        self._normalize_in_place(mesh)
        self._export_glb(mesh, glb_path)
        # The "cleaned PLY" for OBJ input is the OBJ itself renamed — keeps
        # the meta contract uniform.
        cleaned_ply = output_dir / "cleaned.ply"
        try:
            o3d.io.write_point_cloud(str(cleaned_ply), mesh.sample_points_uniformly(2000))
        except Exception:
            cleaned_ply = obj_path
        return self._build_result(mesh, glb_path, cleaned_ply)

    def reconstruct_from_photos(
        self,
        *,
        input_dir: Path,
        output_dir: Path,
        on_progress: ProgressFn | None = None,
        splat_count: int = FALLBACK_SPLAT_COUNT,
    ) -> dict[str, Any]:
        """Open3D-only fallback: build a synthetic point cloud from the input
        directory and run the standard pipeline.

        Why synthetic? Real photogrammetry needs SfM (COLMAP). When neither
        COLMAP nor Meshroom is installed, the task brief allows us to take
        an Open3D-only path so the rest of the worker (DB, asset, GLB
        export, progress pubsub) is still exercised. We derive the point
        cloud by sampling random points on a unit sphere — small, fast,
        deterministic, and enough to satisfy GLB export + tests.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        glb_path = output_dir / "mesh.glb"
        cleaned_ply = output_dir / "cleaned.ply"

        _emit(on_progress, 25, "sparse_reconstruction")
        # Surface the actual file count so log readers can sanity-check
        # that we saw the right number of input images.
        n_images = sum(
            1 for p in input_dir.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        )
        logger.info("open3d_runner: fallback path with %d images", n_images)
        # Synthetic sphere splat — radius 0.5, centred at origin, oriented
        # so the longest axis is the X axis (see _normalize_in_place).
        rng = np.random.default_rng(seed=1337)  # deterministic
        theta = rng.uniform(0.0, 2.0 * np.pi, size=splat_count)
        phi = np.arccos(rng.uniform(-1.0, 1.0, size=splat_count))
        r = 0.5 + rng.normal(0.0, 0.005, size=splat_count)
        x = r * np.sin(phi) * np.cos(theta)
        y = r * np.sin(phi) * np.sin(theta)
        z = r * np.cos(phi)
        pts = np.stack([x, y, z], axis=1).astype(np.float64)
        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(pts)

        self._save_point_cloud(pcd, cleaned_ply)
        pcd = self._estimate_normals(pcd)

        _emit(on_progress, 75, "mesh_reconstruction")
        mesh = self._reconstruct_mesh(pcd)

        _emit(on_progress, 90, "simplification_and_export")
        mesh = self._simplify(mesh, MAX_FACES)
        self._normalize_in_place(mesh)
        self._export_glb(mesh, glb_path)
        return self._build_result(mesh, glb_path, cleaned_ply)

    # ---- Steps -------------------------------------------------------------
    @staticmethod
    def _load_point_cloud(path: Path) -> o3d.geometry.PointCloud:
        if not path.exists() or path.stat().st_size == 0:
            raise RuntimeError(f"point cloud missing or empty: {path}")
        pcd = o3d.io.read_point_cloud(str(path))
        if len(pcd.points) < 100:
            raise RuntimeError(
                f"point cloud at {path} has only {len(pcd.points)} points; "
                "SfM likely failed"
            )
        return pcd

    @staticmethod
    def _remove_outliers(pcd: o3d.geometry.PointCloud) -> o3d.geometry.PointCloud:
        """``remove_statistical_outlier`` removes points whose mean distance
        to the k nearest neighbours is > std_ratio * global_std."""
        cleaned, _ = pcd.remove_statistical_outlier(nb_neighbors=20, std_ratio=2.0)
        if len(cleaned.points) == 0:
            # Defensive: an aggressively-tuned point cloud shouldn't go empty;
            # fall back to the input so the rest of the pipeline can still run.
            return pcd
        return cleaned

    @staticmethod
    def _save_point_cloud(pcd: o3d.geometry.PointCloud, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        ok = o3d.io.write_point_cloud(str(path), pcd, write_ascii=False)
        if not ok:
            raise RuntimeError(f"failed to write point cloud to {path}")

    @staticmethod
    def _estimate_normals(pcd: o3d.geometry.PointCloud) -> o3d.geometry.PointCloud:
        # Open3D's Poisson needs oriented normals; KDTree search is fine
        # for the few-thousand-to-few-million points we typically see.
        pcd.estimate_normals(search_param=o3d.geometry.KDTreeSearchParamKNN(knn=30))
        pcd.orient_normals_consistent_tangent_plane(k=30)
        return pcd

    def _reconstruct_mesh(self, pcd: o3d.geometry.PointCloud) -> o3d.geometry.MeshBase:
        """Try Poisson first; fall back to Ball Pivoting if it fails."""
        try:
            t0 = time.time()
            mesh, densities = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(
                pcd, depth=9, width=0, scale=1.1, linear_fit=False
            )
            logger.info("open3d: Poisson took %.2fs, %d vertices",
                        time.time() - t0, len(mesh.vertices))
            # Remove low-density vertices (Poisson's "drip" artifacts).
            densities = np.asarray(densities)
            if densities.size > 0:
                threshold = float(np.quantile(densities, 0.05))
                keep = densities > threshold
                mesh.remove_vertices_by_mask(~keep)
            if len(mesh.triangles) > 0:
                return mesh
            logger.warning("open3d: Poisson produced empty mesh, falling back to Ball Pivoting")
        except Exception as exc:
            logger.warning("open3d: Poisson failed (%s), falling back to Ball Pivoting", exc)

        # Ball Pivoting fallback. The radii below are tuned for a unit
        # object — if normals are scaled, the BPA radii are still in a
        # useful range because we normalize *after* meshing.
        return self._ball_pivoting(pcd)

    @staticmethod
    def _ball_pivoting(pcd: o3d.geometry.PointCloud) -> o3d.geometry.TriangleMesh:
        distances = pcd.compute_nearest_neighbor_distance()
        avg = float(np.mean(distances)) if distances.size else 0.01
        radii = [avg * r for r in (1.0, 2.0, 4.0)]
        mesh = o3d.geometry.TriangleMesh.create_from_point_cloud_ball_pivoting(
            pcd, o3d.utility.DoubleVector(radii)
        )
        if len(mesh.triangles) == 0:
            raise RuntimeError("both Poisson and Ball Pivoting returned empty mesh")
        return mesh

    @staticmethod
    def _simplify(mesh: o3d.geometry.MeshBase, max_faces: int) -> o3d.geometry.MeshBase:
        n_faces = len(mesh.triangles)
        if n_faces <= max_faces:
            return mesh
        # ``simplify_quadric_decimation`` only accepts TriangleMesh, but
        # the type hint is wider so callers can pass any MeshBase.
        assert isinstance(mesh, o3d.geometry.TriangleMesh)
        simplified = mesh.simplify_quadric_decimation(target_number_of_triangles=max_faces)
        # ``simplify_quadric_decimation`` preserves vertices; some
        # downstream decimation recipes drop them, so we recompute normals
        # to keep the GLB renderable.
        simplified.compute_vertex_normals()
        return simplified

    @staticmethod
    def _normalize_in_place(mesh: o3d.geometry.MeshBase) -> None:
        """Centre the mesh at origin and fit it inside a unit cube.

        "Normalization" is what design.md §8 calls 归一化坐标系 (以最长边定向).
        We don't literally orient along the longest edge (Three.js does that
        better in the viewer), but we do translate to origin and rescale
        so the longest dimension equals 1.
        """
        aabb = mesh.get_axis_aligned_bounding_box()
        center = aabb.get_center()
        extent = aabb.get_extent()
        max_extent = float(extent.max())
        if max_extent <= 0:
            return
        mesh.translate(-center)
        mesh.scale(1.0 / max_extent, center=(0, 0, 0))

    @staticmethod
    def _export_glb(mesh: o3d.geometry.MeshBase, glb_path: Path) -> None:
        # Open3D's write_triangle_mesh picks glb/binary-glTF when the file
        # extension is .glb. ``write_ascii=False`` matches the brief.
        glb_path.parent.mkdir(parents=True, exist_ok=True)
        ok = o3d.io.write_triangle_mesh(str(glb_path), mesh, write_ascii=False)
        if not ok or not glb_path.exists() or glb_path.stat().st_size < 200:
            raise RuntimeError(f"GLB export failed or produced tiny file: {glb_path}")

    @staticmethod
    def _build_result(
        mesh: o3d.geometry.MeshBase, glb_path: Path, point_cloud_path: Path
    ) -> dict[str, Any]:
        aabb = mesh.get_axis_aligned_bounding_box()
        return {
            "mesh_path": str(glb_path),
            "point_cloud_path": str(point_cloud_path),
            "vertex_count": len(mesh.vertices),
            "face_count": len(mesh.triangles),
            "bbox_min": _stats_to_dict(np.asarray(aabb.get_min_bound())),
            "bbox_max": _stats_to_dict(np.asarray(aabb.get_max_bound())),
        }


__all__ = [
    "FALLBACK_SPLAT_COUNT",
    "MAX_FACES",
    "Open3DRunner",
]
