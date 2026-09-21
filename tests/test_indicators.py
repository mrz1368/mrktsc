"""Offline unit tests for setup flags and CLV / pullback logic."""

from __future__ import annotations

from indicators import BarSnapshot, evaluate_setup_flags


def _bar(**overrides: float) -> BarSnapshot:
    base = dict(
        close=100.0,
        open=99.0,
        high=101.0,
        low=98.0,
        volume=2_000_000.0,
        sma_50=100.0,
        sma_150=95.0,
        sma_200=90.0,
        ema_20=99.5,
        rsi=45.0,
        atr=2.0,
        vol_sma=1_000_000.0,
        stock_roc=0.10,
        sma_50_slope=0.5,
        adx=30.0,
    )
    base.update(overrides)
    return BarSnapshot(**base)  # type: ignore[arg-type]


def test_pullback_requires_close_at_or_above_50_sma() -> None:
    # Within 2.5% and close >= SMA50 → pullback
    above = evaluate_setup_flags(_bar(close=100.0, sma_50=100.0), benchmark_return=0.05)
    assert above.is_in_pullback is True

    slightly_above = evaluate_setup_flags(
        _bar(close=101.0, sma_50=100.0), benchmark_return=0.05
    )
    assert slightly_above.is_in_pullback is True

    # Close below 50 SMA must NOT count as a long pullback
    below = evaluate_setup_flags(_bar(close=99.0, sma_50=100.0), benchmark_return=0.05)
    assert below.is_in_pullback is False
    assert below.is_at_resistance is True


def test_bounce_clv_confirmation() -> None:
    # CLV = (close - low) / (high - low); need close > open and CLV >= 0.60
    confirmed = evaluate_setup_flags(
        _bar(open=99.0, low=98.0, high=102.0, close=101.0),  # CLV=0.75
        benchmark_return=0.05,
    )
    assert confirmed.is_bounce_confirmed is True

    weak_clv = evaluate_setup_flags(
        _bar(open=99.0, low=98.0, high=102.0, close=99.5),  # CLV=0.375
        benchmark_return=0.05,
    )
    assert weak_clv.is_bounce_confirmed is False

    red_candle = evaluate_setup_flags(
        _bar(open=101.0, low=98.0, high=102.0, close=100.0),  # green CLV but down day
        benchmark_return=0.05,
    )
    assert red_candle.is_bounce_confirmed is False


def test_rs_sign_and_key_setup_flags() -> None:
    leader = evaluate_setup_flags(_bar(stock_roc=0.12), benchmark_return=0.05)
    assert leader.is_rs_leader is True
    assert leader.is_rs_laggard is False
    assert leader.rs_vs_xiu > 0

    laggard = evaluate_setup_flags(_bar(stock_roc=0.02), benchmark_return=0.05)
    assert laggard.is_rs_leader is False
    assert laggard.is_rs_laggard is True
    assert laggard.rs_vs_xiu < 0

    macro = evaluate_setup_flags(
        _bar(close=110.0, sma_150=100.0, sma_200=95.0, sma_50=105.0),
        benchmark_return=0.05,
    )
    assert macro.is_macro_bullish is True
    assert macro.is_macro_bearish is False

    # Bulletproof long: slope+, volume, support intact, ADX strong
    bp = evaluate_setup_flags(
        _bar(
            close=100.5,
            sma_50=100.0,
            low=99.5,
            atr=2.0,
            volume=2_000_000.0,
            vol_sma=1_000_000.0,
            sma_50_slope=0.4,
            adx=28.0,
        ),
        benchmark_return=0.05,
    )
    assert bp.is_support_intact is True
    assert bp.is_volume_confirmed is True
    assert bp.is_trend_strong is True
    assert bp.is_bull_bulletproof is True
