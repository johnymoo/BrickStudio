# AR Capture Recognition + Parametric Modeling (Backend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a BrickStudio backend path where a phone (ARCore) uploads a top-down stud photo + 16-bit depth + camera intrinsics, the server recognizes the brick (system / kind / stud-grid), and — when confident — generates the exact canonical parametric GLB with zero caliper input; otherwise it returns a `needs_measurement` fallback that reuses the existing `/parametric-blocks` flow.

**Architecture:** A new `POST /api/v1/ar-captures` endpoint runs recognition **synchronously** (fast: PIL decode + scipy.ndimage blob detection + pinhole un-projection math), persists a `Capture(mode="ar_recognized")`, and — on a confident match — dispatches the existing `reconstruct` Celery task. The worker gains an `ar_recognized` branch (`_run_ar_recognized_pipeline`) that builds a canonical `BlockSpec(system, kind, units)` and calls the existing `export_glb` + `_finalize_mesh_pipeline`. Recognition logic lives in a new pure-function service `brick_recognizer.py` so it is unit-testable on the JVM-free dev box; an offline fixture drives a full POST→worker→GLB e2e with no phone.

**Tech Stack:** FastAPI + SQLAlchemy (async) + Celery + MinIO + Alembic; numpy + scipy (`ndimage`, `spatial.cKDTree`) + Pillow for recognition; trimesh for GLB (via existing `services.block_generator`). Python ≥ 3.11, `uv`, pytest + pytest-asyncio.

**Repo / location:** All paths below are relative to `apps/api/` in the `BrickStudio` repo (current branch `v0.2-development`). Spec: `docs/ar-capture-recognition-design.md`. This plan is its own subsystem (one coherent backend feature) — no further decomposition needed.

**Conventions to follow (from the existing code):**
- Endpoints mirror `src/api/v1/parametric_blocks.py` (multipart `Form()`/`File()`, `CaptureInvalid` for 422, `JSONResponse(model_dump(mode="json"))`).
- Worker branches mirror `_run_parametric_pipeline` in `src/workers/tasks/reconstruct.py`.
- Tests mirror `tests/test_reconstruction_parametric.py` (the `_png()` helper, `app_client`, `work_in_tmp`, in-process `reconstruct.apply(args=[capture_id])`).
- **All backend tests require the local stack** (Postgres/Redis/MinIO) — `tests/conftest.py` auto-starts them (or uses a running stack), exactly like every existing backend test. Run from `apps/api/`.

**Test command (used throughout):**
```bash
cd apps/api && uv run --project . pytest <path> -v
```

---

### Task 1: DB columns `ar_metadata` + `recognition_result`

**Files:**
- Modify: `src/db/models.py` (Capture class, after `cross_check_warnings` at line 84)
- Create: `alembic/versions/0004_captures_ar_recognized.py`

- [ ] **Step 1: Add the two columns to the ORM model**

In `src/db/models.py`, immediately after the `cross_check_warnings` column (line 84) and before `created_at` (line 85), add:

```python
    #: AR capture (v0.5, ar_recognized mode): the raw ``ar_metadata``
    #: JSON the phone uploaded (camera intrinsics, depth dims, pose,
    #: distance, coarse hints). Stored verbatim for audit / re-run.
    ar_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    #: AR recognition output: ``units_x`` / ``units_y`` / ``pitch_mm`` /
    #: ``system`` / ``confidence`` / ``warnings`` / ``ok`` / ``reason``.
    #: Populated by ``services.brick_recognizer.recognize_brick`` in the
    #: route layer. NULL for non-AR captures.
    recognition_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
```

- [ ] **Step 2: Write the migration**

Create `alembic/versions/0004_captures_ar_recognized.py`:

```python
"""add captures ar_recognized columns (v0.5)

Revision ID: 0004_captures_ar_recognized
Revises: 0003_captures_parametric_block
Create Date: 2026-06-07 10:00:00

Adds the two JSONB columns the AR-capture recognition path needs. Both
are nullable; existing photo / parametric_block rows keep NULLs. The
``mode`` column (added in 0003) gains a new application-level value
``"ar_recognized"`` — no schema change needed for that (it's a String).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_captures_ar_recognized"
down_revision: str | Sequence[str] | None = "0003_captures_parametric_block"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "captures",
        sa.Column("ar_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "captures",
        sa.Column("recognition_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("captures", "recognition_result")
    op.drop_column("captures", "ar_metadata")
```

- [ ] **Step 3: Apply the migration and verify it round-trips**

Run:
```bash
cd apps/api && uv run --project . alembic upgrade head && uv run --project . alembic downgrade -1 && uv run --project . alembic upgrade head
```
Expected: three "Running upgrade / downgrade" lines, no errors; ends at `0004_captures_ar_recognized`.

- [ ] **Step 4: Commit**

```bash
git add src/db/models.py alembic/versions/0004_captures_ar_recognized.py
git commit -m "feat(db): add ar_metadata + recognition_result columns (ar_recognized mode)"
```

---

### Task 2: Config settings `ar_pitch_tolerance_mm` + `ar_min_confidence`

**Files:**
- Modify: `src/app/config.py` (after the `reconstruct_min_images_for_colmap` field, line 125)

- [ ] **Step 1: Add the two settings**

In `src/app/config.py`, after the `reconstruct_min_images_for_colmap` field (ends line 125), add:

```python

    # ---- AR capture recognition (v0.5) -------------------------------------
    ar_pitch_tolerance_mm: float = Field(
        default=3.0,
        gt=0,
        description=(
            "Max |measured pitch − canonical unit| (mm) for a system "
            "classification to count. LEGO 8 / FEILE 16 / DUPLO 20 are "
            "≥4mm apart, so 3mm absorbs AR depth noise without aliasing."
        ),
    )
    ar_min_confidence: float = Field(
        default=0.6,
        ge=0,
        le=1,
        description=(
            "Min recognizer confidence (0..1, = 1 − Δ/tolerance) to take "
            "the zero-measurement path. Below this the endpoint returns "
            "needs_measurement and the client falls back to /parametric-blocks."
        ),
    )
```

