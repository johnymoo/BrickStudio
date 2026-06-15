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


def _draw_studs(width: int, height: int, centers: list[tuple[int, int]], radius: int) -> np.ndarray:
    """Bright disks on a dark background → an (H, W, 3) uint8 RGB array."""
    img = np.zeros((height, width, 3), dtype=np.uint8)
    yy, xx = np.mgrid[0:height, 0:width]
    for cx, cy in centers:
        mask = (xx - cx) ** 2 + (yy - cy) ** 2 <= radius**2
        img[mask] = 255
    return img


def test_detect_studs_finds_all_centres() -> None:
    from services.brick_recognizer import detect_studs

    centers = [(280, 120), (360, 120), (280, 200), (360, 200),
               (280, 280), (360, 280), (280, 360), (360, 360)]  # 2 cols x 4 rows
    img = _draw_studs(640, 480, centers, radius=12)
    found = detect_studs(img)
    assert found.shape[0] == 8, found.shape
    # Each found centre should be near a drawn centre.
    for cx, cy in centers:
        d = np.min(np.hypot(found[:, 0] - cx, found[:, 1] - cy))
        assert d < 2.0, (cx, cy, d)


def test_fit_grid_counts_rows_and_cols() -> None:
    from services.brick_recognizer import detect_studs, fit_grid

    centers = [(280, 120), (360, 120), (280, 200), (360, 200),
               (280, 280), (360, 280), (280, 360), (360, 360)]
    img = _draw_studs(640, 480, centers, radius=12)
    found = detect_studs(img)
    units_x, units_y = fit_grid(found)
    assert (units_x, units_y) == (2, 4)


def _ar_bundle(system: str = "feile", units_x: int = 2, units_y: int = 4):
    """Build (rgb_bytes, depth_bytes, ar_metadata) that recognizes as ``system``.

    Picks fx so px_pitch * Z / fx == SYSTEM_UNIT_MM[system].
    """
    from services.brick_recognizer import SYSTEM_UNIT_MM, encode_depth16_png

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
    depth = np.full((h // 4, w // 4), int(z), dtype=np.uint16)  # quarter-res, constant
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


def test_recognize_brick_feile_2x4() -> None:
    from services.brick_recognizer import recognize_brick

    rgb, depth, meta = _ar_bundle("feile", 2, 4)
    r = recognize_brick(rgb_bytes=rgb, depth_bytes=depth, ar_metadata=meta, kind="brick")
    assert r.ok is True
    assert r.system == "feile"
    assert (r.units_x, r.units_y) == (2, 4)
    assert abs(r.pitch_mm - 16.0) < 0.3
    assert r.confidence >= 0.6


def test_recognize_brick_unknown_pitch_not_ok() -> None:
    from services.brick_recognizer import recognize_brick

    rgb, depth, meta = _ar_bundle("feile", 2, 2)
    # Corrupt the focal length so the pitch lands at ~12mm (between systems).
    meta["recognition_frame"]["image_intrinsics"]["fx"] = 80.0 * 250.0 / 12.0
    meta["recognition_frame"]["image_intrinsics"]["fy"] = 80.0 * 250.0 / 12.0
    r = recognize_brick(rgb_bytes=rgb, depth_bytes=depth, ar_metadata=meta, kind="brick")
    assert r.ok is False
    assert r.system is None


def test_recognize_brick_bad_rgb_not_ok() -> None:
    from services.brick_recognizer import recognize_brick

    _, depth, meta = _ar_bundle("feile")
    r = recognize_brick(rgb_bytes=b"not-a-png", depth_bytes=depth, ar_metadata=meta, kind="brick")
    assert r.ok is False
    assert r.reason is not None


def test_ar_capture_read_schema_serializes() -> None:
    import uuid
    from datetime import UTC, datetime

    from models.schemas import ArCaptureRead, RecognizedBlock

    body = ArCaptureRead(
        capture_id=uuid.uuid4(),
        part_id="feile-brick-2x4",
        status="recognized",
        recognized=RecognizedBlock(
            system="feile", kind="brick", units_x=2, units_y=4, pitch_mm=16.1, confidence=0.96
        ),
        needs_measurement=None,
        job_id=uuid.uuid4(),
        warnings=["measured pitch 16.10mm vs feile canonical 16.0mm (Δ0.10)"],
        created_at=datetime.now(tz=UTC),
    )
    dumped = body.model_dump(mode="json")
    assert dumped["status"] == "recognized"
    assert dumped["recognized"]["system"] == "feile"
    assert dumped["needs_measurement"] is None
