"""3D reconstruction pipeline adapters.

Every concrete pipeline (COLMAP / Meshroom / Open3D / Blender cleanup) follows
the same :class:`Pipeline` contract so the Celery task in
:mod:`workers.tasks.reconstruct` can drive them interchangeably:

    pipeline.run(
        input_dir=...,
        output_dir=...,
        on_progress=lambda pct, stage: ...,
    )

The contract deliberately exposes only what the worker needs:

* ``input_dir``  — already-downloaded source photos (one per file, supported
  extensions: jpg / jpeg / png / webp / heic).
* ``output_dir`` — working directory the pipeline can scribble intermediates
  in. The worker expects a final ``mesh.glb`` (and optionally ``point_cloud.ply``)
  to appear under it; if not, the pipeline is considered failed.
* ``on_progress`` — callback fired with an integer percent in [0, 100] and a
  short string stage label. Workers translate that into both the Celery
  ``self.update_state`` state machine and the Redis pub/sub channel.

Pipelines are *expected* to mutate ``output_dir`` in place and may emit many
files; cleanup is the worker's responsibility.

Pipelines raise :class:`PipelineUnavailable` when their underlying toolchain
(Meshroom binary, COLMAP binary, GPU, etc.) is not present. The worker
translates that into a graceful ``failed`` job with a clear error message
instead of an opaque stack trace.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any

# ``ProgressFn`` is the public callback shape. Aliasing it makes the signature
# readable at every call site.
ProgressFn = Callable[[int, str], None]


class PipelineUnavailable(RuntimeError):
    """Raised when a pipeline's required toolchain is not reachable.

    The Celery task catches this specifically and reports a stable error
    message ("reconstruction toolchain unavailable") to the job row so
    operators can distinguish "we need to install Meshroom" from "the
    algorithm crashed on bad input".
    """


class Pipeline(ABC):
    """Common 3D-reconstruction pipeline interface (see module docstring)."""

    #: Human-readable name written to the asset ``meta.pipeline_used`` field
    #: so downstream code can tell which path actually produced the GLB.
    name: str = "abstract"
    #: Semver-ish version baked into ``meta.pipeline_version``; bump on
    #: substantive changes so we can compare outputs across runs.
    version: str = "0.0.0"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Subclasses may want to inspect binary paths etc.; keep the ctor open
        # without forcing all subclasses to repeat ``super().__init__()``.
        return

    @abstractmethod
    def run(
        self,
        *,
        input_dir: Path,
        output_dir: Path,
        on_progress: ProgressFn,
    ) -> dict[str, Any]:
        """Run the full reconstruction pipeline.

        Returns a result dict with at least:

        * ``"mesh_path"`` — absolute path to the final GLB file.
        * ``"point_cloud_path"`` — absolute path to the final point cloud
          (PLY). May be the same path as ``mesh_path`` if the pipeline
          only produces meshes.
        * ``"vertex_count"`` / ``"face_count"`` — for the asset meta.
        * ``"bbox_min"`` / ``"bbox_max"`` — 3-tuples of floats.

        The implementation MUST call ``on_progress`` at every meaningful
        stage boundary (sparse / dense / clean / mesh / export). It MAY
        call it more often (e.g. every percentage tick); the worker
        de-duplicates writes either way.
        """

    # ---- Helpers shared by all subclasses ---------------------------------
    @staticmethod
    def _ensure_dir(path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _emit(on_progress: ProgressFn, pct: int, stage: str) -> None:
        """Clamp + call the progress callback. Worker-side subscribers
        sometimes spam stdout with no-ops, so we swallow exceptions from
        broken callbacks here — a dropped progress event must not abort
        a multi-minute reconstruction run."""
        import contextlib

        with contextlib.suppress(Exception):
            on_progress(max(0, min(100, int(pct))), stage)


__all__ = [
    "Pipeline",
    "PipelineUnavailable",
    "ProgressFn",
]