- [ ] **Step 2: Verify settings import cleanly**

Run:
```bash
cd apps/api && uv run --project . python -c "from app.config import settings; print(settings.ar_pitch_tolerance_mm, settings.ar_min_confidence)"
```
Expected: `3.0 0.6`

- [ ] **Step 3: Commit**

```bash
git add src/app/config.py
git commit -m "feat(config): add ar_pitch_tolerance_mm + ar_min_confidence"
```

---

### Task 3: `classify_system` + constants (recognizer, part 1)

**Files:**
- Create: `src/services/brick_recognizer.py`
- Create: `tests/test_brick_recognizer.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_brick_recognizer.py`:

```python
"""Unit tests for services.brick_recognizer (pure functions, synthetic data)."""
from __future__ import annotations

import numpy as np

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && uv run --project . pytest tests/test_brick_recognizer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.brick_recognizer'`.

- [ ] **Step 3: Create the module with constants + `classify_system`**

Create `src/services/brick_recognizer.py`:

```python
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


__all__ = [
    "DEFAULT_MIN_CONFIDENCE",
    "DEFAULT_PITCH_TOLERANCE_MM",
    "SYSTEM_UNIT_MM",
    "classify_system",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && uv run --project . pytest tests/test_brick_recognizer.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/services/brick_recognizer.py tests/test_brick_recognizer.py
git commit -m "feat(recognizer): system classification by metric pitch"
```

---

### Task 4: `encode_depth16_png` / `load_depth16_png` round-trip

**Files:**
- Modify: `src/services/brick_recognizer.py`
- Modify: `tests/test_brick_recognizer.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_brick_recognizer.py`:

```python
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
```

Add the imports at the top of the test file if missing: `import io` and `from PIL import Image`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && uv run --project . pytest tests/test_brick_recognizer.py -k depth16 -v`
Expected: FAIL — `ImportError: cannot import name 'encode_depth16_png'`.

- [ ] **Step 3: Implement the two helpers**

In `src/services/brick_recognizer.py`, add after `classify_system`:

```python
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
```

Add both names to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && uv run --project . pytest tests/test_brick_recognizer.py -k depth16 -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/services/brick_recognizer.py tests/test_brick_recognizer.py
git commit -m "feat(recognizer): 16-bit depth PNG encode/decode"
```

---

### Task 5: `metric_pitch` (pinhole un-projection)

**Files:**
- Modify: `src/services/brick_recognizer.py`
- Modify: `tests/test_brick_recognizer.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_brick_recognizer.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && uv run --project . pytest tests/test_brick_recognizer.py -k metric_pitch -v`
Expected: FAIL — `ImportError: cannot import name 'metric_pitch'`.

- [ ] **Step 3: Implement `metric_pitch` + the NN helper**

In `src/services/brick_recognizer.py`, add:

```python
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
```

Add `metric_pitch` to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && uv run --project . pytest tests/test_brick_recognizer.py -k metric_pitch -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/services/brick_recognizer.py tests/test_brick_recognizer.py
git commit -m "feat(recognizer): metric stud pitch via pinhole un-projection"
```

---

### Task 6: `detect_studs` + `fit_grid`

**Files:**
- Modify: `src/services/brick_recognizer.py`
- Modify: `tests/test_brick_recognizer.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_brick_recognizer.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && uv run --project . pytest tests/test_brick_recognizer.py -k "detect_studs or fit_grid" -v`
Expected: FAIL — `ImportError: cannot import name 'detect_studs'`.

- [ ] **Step 3: Implement `detect_studs`, `fit_grid`, and `_count_clusters`**

In `src/services/brick_recognizer.py`, add:

```python
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
```

Add `detect_studs` and `fit_grid` to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && uv run --project . pytest tests/test_brick_recognizer.py -k "detect_studs or fit_grid" -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/services/brick_recognizer.py tests/test_brick_recognizer.py
git commit -m "feat(recognizer): stud blob detection + grid fitting"
```

---

### Task 7: `recognize_brick` orchestrator + `RecognitionResult`

**Files:**
- Modify: `src/services/brick_recognizer.py`
- Modify: `tests/test_brick_recognizer.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_brick_recognizer.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && uv run --project . pytest tests/test_brick_recognizer.py -k recognize_brick -v`
Expected: FAIL — `ImportError: cannot import name 'recognize_brick'`.

- [ ] **Step 3: Implement `RecognitionResult` + `recognize_brick`**

In `src/services/brick_recognizer.py`, add (after the imports, the dataclass; after the other functions, the orchestrator):

```python
@dataclass(frozen=True)
class RecognitionResult:
    """Outcome of :func:`recognize_brick`. ``ok`` gates the zero-measurement path."""

    ok: bool
    units_x: int | None = None
    units_y: int | None = None
    pitch_mm: float | None = None
    system: str | None = None
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "units_x": self.units_x,
            "units_y": self.units_y,
            "pitch_mm": self.pitch_mm,
            "system": self.system,
            "confidence": self.confidence,
            "warnings": list(self.warnings),
            "reason": self.reason,
        }
```

