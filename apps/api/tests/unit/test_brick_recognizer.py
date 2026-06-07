"""Unit tests for services.brick_recognizer (pure functions, synthetic data)."""
from __future__ import annotations

import io

import numpy as np
from PIL import Image

from services.brick_recognizer import SYSTEM_UNIT_MM, classify_system


def test_classify_system_matches_feile() -> None:
    system, conf = classify_system(16.1, tolerance_mm=3.0)
    assert system == "feile"
    assert 0.9 < conf <= 1.0


def test_classify_system_matches_lego_and_duplo() -> None:
    assert classify_system(8.2, tolerance_mm=3.0)[0] == "lego"
    assert classify_system(19.7, tolerance_mm=3.0)[0] == "duplo"


def test_classify_system_out_of_tolerance_returns_none() -> None:
    # 12mm is 4mm from both lego(8) and feile(16) → no match within 3mm.
    system, conf = classify_system(12.0, tolerance_mm=3.0)
    assert system is None
    assert conf == 0.0


def test_system_unit_table_matches_block_generator() -> None:
    # Stay in lock-step with the canonical block_generator constants.
    from services.block_generator import DUPLO_UNIT_MM, FEILE_UNIT_MM, LEGO_UNIT_MM

    assert SYSTEM_UNIT_MM == {"lego": LEGO_UNIT_MM, "feile": FEILE_UNIT_MM, "duplo": DUPLO_UNIT_MM}


def test_depth16_png_round_trip() -> None:
    from services.brick_recognizer import encode_depth16_png, load_depth16_png

    arr = np.array([[0, 250, 1000], [65535, 12345, 1]], dtype=np.uint16)
    data = encode_depth16_png(arr)
    out = load_depth16_png(data)
    assert out.shape == arr.shape
    assert out.dtype == np.uint16
    assert np.array_equal(out, arr)


def test_load_depth16_png_rejects_rgb() -> None:
    import pytest

    from services.brick_recognizer import load_depth16_png

    # A 3-channel PNG is not a valid depth map.
    rgb = Image.fromarray(np.zeros((4, 4, 3), dtype=np.uint8))
    buf = io.BytesIO()
    rgb.save(buf, format="PNG")
    with pytest.raises(ValueError, match="single-channel"):
        load_depth16_png(buf.getvalue())


def test_metric_pitch_constant_depth() -> None:
    """A 2x2 grid 80px apart at Z=250mm with fx=1250 → pitch = 16mm.

    pitch_mm = px_pitch * Z / fx = 80 * 250 / 1250 = 16.0 (FEILE).
    Depth is half-resolution to exercise the RGB→depth scaling path.
    """
    from services.brick_recognizer import metric_pitch

    centers = np.array(
        [[280.0, 200.0], [360.0, 200.0], [280.0, 280.0], [360.0, 280.0]],
        dtype=np.float64,
    )  # 80px pitch in both axes
    intrinsics = {"fx": 1250.0, "fy": 1250.0, "cx": 320.0, "cy": 240.0, "width": 640, "height": 480}
    depth = np.full((240, 320), 250, dtype=np.uint16)  # half-res, constant 250mm
    pitch = metric_pitch(centers, depth, intrinsics)
    assert pitch is not None
    assert abs(pitch - 16.0) < 0.2, pitch


def test_metric_pitch_too_few_points() -> None:
    from services.brick_recognizer import metric_pitch

    intrinsics = {"fx": 1250.0, "fy": 1250.0, "cx": 320.0, "cy": 240.0, "width": 640, "height": 480}
    depth = np.full((10, 10), 250, dtype=np.uint16)
    assert metric_pitch(np.array([[1.0, 1.0]]), depth, intrinsics) is None
