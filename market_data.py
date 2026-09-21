"""Centralized Yahoo Finance / yfinance network I/O with retries."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError

from universe import BENCHMARK_TICKER

T = TypeVar("T")

RATE_LIMIT_BACKOFFS = (5.0, 15.0, 30.0)

# Rate limits plus connection/timeout style failures that often recover.
_RETRYABLE = (YFRateLimitError, ConnectionError, TimeoutError, OSError)


def with_yahoo_retries(label: str, fn: Callable[[], T]) -> T:
    """Retry Yahoo calls on rate limits / transient errors; re-raise after final backoff."""
    last_exc: BaseException | None = None
    for attempt, wait_sec in enumerate(RATE_LIMIT_BACKOFFS, start=1):
        try:
            return fn()
        except _RETRYABLE as exc:
            last_exc = exc
            kind = "RATE LIMIT" if isinstance(exc, YFRateLimitError) else "TRANSIENT"
            print(
                f" -> [{kind}] {label}: attempt {attempt}/"
                f"{len(RATE_LIMIT_BACKOFFS)}; sleeping {wait_sec:.0f}s"
            )
            time.sleep(wait_sec)
    assert last_exc is not None
    raise last_exc


def get_ticker(symbol: str) -> yf.Ticker:
    """Construct a yfinance Ticker (lazy; no network until a property is accessed)."""
    return yf.Ticker(symbol)


def call_ticker(label: str, symbol: str, fn: Callable[[yf.Ticker], T]) -> T:
    """Run ``fn(ticker)`` under the shared Yahoo retry policy."""
    ticker_obj = get_ticker(symbol)
    return with_yahoo_retries(label, lambda: fn(ticker_obj))


def fetch_history(
    ticker: str,
    *,
    period: str = "18mo",
    interval: str = "1d",
) -> pd.DataFrame:
    """OHLCV history for ``ticker`` with standardized retries."""
    return with_yahoo_retries(
        ticker,
        lambda: get_ticker(ticker).history(period=period, interval=interval),
    )


def fetch_vix_history(*, period: str = "5d") -> pd.DataFrame:
    """Short VIX daily history used by regime scaling."""
    return fetch_history("^VIX", period=period, interval="1d")


def fetch_benchmark_history(*, period: str = "18mo") -> pd.DataFrame:
    """XIU.TO (or configured benchmark) daily history for bull/bear state."""
    return fetch_history(BENCHMARK_TICKER, period=period, interval="1d")


def fetch_inverse_history(inverse_ticker: str, *, period: str = "1mo") -> pd.DataFrame:
    """Recent history for a sector inverse ETF."""
    return fetch_history(inverse_ticker, period=period, interval="1d")
