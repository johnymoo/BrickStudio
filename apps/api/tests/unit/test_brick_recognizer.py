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