```python
def recognize_brick(
    *,
    rgb_bytes: bytes,
    depth_bytes: bytes,
    ar_metadata: dict[str, Any],
    kind: str,
    system_hint: str | None = None,
    pitch_tolerance_mm: float = DEFAULT_PITCH_TOLERANCE_MM,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> RecognitionResult:
    """Recognize a standard brick from a top-down RGB + depth + intrinsics.

    Returns a :class:`RecognitionResult`. ``ok`` is True only when a
    system is classified within tolerance AND confidence ≥
    ``min_confidence``. ``kind`` and ``system_hint`` are accepted for
    parity with the request contract; ``system_hint`` only contributes a
    warning here (the endpoint decides what to do with ``"unknown"``).
    """
    try:
        rgb = np.asarray(Image.open(io.BytesIO(rgb_bytes)).convert("RGB"))
    except Exception as exc:  # noqa: BLE001 — any decode failure is a soft "not ok"
        return RecognitionResult(ok=False, reason=f"cannot decode recognition_rgb: {exc}")

    centers = detect_studs(rgb)
    if centers.shape[0] < 2:
        return RecognitionResult(ok=False, reason=f"detected {centers.shape[0]} studs (need ≥ 2)")

    px_pitch = _median_nn_distance(centers)
    units_x, units_y = fit_grid(centers, px_pitch=px_pitch)

    try:
        depth = load_depth16_png(depth_bytes)
    except Exception as exc:  # noqa: BLE001
        return RecognitionResult(
            ok=False, units_x=units_x, units_y=units_y, reason=f"cannot decode depth: {exc}"
        )

    frame = ar_metadata.get("recognition_frame") if isinstance(ar_metadata, dict) else None
    intrinsics = frame.get("image_intrinsics") if isinstance(frame, dict) else None
    if not isinstance(intrinsics, dict) or not {"fx", "fy", "cx", "cy", "width", "height"} <= set(
        intrinsics
    ):
        return RecognitionResult(
            ok=False,
            units_x=units_x,
            units_y=units_y,
            reason="ar_metadata.recognition_frame.image_intrinsics missing/incomplete",
        )

    pitch_mm = metric_pitch(centers, depth, intrinsics)
    if pitch_mm is None:
        return RecognitionResult(
            ok=False, units_x=units_x, units_y=units_y, reason="no valid depth at stud centres"
        )

    system, confidence = classify_system(pitch_mm, pitch_tolerance_mm)
    warnings: list[str] = []
    if system is not None:
        canonical = SYSTEM_UNIT_MM[system]
        warnings.append(
            f"measured pitch {pitch_mm:.2f}mm vs {system} canonical {canonical}mm "
            f"(Δ{abs(pitch_mm - canonical):.2f})"
        )
    if system_hint and system_hint not in ("", "unknown") and system and system_hint != system:
        warnings.append(f"system_hint={system_hint!r} but classified {system!r}")

    ok = system is not None and confidence >= min_confidence
    return RecognitionResult(
        ok=ok,
        units_x=units_x,
        units_y=units_y,
        pitch_mm=round(pitch_mm, 3),
        system=system,
        confidence=round(confidence, 3),
        warnings=warnings,
        reason=None if ok else "pitch did not match a known system within tolerance/confidence",
    )
```

Add `RecognitionResult` and `recognize_brick` to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && uv run --project . pytest tests/test_brick_recognizer.py -v`
Expected: all tests pass (Tasks 3–7).

- [ ] **Step 5: Commit**

```bash
git add src/services/brick_recognizer.py tests/test_brick_recognizer.py
git commit -m "feat(recognizer): recognize_brick orchestrator + RecognitionResult"
```

---

### Task 8: Response schemas (`ArCaptureRead` + nested)

**Files:**
- Modify: `src/models/schemas.py` (add after `ParametricBlockRead`, before the Asset section at line 181)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_brick_recognizer.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && uv run --project . pytest tests/test_brick_recognizer.py -k ar_capture_read -v`
Expected: FAIL — `ImportError: cannot import name 'ArCaptureRead'`.

- [ ] **Step 3: Add the schemas**

In `src/models/schemas.py`, after `ParametricBlockRead` (ends line 178) and before the `# Asset` section (line 181), add:

```python
# ---------------------------------------------------------------------------
# AR capture recognition (v0.5)
# ---------------------------------------------------------------------------
class RecognizedBlock(BaseModel):
    """The recognizer's classification of an AR capture."""

    model_config = ConfigDict(extra="forbid")

    system: str | None = None
    kind: str
    units_x: int | None = None
    units_y: int | None = None
    pitch_mm: float | None = None
    confidence: float = 0.0


class NeedsMeasurement(BaseModel):
    """Returned when recognition confidence is too low — caliper fallback."""

    model_config = ConfigDict(extra="forbid")

    #: The 5 caliper fields the client should collect (same as
    #: ``/parametric-blocks``).
    fields: list[str] = Field(default_factory=list)
    #: Human-readable, caliper-friendly guidance ("卡两端外边缘…").
    guidance: str
    #: Where to submit the measured fallback.
    endpoint: str = "/api/v1/parametric-blocks"
    #: Why recognition fell back (for logs / UI).
    reason: str | None = None


class ArCaptureRead(BaseModel):
    """Response shape for ``POST /api/v1/ar-captures``."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    capture_id: UUID
    part_id: str
    #: ``"recognized"`` (a GLB job was dispatched) or
    #: ``"needs_measurement"`` (no job; client falls back to caliper).
    status: Literal["recognized", "needs_measurement"]
    mode: str = "ar_recognized"
    recognized: RecognizedBlock
    needs_measurement: NeedsMeasurement | None = None
    job_id: UUID | None = None
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime
```

