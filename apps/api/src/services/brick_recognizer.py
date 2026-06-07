"""AR-capture brick recognizer.

Given a top-down stud photo + a 16-bit depth map + camera intrinsics
(all uploaded by the ARCore client), recognize a standard building
block: its system (lego / feile / duplo), stud grid (units_x × units_y),
and metric stud pitch. When the pitch matches a known system within
tolerance, the caller generates the exact canonical parametric GLB with
zero caliper input.

Everything here is a pure function operating on numpy arrays / dicts so
it is unit-testable without a phone, a GPU, or a running stack. The one
genuinely hard step — detecting stud centres in a real photo
(:func:`detect_studs`) — uses a simple, deterministic blob detector that
is reliable on clean, high-contrast top-down frames (the synthetic test
fixtures and well-lit captures). Robustness on messy real-world photos is
an iterative, on-device concern and is out of scope for this first cut.
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image
from scipy import ndimage
from scipy.spatial import cKDTree

from services.block_generator import DUPLO_UNIT_MM, FEILE_UNIT_MM, LEGO_UNIT_MM

logger = logging.getLogger(__name__)

#: Canonical unit pitch (mm) per system. Imported from block_generator so
#: the recognizer and the mesh generator never drift.
SYSTEM_UNIT_MM: dict[str, float] = {
    "lego": LEGO_UNIT_MM,    # 8.0
    "feile": FEILE_UNIT_MM,  # 16.0
    "duplo": DUPLO_UNIT_MM,  # 20.0
}

DEFAULT_PITCH_TOLERANCE_MM: float = 3.0
DEFAULT_MIN_CONFIDENCE: float = 0.6


def classify_system(
    pitch_mm: float, tolerance_mm: float = DEFAULT_PITCH_TOLERANCE_MM
) -> tuple[str | None, float]:
    """Snap a measured metric pitch to the nearest known system.

    Returns ``(system, confidence)``. ``confidence = 1 - Δ/tolerance``
    (clamped to [0, 1]); ``(None, 0.0)`` when no system is within
    ``tolerance_mm``.
    """
    best_system: str | None = None
    best_diff: float | None = None
    for system, unit in SYSTEM_UNIT_MM.items():
        diff = abs(pitch_mm - unit)
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best_system = system
    if best_diff is None or best_diff > tolerance_mm:
        return None, 0.0
    confidence = max(0.0, min(1.0, 1.0 - best_diff / tolerance_mm))
    return best_system, confidence


def encode_depth16_png(arr_mm: np.ndarray) -> bytes:
    """Encode a uint16 millimetre depth array as a lossless 16-bit PNG.

    Used by tests / fixtures (the production depth bytes come from the
    Android client). Mirrors :func:`load_depth16_png`.
    """
    img = Image.fromarray(np.ascontiguousarray(arr_mm.astype(np.uint16)))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def load_depth16_png(data: bytes) -> np.ndarray:
    """Decode a 16-bit single-channel PNG into a uint16 (H, W) mm array."""
    img = Image.open(io.BytesIO(data))
    arr = np.asarray(img)
    if arr.ndim != 2:
        raise ValueError(
            f"depth PNG must be single-channel (H, W); got shape {arr.shape}"
        )
    return arr.astype(np.uint16)


def _median_nn_distance(points: np.ndarray) -> float | None:
    """Median nearest-neighbour distance among rows of ``points``.

    Returns ``None`` for fewer than 2 points.
    """
    pts = np.asarray(points, dtype=np.float64)
    if pts.shape[0] < 2:
        return None
    tree = cKDTree(pts)
    # k=2: the first neighbour is the point itself (distance 0).
    dists, _ = tree.query(pts, k=2)
    return float(np.median(dists[:, 1]))


def metric_pitch(
    centers_px: np.ndarray,
    depth_mm: np.ndarray,
    intrinsics: dict[str, Any],
) -> float | None:
    """Metric stud pitch (mm) = median NN distance of un-projected studs.

    ``centers_px`` are (x, y) stud centres in the RGB image. Each is
    scaled to the (lower-resolution) depth image, sampled for Z (mm),
    and un-projected with the RGB intrinsics:
    ``X = (u-cx)/fx * Z``, ``Y = (v-cy)/fy * Z``. Returns ``None`` when
    fewer than two studs have valid (> 0) depth.
    """
    centers = np.asarray(centers_px, dtype=np.float64)
    if centers.shape[0] < 2:
        return None
    fx = float(intrinsics["fx"])
    fy = float(intrinsics["fy"])
    cx = float(intrinsics["cx"])
    cy = float(intrinsics["cy"])
    iw = float(intrinsics["width"])
    ih = float(intrinsics["height"])
    dh, dw = depth_mm.shape
    sx = dw / iw
    sy = dh / ih
    pts3d: list[list[float]] = []
    for u, v in centers:
        du = int(round(u * sx))
        dv = int(round(v * sy))
        du = min(max(du, 0), dw - 1)
        dv = min(max(dv, 0), dh - 1)
        z = float(depth_mm[dv, du])
        if z <= 0:
            continue
        x = (u - cx) / fx * z
        y = (v - cy) / fy * z
        pts3d.append([x, y, z])
    if len(pts3d) < 2:
        return None
    return _median_nn_distance(np.array(pts3d, dtype=np.float64))


def detect_studs(
    rgb: np.ndarray,
    *,
    min_area_px: int = 20,
    max_area_frac: float = 0.25,
) -> np.ndarray:
    """Detect stud centres on a top-down frame → (N, 2) array of (x, y).

    Deterministic blob detector: greyscale → threshold at
    ``mean + 0.5*std`` → connected components → centroids of components
    whose area is in ``[min_area_px, max_area_frac * total]``. Tuned for
    clean, high-contrast top-down frames; see the module docstring.
    """
    arr = np.asarray(rgb)
    gray = arr[..., :3].mean(axis=2) if arr.ndim == 3 else arr.astype(np.float64)
    thresh = float(gray.mean() + 0.5 * gray.std())
    mask = gray > thresh
    labels, n = ndimage.label(mask)
    if n == 0:
        return np.empty((0, 2), dtype=np.float64)
    total = gray.size
    centers: list[list[float]] = []
    for lbl in range(1, n + 1):
        ys, xs = np.where(labels == lbl)
        area = xs.size
        if area < min_area_px or area > max_area_frac * total:
            continue
        centers.append([float(xs.mean()), float(ys.mean())])
    if not centers:
        return np.empty((0, 2), dtype=np.float64)
    return np.array(centers, dtype=np.float64)


def _count_clusters(values: np.ndarray, min_gap: float) -> int:
    """Count 1-D clusters: sort, then split wherever the gap > ``min_gap``."""
    s = np.sort(np.asarray(values, dtype=np.float64))
    if s.size == 0:
        return 0
    clusters = 1
    for i in range(1, s.size):
        if s[i] - s[i - 1] > min_gap:
            clusters += 1
    return clusters


def fit_grid(centers_px: np.ndarray, *, px_pitch: float | None = None) -> tuple[int, int]:
    """Infer the stud grid (units_x, units_y) from stud centres.

    Clusters x-coordinates into columns and y-coordinates into rows,
    splitting where the gap exceeds half the (estimated) pixel pitch.
    Assumes a roughly axis-aligned top-down frame.
    """
    centers = np.asarray(centers_px, dtype=np.float64)
    if centers.shape[0] == 0:
        return 0, 0
    if px_pitch is None:
        px_pitch = _median_nn_distance(centers) or 1.0
    gap = px_pitch * 0.5
    units_x = _count_clusters(centers[:, 0], gap)
    units_y = _count_clusters(centers[:, 1], gap)
    return units_x, units_y


__all__ = [
    "DEFAULT_MIN_CONFIDENCE",
    "DEFAULT_PITCH_TOLERANCE_MM",
    "SYSTEM_UNIT_MM",
    "classify_system",
    "detect_studs",
    "encode_depth16_png",
    "fit_grid",
    "load_depth16_png",
    "metric_pitch",
]
