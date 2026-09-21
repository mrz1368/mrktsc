"""Market regime helpers: VIX scaling, XIU bull/bear, inverse quotes."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd
from yfinance.exceptions import YFRateLimitError

from indicators import add_technical_indicators
from market_data import fetch_benchmark_history, fetch_inverse_history, fetch_vix_history


@dataclass(frozen=True)
class BenchmarkState:
    roc63: float
    is_bear: bool
    close: float
    sma200: float

    @property
    def market_regime(self) -> str:
        return "BEAR" if self.is_bear else "BULL"


def is_bear_regime(
    closes: Sequence[float],
    sma200s: Sequence[float],
    sma50: float,
    sma200: float,
) -> bool:
    """3-day close vs 200 SMA confirmation; 50/200 SMA is the mid-whipsaw tie-break."""
    if len(closes) < 3 or len(sma200s) < 3:
        return sma50 < sma200

    recent_closes = list(closes)[-3:]
    recent_smas = list(sma200s)[-3:]
    if all(c < s for c, s in zip(recent_closes, recent_smas)):
        return True
    if all(c > s for c, s in zip(recent_closes, recent_smas)):
        return False
    return sma50 < sma200


def regime_from_benchmark_frame(bench_df: pd.DataFrame) -> BenchmarkState:
    """Derive BenchmarkState from an already-enriched daily frame (no network)."""
    empty = BenchmarkState(roc63=0.0, is_bear=False, close=0.0, sma200=0.0)
    if bench_df.empty or len(bench_df) < 200:
        return empty
    if "SMA_200" not in bench_df.columns:
        bench_df = add_technical_indicators(bench_df)

    last = bench_df.iloc[-1]
    close = float(last["Close"])
    sma200 = float(last["SMA_200"])
    sma50 = float(last["SMA_50"])
    roc63 = float(last["ROC_63"])
    if math.isnan(roc63):
        roc63 = 0.0

    is_bear = is_bear_regime(
        bench_df["Close"].tail(3).tolist(),
        bench_df["SMA_200"].tail(3).tolist(),
        sma50,
        sma200,
    )
    return BenchmarkState(
        roc63=roc63,
        is_bear=is_bear,
        close=close,
        sma200=sma200,
    )


_SOFT_FAIL = (ValueError, TypeError, KeyError, IndexError, OSError, YFRateLimitError)


def get_vix_multiplier() -> tuple[float, float]:
    """Fetch VIX to scale risk up or down based on macro volatility."""
    try:
        hist = fetch_vix_history(period="5d")
        if hist.empty:
            return 1.0, 20.0
        vix_close = float(hist["Close"].iloc[-1])
        if vix_close < 15.0:
            return 1.25, vix_close
        if vix_close > 25.0:
            return 0.50, vix_close
        return 1.0, vix_close
    except _SOFT_FAIL as exc:
        print(f"Warning: Could not fetch VIX: {exc}")
        return 1.0, 20.0


def load_benchmark_state() -> BenchmarkState:
    """XIU 3-month return and 200-day regime with 3-day whipsaw hysteresis."""
    empty = BenchmarkState(roc63=0.0, is_bear=False, close=0.0, sma200=0.0)
    try:
        bench_df = fetch_benchmark_history(period="18mo")
        if bench_df.empty or len(bench_df) < 200:
            return empty
        return regime_from_benchmark_frame(add_technical_indicators(bench_df))
    except _SOFT_FAIL as exc:
        print(f"Warning: Could not fetch benchmark: {exc}")
        return empty


def load_inverse_quote(inverse_ticker: str) -> float | None:
    """Latest close of a BetaPro inverse ETF."""
    try:
        inv_df = fetch_inverse_history(inverse_ticker, period="1mo")
        if inv_df.empty:
            return None
        close = float(inv_df["Close"].iloc[-1])
        return close if close > 0 else None
    except _SOFT_FAIL as exc:
        print(f"Warning: Could not fetch {inverse_ticker}: {exc}")
        return None
