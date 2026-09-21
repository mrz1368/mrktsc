"""Centralized Yahoo Finance / yfinance network I/O with retries."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence
from typing import TypeVar

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError

from universe import BENCHMARK_TICKER

T = TypeVar("T")

RATE_LIMIT_BACKOFFS = (5.0, 15.0, 30.0)

# Rate limits plus connection/timeout style failures that often recover.
# Public so fundamentals/news fail-open paths can re-raise instead of swallowing retries.
YAHOO_RETRYABLE = (YFRateLimitError, ConnectionError, TimeoutError, OSError)
_RETRYABLE = YAHOO_RETRYABLE

# Columns we keep when unwrapping a batch download frame.
_PRICE_COLS = (
    "Open",
    "High",
    "Low",
    "Close",
    "Adj Close",
    "Volume",
    "Dividends",
    "Stock Splits",
    "Capital Gains",
)


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


def fetch_fundamentals(symbol: str):
    """Deep-scan fundamentals via :func:`call_ticker` (production entry point)."""
    from fundamentals import evaluate_fundamentals

    return call_ticker(f"{symbol} fundamentals", symbol, evaluate_fundamentals)


def fetch_news_velocity(symbol: str):
    """Deep-scan headline velocity via :func:`call_ticker` (production entry point)."""
    from sentiment import evaluate_news_velocity

    return call_ticker(f"{symbol} news", symbol, evaluate_news_velocity)


def fetch_days_to_earnings(symbol: str):
    """Upcoming earnings horizon via :func:`call_ticker` (production entry point)."""
    from fundamentals import days_to_next_earnings

    return call_ticker(f"{symbol} earnings horizon", symbol, days_to_next_earnings)


def _normalize_history_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Clean one ticker's OHLCV frame: drop NaN closes, flatten accidental MultiIndex."""
    if df is None or df.empty:
        return pd.DataFrame()

    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        # Single-ticker download sometimes leaves a redundant level.
        if out.columns.nlevels == 2:
            level0 = {str(v) for v in out.columns.get_level_values(0)}
            level1 = {str(v) for v in out.columns.get_level_values(1)}
            if level0 & set(_PRICE_COLS) and not (level1 & set(_PRICE_COLS)):
                out.columns = out.columns.get_level_values(0)
            elif level1 & set(_PRICE_COLS) and not (level0 & set(_PRICE_COLS)):
                out.columns = out.columns.get_level_values(1)
            else:
                flat = out.columns.to_flat_index()
                out.columns = [c[-1] if isinstance(c, tuple) else c for c in flat]
        else:
            flat = out.columns.to_flat_index()
            out.columns = [c[-1] if isinstance(c, tuple) else c for c in flat]

    out.columns = [str(c) for c in out.columns]
    if "Close" not in out.columns:
        return pd.DataFrame()

    out = out.loc[out["Close"].notna()].copy()
    if out.empty:
        return pd.DataFrame()
    return out


def frames_from_download(
    raw: pd.DataFrame | None,
    tickers: Sequence[str],
) -> dict[str, pd.DataFrame]:
    """Split a ``yf.download`` result into ``{ticker: DataFrame}``.

    Handles single-ticker flat columns, MultiIndex ``group_by='ticker'``
    (Ticker, Price), and ``group_by='column'`` (Price, Ticker). Missing or
    empty tickers map to an empty DataFrame.
    """
    ordered = list(dict.fromkeys(str(t).strip() for t in tickers if t and str(t).strip()))
    result: dict[str, pd.DataFrame] = {t: pd.DataFrame() for t in ordered}
    if not ordered or raw is None or raw.empty:
        return result

    ticker_set = set(ordered)

    if not isinstance(raw.columns, pd.MultiIndex):
        if len(ordered) == 1:
            result[ordered[0]] = _normalize_history_frame(raw)
        return result

    level0 = [str(v) for v in raw.columns.get_level_values(0)]
    level1 = [str(v) for v in raw.columns.get_level_values(1)]
    level0_tickers = ticker_set & set(level0)
    level1_tickers = ticker_set & set(level1)

    if level0_tickers and not level1_tickers:
        # group_by='ticker' → columns (Ticker, Price)
        for t in ordered:
            if t not in raw.columns.get_level_values(0):
                continue
            try:
                sub = raw[t]
            except (KeyError, TypeError, ValueError):
                continue
            if isinstance(sub, pd.Series):
                sub = sub.to_frame()
            result[t] = _normalize_history_frame(sub)
        return result

    if level1_tickers:
        # group_by='column' → columns (Price, Ticker)
        for t in ordered:
            if t not in set(level1):
                continue
            try:
                sub = raw.xs(t, axis=1, level=1)
            except (KeyError, TypeError, ValueError):
                continue
            if isinstance(sub, pd.Series):
                sub = sub.to_frame()
            result[t] = _normalize_history_frame(sub)
        return result

    # Ambiguous / unexpected MultiIndex — best-effort single-ticker unwrap.
    if len(ordered) == 1:
        result[ordered[0]] = _normalize_history_frame(raw)
    return result


