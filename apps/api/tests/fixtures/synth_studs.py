"""Synthetic AR-capture bundles for ar-captures tests (no phone needed).

Builds a top-down stud RGB + constant-depth 16-bit PNG + ar_metadata
whose intrinsics make the metric pitch resolve to a chosen system.
"""
from __future__ import annotations

import io
import json
from typing import Any

import numpy as np
from PIL import Image

from services.brick_recognizer import SYSTEM_UNIT_MM, encode_depth16_png


def _draw_studs(width: int, height: int, centers: list[tuple[int, int]], radius: int) -> np.ndarray:
    img = np.zeros((height, width, 3), dtype=np.uint8)
    yy, xx = np.mgrid[0:height, 0:width]
    for cx, cy in centers:
        img[(xx - cx) ** 2 + (yy - cy) ** 2 <= radius**2] = 255
    return img


def make_ar_bundle(
    system: str = "feile", units_x: int = 2, units_y: int = 4
) -> tuple[bytes, bytes, dict[str, Any]]:
    """Return (rgb_png_bytes, depth16_png_bytes, ar_metadata_dict)."""
    w, h, px_pitch, z = 640, 480, 80.0, 250.0
    fx = px_pitch * z / SYSTEM_UNIT_MM[system]
    cx, cy = w / 2.0, h / 2.0
    centers: list[tuple[int, int]] = []
    for i in range(units_x):
        for j in range(units_y):
            x = int(cx + (i - (units_x - 1) / 2.0) * px_pitch)
            y = int(cy + (j - (units_y - 1) / 2.0) * px_pitch)
            centers.append((x, y))
    rgb = _draw_studs(w, h, centers, radius=12)
    rgb_buf = io.BytesIO()
    Image.fromarray(rgb).save(rgb_buf, format="PNG")
    depth = np.full((h // 4, w // 4), int(z), dtype=np.uint16)
    meta = {
        "device": {"model": "TEST", "arcore": "x"},
        "recognition_frame": {
            "image_intrinsics": {"fx": fx, "fy": fx, "cx": cx, "cy": cy, "width": w, "height": h},
            "depth": {"width": w // 4, "height": h // 4, "format": "DEPTH16_MM"},
            "camera_pose": {"t": [0, 0, 0], "q": [0, 0, 0, 1]},
            "distance_m": z / 1000.0,
        },
        "coarse_hints": {"rough_units_x": units_x, "rough_units_y": units_y, "rough_pitch_mm": 0},
    }
    return rgb_buf.getvalue(), encode_depth16_png(depth), meta


def ar_metadata_json(meta: dict[str, Any]) -> str:
    return json.dumps(meta)