Add `"ArCaptureRead"`, `"NeedsMeasurement"`, `"RecognizedBlock"` to the `__all__` list (keep it alphabetical with the rest).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && uv run --project . pytest tests/test_brick_recognizer.py -k ar_capture_read -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/models/schemas.py tests/test_brick_recognizer.py
git commit -m "feat(schemas): ArCaptureRead + RecognizedBlock + NeedsMeasurement"
```

---

### Task 9: Worker `ar_recognized` branch (`_run_ar_recognized_pipeline`)

**Files:**
- Modify: `src/workers/tasks/reconstruct.py` (dispatch at lines 246-250; add new function after `_run_parametric_pipeline` / before `_opt_float` at line 470)
- Create: `tests/test_ar_recognized_worker.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_ar_recognized_worker.py`:

```python
"""Worker-level tests for the ar_recognized reconstruction branch."""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest


@pytest.fixture
def work_in_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("BLOCKTOOL_RECON_ROOT", str(tmp_path))
    yield tmp_path


def test_run_ar_recognized_pipeline_builds_canonical_glb(work_in_tmp: Path) -> None:
    """Insert an ar_recognized capture+job directly, run the pipeline,
    assert a canonical FEILE 2x4 GLB asset with pipeline_used=ar_recognized."""
    from db.models import Asset, Capture, Job
    from db.session import async_session_factory
    from workers.tasks.reconstruct import _run_ar_recognized_pipeline

    capture_id = uuid.uuid4()
    job_id = uuid.uuid4()

    async def _seed() -> None:
        f = async_session_factory()
        async with f() as session:
            session.add(
                Capture(
                    id=capture_id, part_id="feile-brick-2x4", status="pending",
                    image_count=0, image_keys=[], capture_mode="phone_walkaround",
                    mode="ar_recognized", system="feile", kind="brick",
                    units_x=2, units_y=4,
                    recognition_result={"ok": True, "system": "feile", "pitch_mm": 16.1,
                                        "confidence": 0.96, "units_x": 2, "units_y": 4,
                                        "warnings": [], "reason": None},
                )
            )
            session.add(Job(id=job_id, capture_id=capture_id, kind="reconstruct",
                            status="pending", progress=0, stage="queued"))
            await session.commit()

    asyncio.run(_seed())

    class _FakeTask:
        def update_state(self, **_: object) -> None:  # noqa: D401
            pass

    result = _run_ar_recognized_pipeline(_FakeTask(), capture_id, job_id)
    assert result["status"] == "completed"

    async def _asset() -> Asset | None:
        f = async_session_factory()
        async with f() as session:
            from sqlalchemy import select
            return (await session.execute(select(Asset).where(Asset.job_id == job_id))).scalar_one_or_none()

    asset = asyncio.run(_asset())
    assert asset is not None
    assert asset.meta["pipeline_used"] == "ar_recognized"
    assert asset.meta["system"] == "feile"
    assert asset.meta["units_x"] == 2 and asset.meta["units_y"] == 4
    # FEILE 2x4 brick: width=2*16=32 → bbox_max.x=16; depth=4*16=64 → bbox_max.y=32.
    assert abs(asset.meta["bbox_max"][0] - 16.0) < 0.5, asset.meta["bbox_max"]
    assert abs(asset.meta["bbox_max"][1] - 32.0) < 0.5, asset.meta["bbox_max"]
    # recognition_result echoed into asset meta.
    assert asset.meta["recognition_result"]["system"] == "feile"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && uv run --project . pytest tests/test_ar_recognized_worker.py -v`
Expected: FAIL — `ImportError: cannot import name '_run_ar_recognized_pipeline'`.

- [ ] **Step 3: Add the dispatch branch**

In `src/workers/tasks/reconstruct.py`, change the dispatch block (lines 246-250) to:

```python
        mode = _run_async(_get_capture_mode(capture_uuid))
        if mode == "parametric_block":
            result = _run_parametric_pipeline(self, capture_uuid, job_id)
        elif mode == "ar_recognized":
            result = _run_ar_recognized_pipeline(self, capture_uuid, job_id)
        else:
            result = _run_pipeline(self, capture_uuid, job_id)