def fetch_history(
    ticker: str,
    *,
    period: str = "18mo",
    interval: str = "1d",
) -> pd.DataFrame:
    """OHLCV history for ``ticker`` with standardized retries.

    Prefer :func:`fetch_history_batch` when screening many symbols.
    """
    return with_yahoo_retries(
        ticker,
        lambda: get_ticker(ticker).history(period=period, interval=interval),
    )


def fetch_history_batch(
    tickers: Iterable[str],
    *,
    period: str = "18mo",
    interval: str = "1d",
) -> dict[str, pd.DataFrame]:
    """Batch-download OHLCV (+ dividends/splits) via ``yf.download``.

    Returns ``{ticker: DataFrame}`` for every requested symbol. Missing or
    empty Yahoo series map to an empty DataFrame (caller soft-skips).

    Matches ``Ticker.history`` defaults used elsewhere: ``auto_adjust=True``
    and ``actions=True`` so exits still see a ``Dividends`` column.
    """
    ordered = list(dict.fromkeys(str(t).strip() for t in tickers if t and str(t).strip()))
    if not ordered:
        return {}

    label = f"batch[{len(ordered)}]"
    download_arg: str | list[str] = ordered[0] if len(ordered) == 1 else list(ordered)

    def _download() -> pd.DataFrame:
        raw = yf.download(
            download_arg,
            period=period,
            interval=interval,
            group_by="ticker",
            auto_adjust=True,
            actions=True,
            threads=True,
            progress=False,
            timeout=30,
        )
        if raw is None:
            return pd.DataFrame()
        return raw

    raw = with_yahoo_retries(label, _download)
    return frames_from_download(raw, ordered)


def safe_fetch_batch(
    tickers: Iterable[str],
    *,
    period: str = "18mo",
    interval: str = "1d",
    error_label: str = "BATCH ERROR",
) -> tuple[dict[str, pd.DataFrame], str | None]:
    """Like :func:`fetch_history_batch`, but soft-fails after retries are exhausted.

    On ``YFRateLimitError`` (post-retry) or other soft I/O/parse errors: log under
    ``error_label``, sleep the final backoff on rate limits, and return
    ``({}, reason)``. Success returns ``(frames, None)``. Does not stack another
    retry layer on top of :func:`with_yahoo_retries`.
    """
    try:
        return fetch_history_batch(tickers, period=period, interval=interval), None
    except YFRateLimitError as exc:
        reason = f"Yahoo rate limit after retries: {exc}"
        print(f" -> [{error_label}] {reason}")
        time.sleep(RATE_LIMIT_BACKOFFS[-1])
        return {}, reason
    except (ValueError, TypeError, KeyError, IndexError, OSError, RuntimeError) as exc:
        reason = f"Batch history error: {exc}"
        print(f" -> [{error_label}] {reason}")
        return {}, reason


def fetch_vix_history(*, period: str = "5d") -> pd.DataFrame:
    """Short VIX daily history used by regime scaling."""
    return fetch_history("^VIX", period=period, interval="1d")


def fetch_benchmark_history(*, period: str = "18mo") -> pd.DataFrame:
    """XIU.TO (or configured benchmark) daily history for bull/bear state."""
    return fetch_history(BENCHMARK_TICKER, period=period, interval="1d")


def fetch_inverse_history(inverse_ticker: str, *, period: str = "1mo") -> pd.DataFrame:
    """Recent history for a sector inverse ETF."""
    return fetch_history(inverse_ticker, period=period, interval="1d")
