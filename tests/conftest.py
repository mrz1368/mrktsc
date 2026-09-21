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


def _block_live_network(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError(
        "Live network blocked in tests — mock Yahoo/Telegram/HTTP before calling."
    )


@pytest.fixture(autouse=True)
def _offline_network_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail fast if a test accidentally hits Yahoo, CNN, or Telegram."""
    import requests

    import market_data
    import sentiment
    import telegram_notify

    monkeypatch.setattr(requests, "get", _block_live_network)
    monkeypatch.setattr(requests, "post", _block_live_network)
    monkeypatch.setattr(market_data.yf, "download", _block_live_network)
    monkeypatch.setattr(sentiment.requests, "get", _block_live_network)
    monkeypatch.setattr(telegram_notify.requests, "post", _block_live_network)
