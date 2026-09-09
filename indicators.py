"""Institutional technical indicators and setup flag helpers.

Corrected to use Wilder's smoothing for RSI/ATR, shifted RVOL baselines,
normalized MA slopes, and Close Location Value (CLV) candle confirmation.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

MIN_RVOL = 1.0
MIN_ADX = 25.0
MAX_PENETRATION_ATR = 1.0
SMA_SLOPE_LOOKBACK = 5


def _wilder_smooth(series: pd.Series, length: int) -> pd.Series:
    """Wilder's smoothing (RMA) used for standard ATR, RSI, and ADX."""
    return series.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    """Standard ADX calculation using Wilder's smoothing."""
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    up_move = high - prev_high
    down_move = prev_low - low

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = _wilder_smooth(tr, length)
    plus_di = 100.0 * _wilder_smooth(pd.Series(plus_dm, index=high.index), length) / (
        atr + 1e-9
    )
    minus_di = 100.0 * _wilder_smooth(pd.Series(minus_dm, index=high.index), length) / (
        atr + 1e-9
    )
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9)
    return _wilder_smooth(dx, length)


def add_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute EMA/SMA, Wilder's RSI, Wilder's ATR, ADX, RVOL, and normalized slope."""
    data = df.copy()

    data["EMA_20"] = data["Close"].ewm(span=20, adjust=False).mean()
    data["SMA_50"] = data["Close"].rolling(window=50).mean()
    data["SMA_150"] = data["Close"].rolling(window=150).mean()
    data["SMA_200"] = data["Close"].rolling(window=200).mean()

    # Normalized 50 SMA slope (% change over lookback), not dollar change.
    sma50_prev = data["SMA_50"].shift(SMA_SLOPE_LOOKBACK)
    data["SMA_50_SLOPE"] = ((data["SMA_50"] - sma50_prev) / (sma50_prev + 1e-9)) * 100.0

    # Wilder's RSI (matches TradingView / Bloomberg / Yahoo convention)
    delta = data["Close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = _wilder_smooth(gain, 14)
    avg_loss = _wilder_smooth(loss, 14)
    rs = avg_gain / (avg_loss + 1e-9)
    data["RSI_14"] = 100.0 - (100.0 / (1.0 + rs))

    # Wilder's ATR (same smoother used by ADX and sizing stops)
    tr1 = data["High"] - data["Low"]
    tr2 = (data["High"] - data["Close"].shift(1)).abs()
    tr3 = (data["Low"] - data["Close"].shift(1)).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    data["ATR_14"] = _wilder_smooth(true_range, 14)

    data["ADX_14"] = _adx(data["High"], data["Low"], data["Close"], length=14)

    # Shifted volume baseline so today's spike cannot inflate the denominator.
    data["VOL_SMA_20"] = data["Volume"].shift(1).rolling(window=20).mean()
    data["ROC_63"] = (data["Close"] - data["Close"].shift(63)) / data["Close"].shift(63)

    return data


@dataclass(frozen=True)
class BarSnapshot:
    close: float
    open: float
    high: float
    low: float
    volume: float
    sma_50: float
    sma_150: float
    sma_200: float
    ema_20: float
    rsi: float
    atr: float
    vol_sma: float
    stock_roc: float
    sma_50_slope: float
    adx: float


@dataclass(frozen=True)
class SetupFlags:
    is_macro_bullish: bool
    is_macro_bearish: bool
    pullback_pct: float
    pullback_below_pct: float
    is_in_pullback: bool
    is_at_resistance: bool
    dist_to_200_sma_pct: float
    is_rs_leader: bool
    is_rs_laggard: bool
    is_bounce_confirmed: bool
    is_rejection_confirmed: bool
    rs_vs_xiu: float
    rvol: float
    is_slope_positive: bool
    is_slope_negative: bool
    is_volume_confirmed: bool
    is_support_intact: bool
    is_resistance_intact: bool
    is_trend_strong: bool
    is_bull_bulletproof: bool
    is_bear_bulletproof: bool


def snapshot_from_bar(row: pd.Series) -> BarSnapshot:
    return BarSnapshot(
        close=float(row["Close"]),
        open=float(row["Open"]),
        high=float(row["High"]),
        low=float(row["Low"]),
        volume=float(row["Volume"]),
        sma_50=float(row["SMA_50"]),
        sma_150=float(row["SMA_150"]),
        sma_200=float(row["SMA_200"]),
        ema_20=float(row["EMA_20"]),
        rsi=float(row["RSI_14"]),
        atr=float(row["ATR_14"]),
        vol_sma=float(row["VOL_SMA_20"]),
        stock_roc=float(row["ROC_63"]),
        sma_50_slope=float(row["SMA_50_SLOPE"]),
        adx=float(row["ADX_14"]),
    )


def evaluate_setup_flags(bar: BarSnapshot, benchmark_return: float) -> SetupFlags:
    # Signed distance to 50 SMA; abs() used only for proximity width.
    pullback_pct = ((bar.close - bar.sma_50) / bar.sma_50) * 100
    pullback_below_pct = ((bar.sma_50 - bar.close) / bar.sma_50) * 100

    # Close Location Value — institutional confirmation threshold (>60% / <40%).
    day_range = bar.high - bar.low
    clv = (bar.close - bar.low) / day_range if day_range > 0 else 0.5
    strong_bull_wick = clv >= 0.60
    strong_bear_wick = clv <= 0.40

    rvol = (bar.volume / bar.vol_sma) if bar.vol_sma > 0 else 0.0

    is_slope_positive = bar.sma_50_slope > 0
    is_slope_negative = bar.sma_50_slope < 0
    is_volume_confirmed = rvol >= MIN_RVOL

    # Close must hold the MA; wick penetration limited to 1x ATR.
    is_support_intact = (bar.close >= bar.sma_50) and (
        bar.low >= (bar.sma_50 - MAX_PENETRATION_ATR * bar.atr)
    )
    is_resistance_intact = (bar.close <= bar.sma_50) and (
        bar.high <= (bar.sma_50 + MAX_PENETRATION_ATR * bar.atr)
    )
    is_trend_strong = bar.adx >= MIN_ADX

    is_bull_bulletproof = (
        is_slope_positive
        and is_volume_confirmed
        and is_support_intact
        and is_trend_strong
    )
    is_bear_bulletproof = (
        is_slope_negative
        and is_volume_confirmed
        and is_resistance_intact
        and is_trend_strong
    )

    return SetupFlags(
        is_macro_bullish=(
            bar.close > bar.sma_150
            and bar.close > bar.sma_200
            and bar.sma_50 > bar.sma_200
        ),
        is_macro_bearish=(
            bar.close < bar.sma_150
            and bar.close < bar.sma_200
            and bar.sma_50 < bar.sma_200
        ),
        pullback_pct=abs(pullback_pct),
        pullback_below_pct=pullback_below_pct,
        # Long pullback: near support from above — not a close below the 50 SMA.
        is_in_pullback=(abs(pullback_pct) <= 2.5) and (bar.close >= bar.sma_50),
        is_at_resistance=(abs(pullback_pct) <= 2.5) and (bar.close <= bar.sma_50),
        dist_to_200_sma_pct=(abs(bar.close - bar.sma_200) / bar.sma_200) * 100,
        is_rs_leader=bar.stock_roc > benchmark_return,
        is_rs_laggard=bar.stock_roc < benchmark_return,
        is_bounce_confirmed=(bar.close > bar.open) and strong_bull_wick,
        is_rejection_confirmed=(bar.close < bar.open) and strong_bear_wick,
        rs_vs_xiu=(bar.stock_roc - benchmark_return) * 100,
        rvol=rvol,
        is_slope_positive=is_slope_positive,
        is_slope_negative=is_slope_negative,
        is_volume_confirmed=is_volume_confirmed,
        is_support_intact=is_support_intact,
        is_resistance_intact=is_resistance_intact,
        is_trend_strong=is_trend_strong,
        is_bull_bulletproof=is_bull_bulletproof,
        is_bear_bulletproof=is_bear_bulletproof,
    )
