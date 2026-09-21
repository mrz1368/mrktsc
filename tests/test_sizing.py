"""Offline unit tests for sizing math and tranche fractions."""

from __future__ import annotations

import math

import pytest

from sizing import (
    ATR_STOP_MULT,
    SCALE_OUT_FRACTION,
    TARGET_1_R,
    estimate_tau_i,
    size_inverse_from_underlying,
    size_position,
    slippage_destroys_edge,
    stops_after_pending_fill,
    tranche_one_shares,
)

# Liquid mega-cap-ish defaults that must not trip the slippage veto.
_LIQUID = dict(addv=100_000_000.0, hist_vol=0.01)


def test_r_stop_and_t1_math() -> None:
    entry, atr, risk = 100.0, 2.0, 300.0
    size = size_position(entry, atr, risk, **_LIQUID)
    assert size is not None
    assert size.r == ATR_STOP_MULT * atr == 3.0
    assert size.stop == entry - size.r == 97.0
    assert size.target_1 == entry + TARGET_1_R * size.r == 104.5
    assert size.shares == math.floor(risk / size.r) == 100
    assert size.t1_shares == tranche_one_shares(100)
    assert size.runner_shares == size.shares - size.t1_shares


def test_max_limit_price_is_entry_plus_15pct_atr() -> None:
    size = size_position(100.0, 2.0, 300.0, **_LIQUID)
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
    assert size_position(100.0, 10.0, 20.0, **_LIQUID) is None
    # Exactly 2 shares still rejected
    assert size_position(100.0, 1.0, 3.0, **_LIQUID) is None  # r=1.5, floor(3/1.5)=2
    # Bare minimum 3 shares accepted
    size = size_position(100.0, 1.0, 4.5, **_LIQUID)  # floor(4.5/1.5)=3
    assert size is not None
    assert size.shares == 3
    assert size.t1_shares == 1
    assert size.runner_shares == 2


def test_invalid_inputs_return_none() -> None:
    assert size_position(0.0, 2.0, 100.0, **_LIQUID) is None
    assert size_position(100.0, 0.0, 100.0, **_LIQUID) is None
    assert size_position(100.0, 2.0, 0.0, **_LIQUID) is None


def test_stops_after_pending_fill_preserves_r_distance() -> None:
    # Signal entry 100, stop 97 → R = 3; fill at 102 → stop 99.
    initial, current = stops_after_pending_fill(
        fill_price=102.0,
        old_entry=100.0,
        old_initial_stop=97.0,
    )
    assert initial == pytest.approx(99.0)
    assert current == pytest.approx(99.0)
    assert initial == current


def test_stops_after_pending_fill_gap_down() -> None:
    initial, current = stops_after_pending_fill(
        fill_price=98.0,
        old_entry=100.0,
        old_initial_stop=97.0,
    )
    assert initial == pytest.approx(95.0)
    assert current == pytest.approx(95.0)


def test_stops_after_pending_fill_clamps_negative_r() -> None:
    # Stop already above entry → R clamped to 0; stop equals fill.
    initial, current = stops_after_pending_fill(
        fill_price=105.0,
        old_entry=100.0,
        old_initial_stop=101.0,
    )
    assert initial == pytest.approx(105.0)
    assert current == pytest.approx(105.0)


def test_inverse_atr_scales_underlying_risk_by_leverage() -> None:
    # stop pct = 1.5 * 2 / 100 = 3%; 2x leverage → 6% of the inverse.
    # inv ATR = 50 * 0.06 / 1.5 = 2.0, same ticket as sizing the inverse directly.
    scaled = size_inverse_from_underlying(
        underlying_close=100.0,
        underlying_atr=2.0,
        inverse_close=50.0,
        leverage_factor=2.0,
        risk_cad=300.0,
        **_LIQUID,
    )
    assert scaled == size_position(50.0, 2.0, 300.0, **_LIQUID)

    half = size_inverse_from_underlying(
        underlying_close=100.0,
        underlying_atr=2.0,
        inverse_close=50.0,
        leverage_factor=1.0,
        risk_cad=300.0,
        **_LIQUID,
    )
    assert half == size_position(50.0, 1.0, 300.0, **_LIQUID)
    assert half is not None
    assert half.atr == pytest.approx(1.0)

    assert (
        size_inverse_from_underlying(
            underlying_close=0.0,
            underlying_atr=2.0,
            inverse_close=50.0,
            leverage_factor=2.0,
            risk_cad=300.0,
            **_LIQUID,
        )
        is None
    )


def test_tau_monotone_in_vol_and_addv() -> None:
    base = estimate_tau_i(100_000_000.0, 0.01)
    higher_vol = estimate_tau_i(100_000_000.0, 0.02)
    lower_addv = estimate_tau_i(50_000_000.0, 0.01)
    assert higher_vol > base
    assert lower_addv > base
    # Zero / negative ADDV clamps to 1.0 — no div-by-zero, finite tau.
    assert math.isfinite(estimate_tau_i(0.0, 0.01))
    assert estimate_tau_i(0.0, 0.01) == pytest.approx(estimate_tau_i(1.0, 0.01))
    assert estimate_tau_i(-5.0, 0.01) == pytest.approx(estimate_tau_i(1.0, 0.01))


def test_mega_cap_does_not_veto_normal_atr() -> None:
    # $100M ADDV, 1% daily vol, ATR $2 on $100 → 1.5R edge >> impact.
    assert not slippage_destroys_edge(100.0, 3.0, 100_000_000.0, 0.01)
    size = size_position(100.0, 2.0, 300.0, addv=100_000_000.0, hist_vol=0.01)
    assert size is not None


def test_illiquid_high_vol_vetoes_edge() -> None:
    # Thin book + high vol: impact ≥ 1.5R profit → veto.
    entry, atr = 20.0, 1.0
    r = ATR_STOP_MULT * atr
    addv, hist_vol = 500_000.0, 0.08
    assert slippage_destroys_edge(entry, r, addv, hist_vol)
    assert size_position(entry, atr, 300.0, addv=addv, hist_vol=hist_vol) is None


def test_slippage_veto_when_impact_equals_target_edge() -> None:
    # Construct tau so entry * tau == TARGET_1_R * r exactly → veto (<= 0).
    entry, r = 100.0, 3.0
    expected = TARGET_1_R * r  # 4.5
    # Solve estimate_tau_i for impact == expected: need tau = expected / entry.
    # tau = BASE * hist_vol / max(addv,1) * NORM → pick addv=1, solve hist_vol.
    from thresholds import BASE_SLIPPAGE_BPS, SLIPPAGE_NORM_ADDV

    target_tau = expected / entry
    hist_vol = target_tau / (BASE_SLIPPAGE_BPS * SLIPPAGE_NORM_ADDV)
    assert slippage_destroys_edge(entry, r, addv=1.0, hist_vol=hist_vol)
