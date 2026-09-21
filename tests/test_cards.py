"""Offline dashboard card shaping. Keys must match templates/dashboard.html."""

from __future__ import annotations

import pytest

import thresholds
from cards import daily_change_pct, dashboard_card, skipped_dashboard_card
from fundamentals import FundamentalsResult
from indicators import BarSnapshot, SetupFlags
from sentiment import NewsVelocityResult

TEMPLATE_KEYS = {
    "ticker",
    "sector",
    "close",
    "change_pct",
    "category",
    "adx",
    "rvol",
    "sma50",
    "sma200",
    "sma50_slope",
    "rs_vs_xiu",
    "pullback_pct",
    "dist_to_50_pct",
    "above_sma50",
    "dist_to_200_pct",
    "invalidation_price",
    "debt_safe",
    "fcf_positive",
    "earnings_conflict",
    "headlines_clean",
    "fund_notes",
}


def test_daily_change_pct(make_ohlcv) -> None:
    df = make_ohlcv([100.0, 110.0])
    assert daily_change_pct(df, 110.0) == pytest.approx(10.0)
    assert daily_change_pct(make_ohlcv([100.0]), 100.0) == 0.0
    assert daily_change_pct(make_ohlcv([0.0, 10.0]), 10.0) == 0.0


def test_dashboard_card_template_keys_and_signed_distance(make_ohlcv) -> None:
    bar = BarSnapshot(
        close=102.0,
        open=100.0,
        high=103.0,
        low=99.0,
        volume=1.0,
        sma_50=100.0,
        sma_150=95.0,
        sma_200=90.0,
        ema_20=101.0,
        rsi=50.0,
        atr=2.0,
        vol_sma=1.0,
        stock_roc=0.1,
        sma_50_slope=0.4,
        adx=28.0,
        addv=100_000_000.0,
        hist_vol=0.01,
    )
    flags = SetupFlags(
        is_macro_bullish=True,
        is_macro_bearish=False,
        pullback_pct=2.0,
        pullback_below_pct=-2.0,
        is_in_pullback=True,
        is_at_resistance=False,
        dist_to_200_sma_pct=13.333,
        is_rs_leader=True,
        is_rs_laggard=False,
        is_bounce_confirmed=True,
        is_rejection_confirmed=False,
        rs_vs_xiu=5.0,
        rvol=1.4,
        is_slope_positive=True,
        is_slope_negative=False,
        is_volume_confirmed=True,
        is_support_intact=True,
        is_resistance_intact=False,
        is_trend_strong=True,
        is_bull_bulletproof=True,
        is_bear_bulletproof=False,
    )
    fund = FundamentalsResult(
        earnings_conflict=False,
        debt_safe=True,
        fcf_positive=True,
        quality_ok=True,
        notes="ok",
    )
    news = NewsVelocityResult(headlines_clean=True, hit_count=0, notes="clean")
    card = dashboard_card(
        "RY.TO",
        "Financials",
        bar,
        make_ohlcv([100.0, 102.0]),
        flags,
        fund,
        news,
        "setup",
        extra_note="thin book",
    )
    payload = card.to_template_dict()
    assert set(payload) == TEMPLATE_KEYS
    assert payload["dist_to_50_pct"] == pytest.approx(2.0)
    assert payload["above_sma50"] is True
    assert payload["invalidation_price"] == pytest.approx(
        90.0 * (1.0 - thresholds.WATCH_INVALIDATION_BUFFER)
    )
    assert payload["fund_notes"] == "thin book ok"
    assert payload["change_pct"] == pytest.approx(2.0)
    assert payload["sma50"] == 100.0


def test_skipped_card_is_neutral_placeholder() -> None:
    card = skipped_dashboard_card("RY.TO", "No price history from Yahoo.")
    payload = card.to_template_dict()
    assert set(payload) == TEMPLATE_KEYS
    assert payload["category"] == "neutral"
    assert payload["sector"] == "Financials"
    assert payload["headlines_clean"] is True
    assert payload["fund_notes"] == "No price history from Yahoo."
    assert payload["close"] == 0.0
