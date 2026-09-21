"""Offline unit tests for sizing math and tranche fractions."""

from __future__ import annotations

import math

import pytest

from sizing import (
    ATR_STOP_MULT,
    SCALE_OUT_FRACTION,
    TARGET_1_R,
    size_position,
    tranche_one_shares,
)


def test_r_stop_and_t1_math() -> None:
    entry, atr, risk = 100.0, 2.0, 300.0
    size = size_position(entry, atr, risk)
    assert size is not None
    assert size.r == ATR_STOP_MULT * atr == 3.0
    assert size.stop == entry - size.r == 97.0
    assert size.target_1 == entry + TARGET_1_R * size.r == 104.5
    assert size.shares == math.floor(risk / size.r) == 100
    assert size.t1_shares == tranche_one_shares(100)
    assert size.runner_shares == size.shares - size.t1_shares


def test_max_limit_price_is_entry_plus_15pct_atr() -> None:
    size = size_position(100.0, 2.0, 300.0)
    assert size is not None
    assert size.max_limit_price == pytest.approx(100.0 + 0.15 * 2.0)


def test_tranche_one_shares_uses_one_third() -> None:
    assert SCALE_OUT_FRACTION == pytest.approx(1.0 / 3.0)
    assert tranche_one_shares(9) == 3
    assert tranche_one_shares(10) == math.ceil(10 / 3) == 4
    assert tranche_one_shares(1) == 1
    assert tranche_one_shares(0) == 0
    assert tranche_one_shares(-5) == 0


def test_rejects_fewer_than_three_shares() -> None:
    # r = 1.5 * 10 = 15; floor(20/15) = 1 < 3
    assert size_position(100.0, 10.0, 20.0) is None
    # Exactly 2 shares still rejected
    assert size_position(100.0, 1.0, 3.0) is None  # r=1.5, floor(3/1.5)=2
    # Bare minimum 3 shares accepted
    size = size_position(100.0, 1.0, 4.5)  # floor(4.5/1.5)=3
    assert size is not None
    assert size.shares == 3
    assert size.t1_shares == 1
    assert size.runner_shares == 2


def test_invalid_inputs_return_none() -> None:
    assert size_position(0.0, 2.0, 100.0) is None
    assert size_position(100.0, 0.0, 100.0) is None
    assert size_position(100.0, 2.0, 0.0) is None
