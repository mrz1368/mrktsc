"""Shared offline fixtures — no network."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def make_ohlcv():
    """Build a synthetic OHLCV frame from close levels (optional dividends)."""

    def _make(
        closes: list[float],
        *,
        atr_pad: float = 1.0,
        volume: float = 1_000_000.0,
        dividends: list[float] | None = None,
    ) -> pd.DataFrame:
        n = len(closes)
        closes_arr = np.asarray(closes, dtype=float)
        opens = np.concatenate([[closes_arr[0]], closes_arr[:-1]])
        highs = closes_arr + atr_pad
        lows = closes_arr - atr_pad
        idx = pd.date_range("2024-01-01", periods=n, freq="B")
        data = {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes_arr,
            "Volume": np.full(n, volume),
        }
        if dividends is not None:
            data["Dividends"] = dividends
        return pd.DataFrame(data, index=idx)

    return _make