```

- [ ] **Step 4: Add `_run_ar_recognized_pipeline`**

In `src/workers/tasks/reconstruct.py`, add this function immediately after `_run_parametric_pipeline` (i.e. after line 467, before `_opt_float`):

```python
def _run_ar_recognized_pipeline(
    self: Any,
    capture_uuid: uuid.UUID,
    job_id: uuid.UUID,
) -> dict[str, Any]:
    """AR-recognized path: canonical ``BlockSpec`` → ``export_glb`` → upload.

    The route layer already ran recognition synchronously and stored
    ``system`` / ``kind`` / ``units_x`` / ``units_y`` (+ the audit blob
    in ``recognition_result``) on the capture row. This pipeline builds
    the *canonical* spec for that system/kind/units — NO measurement
    overrides, since a recognized standard part is fully described by its
    public spec — and emits the GLB. Mirrors
    :func:`_run_parametric_pipeline`, minus the derived-measurement
    overrides.
    """
    work_dir = _recon_root() / str(capture_uuid)
    output_dir = work_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "input").mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    _emit_progress(self, job_id, progress=10, stage="building_spec", eta_seconds=None)

    async def _load_capture() -> Capture:
        f = async_session_factory()
        async with f() as session:
            cap = await session.get(Capture, capture_uuid)
            if cap is None:
                raise RuntimeError(
                    f"capture {capture_uuid} not in DB "
                    "(should have been created by the route layer)"
                )
            session.expunge(cap)
            return cap

    capture = asyncio.run(_load_capture())
    if capture.mode != "ar_recognized":
        raise RuntimeError(
            f"_run_ar_recognized_pipeline called for capture {capture_uuid} "
            f"with mode={capture.mode!r}"
        )

    spec = BlockSpec(
        system=capture.system or "duplo",
        kind=capture.kind or "brick",
        units_x=int(capture.units_x or 2),
        units_y=int(capture.units_y or 2),
    )

    _emit_progress(
        self, job_id, progress=40, stage="generating_mesh", eta_seconds=_eta_seconds(started, 40)
    )

    glb_path = output_dir / "mesh.glb"
    result = export_glb(spec, glb_path)
    result["pipeline_used"] = "ar_recognized"
    result["pipeline_version"] = "brick_recognizer-0.1.0"
    result["blender_used"] = None

    elapsed = time.monotonic() - started
    logger.info(
        "reconstruct.ar_recognized: job %s system=%s kind=%s units=%dx%d elapsed=%.2fs",
        job_id, spec.system, spec.kind, spec.units_x, spec.units_y, elapsed,
    )

    return _finalize_mesh_pipeline(
        self,
        capture_uuid=capture_uuid,
        job_id=job_id,
        work_dir=work_dir,
        output_dir=output_dir,
        result=result,
        started=started,
        pipeline_used="ar_recognized",
        n_images=0,
        extra_meta={
            "system": spec.system,
            "kind": spec.kind,
            "units_x": spec.units_x,
            "units_y": spec.units_y,
            "recognition_result": capture.recognition_result,
        },
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd apps/api && uv run --project . pytest tests/test_ar_recognized_worker.py -v`
Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add src/workers/tasks/reconstruct.py tests/test_ar_recognized_worker.py
git commit -m "feat(worker): ar_recognized branch builds canonical parametric GLB"
```

---

### Task 10: `POST /api/v1/ar-captures` endpoint + router registration

**Files:**
- Create: `src/api/v1/ar_captures.py`
- Modify: `src/api/v1/__init__.py` (register the router)
- Create: `tests/fixtures/synth_studs.py` (shared bundle builder for endpoint + e2e tests)
- Create: `tests/test_ar_captures.py`

- [ ] **Step 1: Create the shared synthetic-bundle fixture helper**

Create `tests/fixtures/synth_studs.py`:

```python
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
```

- [ ] **Step 2: Write the failing endpoint tests**

Create `tests/test_ar_captures.py`:

```python
"""Endpoint tests for POST /api/v1/ar-captures."""
from __future__ import annotations

import json
import struct
import zlib
from collections.abc import AsyncIterator

from fixtures.synth_studs import make_ar_bundle


def _png(width: int = 4, height: int = 4, color: bytes = b"\xff\x80\x00") -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + color * width for _ in range(height))
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def _files(rgb: bytes, depth: bytes, n_images: int = 4):
    files = [
        ("recognition_rgb", ("rgb.png", rgb, "image/png")),
        ("recognition_depth", ("depth.png", depth, "image/png")),
    ]
    files += [("images", (f"{i}.png", _png(), "image/png")) for i in range(n_images)]
    return files


async def test_ar_capture_recognized_feile(app_client: AsyncIterator) -> None:
    rgb, depth, meta = make_ar_bundle("feile", 2, 4)
    resp = await app_client.post(
        "/api/v1/ar-captures",
        data={"kind": "brick", "ar_metadata": json.dumps(meta)},
        files=_files(rgb, depth),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "recognized"
    assert body["mode"] == "ar_recognized"
    assert body["recognized"]["system"] == "feile"
    assert body["recognized"]["units_x"] == 2
    assert body["recognized"]["units_y"] == 4
    assert body["job_id"] is not None
    assert body["needs_measurement"] is None


async def test_ar_capture_unknown_hint_needs_measurement(app_client: AsyncIterator) -> None:
    rgb, depth, meta = make_ar_bundle("feile", 2, 2)
    resp = await app_client.post(
        "/api/v1/ar-captures",
        data={"kind": "brick", "ar_metadata": json.dumps(meta), "system_hint": "unknown"},
        files=_files(rgb, depth),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "needs_measurement"
    assert body["job_id"] is None
    assert body["needs_measurement"]["endpoint"] == "/api/v1/parametric-blocks"
    assert "outer_pitch_mm" in body["needs_measurement"]["fields"]


async def test_ar_capture_rejects_missing_depth(app_client: AsyncIterator) -> None:
    rgb, _depth, meta = make_ar_bundle("feile")
    resp = await app_client.post(
        "/api/v1/ar-captures",
        data={"kind": "brick", "ar_metadata": json.dumps(meta)},
        files=[("recognition_rgb", ("rgb.png", rgb, "image/png"))]
        + [("images", (f"{i}.png", _png(), "image/png")) for i in range(4)],
    )
    assert resp.status_code == 422, resp.text


async def test_ar_capture_rejects_bad_metadata_json(app_client: AsyncIterator) -> None:
    rgb, depth, _meta = make_ar_bundle("feile")
    resp = await app_client.post(
        "/api/v1/ar-captures",
        data={"kind": "brick", "ar_metadata": "not-json"},
        files=_files(rgb, depth),
    )
    assert resp.status_code == 422, resp.text


async def test_ar_capture_rejects_too_few_images(app_client: AsyncIterator) -> None:
    rgb, depth, meta = make_ar_bundle("feile")
    resp = await app_client.post(
        "/api/v1/ar-captures",
        data={"kind": "brick", "ar_metadata": json.dumps(meta)},
        files=_files(rgb, depth, n_images=2),
    )
    assert resp.status_code == 422, resp.text
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd apps/api && uv run --project . pytest tests/test_ar_captures.py -v`
Expected: FAIL — 404 (route not registered) / `ModuleNotFoundError: No module named 'fixtures.synth_studs'`.

- [ ] **Step 4: Create the endpoint**

Create `src/api/v1/ar_captures.py`:

```python
"""``/api/v1/ar-captures`` — ARCore capture upload + synchronous recognition.

The ARCore client uploads a top-down stud frame (RGB + 16-bit depth +
camera intrinsics in ``ar_metadata``) plus a few angle photos. This
endpoint runs :func:`services.brick_recognizer.recognize_brick`
*synchronously* (fast: PIL decode + blob detection + pinhole math) and:

* **recognized** (system classified within tolerance & confidence): persists
  a ``Capture(mode="ar_recognized")`` with system/kind/units, dispatches the
  existing ``reconstruct`` task (which builds the canonical GLB via the
  ``ar_recognized`` worker branch), and returns ``status="recognized"``.
* **needs_measurement** (low confidence, or ``system_hint="unknown"``):
  persists the capture for audit, does NOT dispatch a job, and returns the
  5 caliper fields + guidance so the client falls back to
  ``POST /parametric-blocks``.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, UploadFile, status
from fastapi.responses import JSONResponse

from app.config import settings
from app.deps import DBSessionDep
from core.errors import CaptureInvalid
from db.models import Capture, Job
from models.schemas import ArCaptureRead, NeedsMeasurement, RecognizedBlock
from services.brick_recognizer import recognize_brick
from storage.minio_client import raw_object_key, storage
from workers.tasks.reconstruct import reconstruct as reconstruct_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ar-captures", tags=["ar-captures"])

MIN_IMAGES = 4
MAX_IMAGES = 20
ALLOWED_CONTENT_TYPES = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
}

Kind = Literal["brick", "plate", "tile", "slope"]
SystemHint = Literal["lego", "duplo", "feile", "generic", "unknown"]

#: The 5 caliper fields the fallback collects (identical to /parametric-blocks).
_MEASUREMENT_FIELDS = [
    "outer_pitch_mm",
    "inner_pitch_mm",
    "stud_diameter_mm",
    "brick_height_net_mm",
    "brick_height_total_mm",
]
_GUIDANCE = (
    "无法自动识别。请用游标卡尺测 5 个值并提交到 /api/v1/parametric-blocks："
    "1A=卡两端最外侧凸点外缘的总跨距 (outer_pitch_mm)；"
    "1B=相邻两凸点之间最窄缝隙 (inner_pitch_mm)；"
    "③=任一凸点外径 (stud_diameter_mm)；"
    "②=底面到砖体顶面(不含凸点)的净高 (brick_height_net_mm)；"
    "④=底面到凸点顶的总高 (brick_height_total_mm)。卡‘可夹的实边’，不要找凸点中心。"
)


def _bucket_raw() -> str:
    from app.config import settings as _s

    return _s.s3_bucket_raw


@router.post(
    "",
    response_model=ArCaptureRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload an ARCore capture (top-down stud frame + depth + photos) and recognize it",
    responses={
        201: {"description": "Capture created; recognized → GLB job dispatched, else needs_measurement."},
        422: {"description": "Validation failed (bad image count, bad ar_metadata JSON, empty frame)."},
    },
)
async def create_ar_capture(
    session: DBSessionDep,
    kind: Annotated[Kind, Form()],
    recognition_rgb: Annotated[UploadFile, File(description="Top-down RGB of the stud face.")],
    recognition_depth: Annotated[UploadFile, File(description="DEPTH16 of the same frame as a 16-bit PNG.")],
    ar_metadata: Annotated[str, Form(description="JSON: intrinsics, depth dims, pose, distance, coarse hints.")],
    images: Annotated[
        list[UploadFile],
        File(description=f"{MIN_IMAGES}-{MAX_IMAGES} angle photos (compat with photo path / future)."),
    ] = [],  # noqa: B006 — FastAPI's Annotated/File idiom for a file list
    system_hint: Annotated[SystemHint | None, Form()] = None,
    part_id: Annotated[str | None, Form(max_length=64)] = None,
) -> JSONResponse:
    # ---- 1. Validate image count ----------------------------------------
    imgs = images or []
    if len(imgs) < MIN_IMAGES or len(imgs) > MAX_IMAGES:
        raise CaptureInvalid(
            f"need {MIN_IMAGES} <= image_count <= {MAX_IMAGES}, got {len(imgs)}",
            details={"image_count": len(imgs), "min": MIN_IMAGES, "max": MAX_IMAGES},
        )

    # ---- 2. Parse ar_metadata -------------------------------------------
    try:
        meta = json.loads(ar_metadata)
    except json.JSONDecodeError as exc:
        raise CaptureInvalid(
            f"ar_metadata is not valid JSON: {exc.msg} (line {exc.lineno})",
            details={"line": exc.lineno, "column": exc.colno},
        ) from exc
    if not isinstance(meta, dict):
        raise CaptureInvalid("ar_metadata must be a JSON object", details={"got_type": type(meta).__name__})

    # ---- 3. Read recognition frame bytes --------------------------------
    rgb_bytes = await recognition_rgb.read()
    depth_bytes = await recognition_depth.read()
    await recognition_rgb.close()
    await recognition_depth.close()
    if not rgb_bytes:
        raise CaptureInvalid("recognition_rgb is empty")
    if not depth_bytes:
        raise CaptureInvalid("recognition_depth is empty")

    # ---- 4. Recognize (synchronous, fast) -------------------------------
    result = recognize_brick(
        rgb_bytes=rgb_bytes,
        depth_bytes=depth_bytes,
        ar_metadata=meta,
        kind=kind,
        system_hint=system_hint,
        pitch_tolerance_mm=settings.ar_pitch_tolerance_mm,
        min_confidence=settings.ar_min_confidence,
    )
    recognized = result.ok and system_hint != "unknown"

    # ---- 5. part_id default ---------------------------------------------
    capture_id = uuid.uuid4()
    if not part_id:
        part_id = (
            f"{result.system}-{kind}-{result.units_x}x{result.units_y}"
            if recognized
            else f"ar-{kind}-{str(capture_id)[:8]}"
        )

    # ---- 6. Upload recognition frame + angle photos to MinIO ------------
    rgb_key = raw_object_key(str(capture_id), "recognition_rgb.png")
    storage.put_object(_bucket_raw(), rgb_key, rgb_bytes, content_type=recognition_rgb.content_type or "image/png")
    depth_key = raw_object_key(str(capture_id), "recognition_depth.png")
    storage.put_object(_bucket_raw(), depth_key, depth_bytes, content_type="image/png")
    keys: list[str] = []
    for idx, upload in enumerate(imgs):
        ext = ALLOWED_CONTENT_TYPES.get((upload.content_type or "").lower(), "bin")
        body = await upload.read()
        if not body:
            raise CaptureInvalid(f"image #{idx} is empty", details={"index": idx})
        key = raw_object_key(str(capture_id), f"{idx:03d}.{ext}")
        storage.put_object(_bucket_raw(), key, body, content_type=upload.content_type or "application/octet-stream")
        keys.append(key)
        await upload.close()

    # ---- 7. Persist the capture row -------------------------------------
    capture = Capture(
        id=capture_id,
        part_id=part_id,
        status="pending",
        image_count=len(keys),
        image_keys=keys,
        capture_mode="phone_walkaround",
        mode="ar_recognized",
        system=result.system if recognized else None,
        kind=kind,
        units_x=result.units_x if recognized else None,
        units_y=result.units_y if recognized else None,
        ar_metadata=meta,
        recognition_result=result.to_dict(),
    )
    session.add(capture)

    # ---- 8. Dispatch a GLB job only when recognized ---------------------
    job_id: uuid.UUID | None = None
    if recognized:
        job = Job(capture_id=capture_id, kind="reconstruct", status="pending", progress=0, stage="queued")
        session.add(job)
        await session.flush()  # populate job.id
        async_result = reconstruct_task.apply_async(args=[str(capture_id)], task_id=str(job.id))
        job.celery_task_id = async_result.id
        job_id = job.id

    await session.commit()
    await session.refresh(capture)

    logger.info(
        "ar_captures.create: capture_id=%s part_id=%s recognized=%s system=%s units=%sx%s "
        "pitch=%s conf=%s job_id=%s",
        capture_id, part_id, recognized, result.system, result.units_x, result.units_y,
        result.pitch_mm, result.confidence, job_id,
    )

    body = ArCaptureRead(
        capture_id=capture.id,
        part_id=capture.part_id,
        status="recognized" if recognized else "needs_measurement",
        recognized=RecognizedBlock(
            system=result.system,
            kind=kind,
            units_x=result.units_x,
            units_y=result.units_y,
            pitch_mm=result.pitch_mm,
            confidence=result.confidence,
        ),
        needs_measurement=None
        if recognized
        else NeedsMeasurement(fields=_MEASUREMENT_FIELDS, guidance=_GUIDANCE, reason=result.reason),
        job_id=job_id,
        warnings=result.warnings,
        created_at=capture.created_at,
    )
    return JSONResponse(status_code=status.HTTP_201_CREATED, content=body.model_dump(mode="json"))


__all__ = ["router"]
```

- [ ] **Step 5: Register the router**

In `src/api/v1/__init__.py`, add `ar_captures` to the import (line 5) and include it after `parametric_blocks` (line 15):

```python
from api.v1 import ar_captures, assets, captures, health, jobs, parametric_blocks
```
and, after `api_v1_router.include_router(parametric_blocks.router)`:
```python
# v0.5 AR-capture recognition endpoint. Shares the Capture row +
# reconstruct task, disambiguated by capture.mode == "ar_recognized".
api_v1_router.include_router(ar_captures.router)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd apps/api && uv run --project . pytest tests/test_ar_captures.py -v`
Expected: 5 passed.

- [ ] **Step 7: Commit**

```bash
git add src/api/v1/ar_captures.py src/api/v1/__init__.py tests/fixtures/synth_studs.py tests/test_ar_captures.py
git commit -m "feat(api): POST /ar-captures with synchronous recognition + caliper fallback"
```

---

### Task 11: Offline end-to-end (POST → worker → GLB asset)

**Files:**
- Modify: `tests/test_ar_captures.py` (append the e2e test)

- [ ] **Step 1: Write the failing e2e test**

Append to `tests/test_ar_captures.py`:

```python
import asyncio
import tempfile
import uuid
from pathlib import Path

import pytest
import trimesh


@pytest.fixture
def work_in_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("BLOCKTOOL_RECON_ROOT", str(tmp_path))
    yield tmp_path


def test_ar_capture_end_to_end(work_in_tmp: Path, app_client) -> None:
    """POST a recognized FEILE 2x4 bundle → run the worker in-process →
    assert a canonical GLB asset with pipeline_used == 'ar_recognized'."""
    from app.config import settings
    from db.models import Asset, Job
    from db.session import async_session_factory
    from workers.tasks.reconstruct import reconstruct

    rgb, depth, meta = make_ar_bundle("feile", 2, 4)
    resp = asyncio.get_event_loop().run_until_complete(
        app_client.post(
            "/api/v1/ar-captures",
            data={"kind": "brick", "ar_metadata": json.dumps(meta)},
            files=_files(rgb, depth),
        )
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "recognized"
    capture_id = body["capture_id"]
    job_id = body["job_id"]

    result = reconstruct.apply(args=[capture_id])
    assert result.successful() or result.state == "SUCCESS", result.state

    async def _state() -> tuple[Job | None, list[Asset]]:
        f = async_session_factory()
        async with f() as session:
            from sqlalchemy import select
            from sqlalchemy.orm import selectinload

            stmt = select(Job).where(Job.id == uuid.UUID(job_id)).options(selectinload(Job.assets))
            job = (await session.execute(stmt)).scalar_one_or_none()
            return job, list(job.assets) if job else []

    job, assets = asyncio.run(_state())
    assert job is not None and job.status == "completed", job
    assert len(assets) == 1
    meta_out = assets[0].meta
    assert meta_out["pipeline_used"] == "ar_recognized"
    assert meta_out["system"] == "feile"
    assert meta_out["units_x"] == 2 and meta_out["units_y"] == 4
    assert meta_out["input_image_count"] == 0
    # FEILE 2x4: bbox_max.x = 16mm, bbox_max.y = 32mm.
    assert abs(meta_out["bbox_max"][0] - 16.0) < 0.5, meta_out["bbox_max"]
    assert abs(meta_out["bbox_max"][1] - 32.0) < 0.5, meta_out["bbox_max"]

    # GLB is a real binary glTF in MinIO.
    with tempfile.NamedTemporaryFile(suffix=".glb", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        from minio import Minio

        client = Minio(
            endpoint=settings.s3_endpoint.split("://", 1)[-1],
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            secure=settings.s3_secure,
        )
        client.fget_object(settings.s3_bucket_recon, assets[0].storage_key, str(tmp_path))
        with tmp_path.open("rb") as f:
            assert f.read(4) == b"glTF"
        loaded = trimesh.load(str(tmp_path), force="mesh")
        assert isinstance(loaded, trimesh.Trimesh)
        assert len(loaded.vertices) > 0
    finally:
        tmp_path.unlink(missing_ok=True)
```

- [ ] **Step 2: Run the e2e to verify it passes**

Run: `cd apps/api && uv run --project . pytest tests/test_ar_captures.py::test_ar_capture_end_to_end -v`
Expected: PASS (this is the no-phone backend closed loop the spec calls for).

- [ ] **Step 3: Commit**

```bash
git add tests/test_ar_captures.py
git commit -m "test(e2e): offline ar-capture closed loop (POST → worker → GLB asset)"
```

---

### Task 12: Full-suite regression + lint

**Files:** none (verification only)

- [ ] **Step 1: Run the new + adjacent suites**

Run:
```bash
cd apps/api && uv run --project . pytest tests/test_brick_recognizer.py tests/test_ar_recognized_worker.py tests/test_ar_captures.py -v
```
Expected: all pass.

- [ ] **Step 2: Run the existing photo + parametric regressions (must be untouched)**

Run:
```bash
cd apps/api && uv run --project . pytest tests/test_reconstruction_parametric.py tests/test_reconstruction_routing.py tests/test_captures.py tests/test_jobs.py -v
```
Expected: all pass (no regressions from the dispatch edit or model/migration changes).

- [ ] **Step 3: Lint the new modules**

Run:
```bash
cd apps/api && uv run --project . ruff check src/services/brick_recognizer.py src/api/v1/ar_captures.py tests/test_ar_captures.py tests/test_brick_recognizer.py
```
Expected: no errors (fix any ruff findings inline, then re-run).

- [ ] **Step 4: Full suite (final gate)**

Run:
```bash
cd apps/api && uv run --project . pytest -q
```
Expected: the whole backend suite passes (pre-existing skips/xfails unchanged).

- [ ] **Step 5: Commit any lint fixes**

```bash
git add -A
git commit -m "chore: lint fixes for ar-capture recognition path"
```

---

## Self-Review

**Spec coverage (against `docs/ar-capture-recognition-design.md`):**
- §1 new endpoint + `ar_recognized` mode → Tasks 1, 10.
- §1/§4 `brick_recognizer` (`detect_studs` / `metric_pitch` / `classify_system`) → Tasks 3, 5, 6, 7.
- §1/§7 fit gate + `needs_measurement` fallback reusing `/parametric-blocks` → Task 10 (gate + `NeedsMeasurement`).
- §5 worker `_run_ar_recognized_pipeline` reusing `BlockGenerator`/`export_glb` → Task 9.
- §5 DB `ar_metadata`/`recognition_result` + reuse system/kind/units + alembic → Task 1.
- §6 upload contract (multipart + `ar_metadata` JSON + 16-bit depth PNG) → Task 10 endpoint + Task 4 depth codec.
- §8 pytest pure functions (synthetic) + offline fixture e2e + photo/parametric regression → Tasks 3–7 (units), 11 (e2e), 12 (regression).
- §9 config `ar_pitch_tolerance_mm`/`ar_min_confidence` → Task 2.

**Resolved spec tension:** the spec §3 returns `recognized`/`needs_measurement` synchronously in the POST, while §5 mentions recognition "in the worker." This plan runs recognition **synchronously in the endpoint** (fast, no SfM) and keeps the worker responsible only for canonical GLB generation — the only way to satisfy the synchronous response contract. Noted here intentionally.

**Placeholder scan:** no TBD/TODO; every code step is complete. The one acknowledged limitation (`detect_studs` robustness on messy real photos) is explicit in the module docstring and tested only against clean synthetic frames — by design for this cut.

**Type/name consistency:** `RecognitionResult` fields (`ok/units_x/units_y/pitch_mm/system/confidence/warnings/reason`) are produced in Task 7 and consumed in Tasks 9 (`recognition_result` dict), 10 (endpoint). `ArCaptureRead`/`RecognizedBlock`/`NeedsMeasurement` defined in Task 8, used in Task 10. `metric_pitch`/`detect_studs`/`fit_grid`/`classify_system`/`encode_depth16_png`/`load_depth16_png`/`recognize_brick` signatures match between definitions (Tasks 3–7) and call sites (Tasks 10, 11, tests). Worker `_run_ar_recognized_pipeline` mirrors `_run_parametric_pipeline`'s `_finalize_mesh_pipeline(...)` keyword contract verified against `reconstruct.py:480-492`.
