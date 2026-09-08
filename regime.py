"""Market regime helpers: VIX scaling, XIU bull/bear, inverse quotes."""

from __future__ import annotations

import math
from dataclasses import dataclass

import yfinance as yf

from indicators import add_technical_indicators
from universe import BENCHMARK_TICKER


@dataclass(frozen=True)
class BenchmarkState:
    roc63: float
    is_bear: bool
    close: float
    sma200: float

    @property
    def market_regime(self) -> str:
        return "BEAR" if self.is_bear else "BULL"


def get_vix_multiplier() -> tuple[float, float]:
    """Fetch VIX to scale risk up or down based on macro volatility."""
    try:
        hist = yf.Ticker("^VIX").history(period="5d")
        if hist.empty:
            return 1.0, 20.0
        vix_close = float(hist["Close"].iloc[-1])
        if vix_close < 15.0:
            return 1.25, vix_close
        if vix_close > 25.0:
            return 0.50, vix_close
        return 1.0, vix_close
    except (ValueError, TypeError, KeyError, IndexError, OSError) as exc:
        print(f"Warning: Could not fetch VIX: {exc}")
        return 1.0, 20.0


def load_benchmark_state() -> BenchmarkState:
    """XIU 3-month return and 200-day regime (bull vs bear)."""
    empty = BenchmarkState(roc63=0.0, is_bear=False, close=0.0, sma200=0.0)
    try:
        bench_df = yf.Ticker(BENCHMARK_TICKER).history(period="18mo", interval="1d")
        if bench_df.empty or len(bench_df) < 200:
            return empty
        bench_df = add_technical_indicators(bench_df)
        last = bench_df.iloc[-1]
        close = float(last["Close"])
        sma200 = float(last["SMA_200"])
        roc63 = float(last["ROC_63"])
        if math.isnan(roc63):
            roc63 = 0.0
        return BenchmarkState(
            roc63=roc63,
            is_bear=close < sma200,
            close=close,
            sma200=sma200,
        )
    except (ValueError, TypeError, KeyError, IndexError, OSError) as exc:
        print(f"Warning: Could not fetch benchmark: {exc}")
        return empty


def load_inverse_quote(inverse_ticker: str) -> float | None:
    """Latest close of a BetaPro inverse ETF."""
    try:
        inv_df = yf.Ticker(inverse_ticker).history(period="1mo", interval="1d")
        if inv_df.empty:
            return None
        close = float(inv_df["Close"].iloc[-1])
        return close if close > 0 else None
    except (ValueError, TypeError, KeyError, IndexError, OSError) as exc:
        print(f"Warning: Could not fetch {inverse_ticker}: {exc}")
        return None
