"""Offline regime hysteresis tests — pure logic, no Yahoo."""

from __future__ import annotations

import numpy as np
import pandas as pd

from indicators import add_technical_indicators
from regime import is_bear_regime, regime_from_benchmark_frame


def test_three_closes_below_200_confirm_bear() -> None:
    assert is_bear_regime(
        closes=[99.0, 98.0, 97.0],
        sma200s=[100.0, 100.0, 100.0],
        sma50=95.0,
        sma200=100.0,
    )


def test_three_closes_above_200_confirm_bull() -> None:
    assert (
        is_bear_regime(
            closes=[101.0, 102.0, 103.0],
            sma200s=[100.0, 100.0, 100.0],
            sma50=105.0,
            sma200=100.0,
        )
        is False
    )


def test_whipsaw_uses_50_vs_200_tiebreaker() -> None:
    # Mixed closes vs 200 SMA → mid-whipsaw
    mixed_closes = [101.0, 99.0, 101.0]
    sma200s = [100.0, 100.0, 100.0]
    assert is_bear_regime(mixed_closes, sma200s, sma50=95.0, sma200=100.0) is True
    assert is_bear_regime(mixed_closes, sma200s, sma50=105.0, sma200=100.0) is False


def test_short_history_falls_back_to_50_200() -> None:
    assert is_bear_regime([99.0], [100.0], sma50=90.0, sma200=100.0) is True
    assert is_bear_regime([99.0], [100.0], sma50=110.0, sma200=100.0) is False


def test_regime_from_benchmark_frame_bull_trend() -> None:
    # Steady uptrend: closes stay above rising 200 SMA for 3+ days.
    n = 250
    closes = np.linspace(80.0, 120.0, n)
    df = pd.DataFrame(
        {
            "Open": closes,
            "High": closes + 1.0,
            "Low": closes - 1.0,
            "Close": closes,
            "Volume": np.full(n, 1_000_000.0),
        },
        index=pd.date_range("2023-01-01", periods=n, freq="B"),
    )
    state = regime_from_benchmark_frame(add_technical_indicators(df))
    assert state.is_bear is False
    assert state.close > 0
    assert state.sma200 > 0


def test_regime_from_benchmark_frame_bear_trend() -> None:
    n = 250
    closes = np.linspace(120.0, 80.0, n)
    df = pd.DataFrame(
        {
            "Open": closes,
            "High": closes + 1.0,
            "Low": closes - 1.0,
            "Close": closes,
            "Volume": np.full(n, 1_000_000.0),
        },
        index=pd.date_range("2023-01-01", periods=n, freq="B"),
    )
    state = regime_from_benchmark_frame(add_technical_indicators(df))
    assert state.is_bear is True
