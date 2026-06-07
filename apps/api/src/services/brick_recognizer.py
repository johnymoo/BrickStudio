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


__all__ = [
    "DEFAULT_MIN_CONFIDENCE",
    "DEFAULT_PITCH_TOLERANCE_MM",
    "SYSTEM_UNIT_MM",
    "classify_system",
    "encode_depth16_png",
    "load_depth16_png",
]
