"""Offline checks that trading thresholds stay a single shared object."""

from __future__ import annotations

import pytest

import fundamentals
import gates
import indicators
import sizing
import thresholds
import universe


def test_indicator_constants_are_thresholds_aliases() -> None:
    assert indicators.MIN_ADX is thresholds.MIN_ADX
    assert indicators.MIN_RVOL is thresholds.MIN_RVOL
    assert indicators.MAX_PENETRATION_ATR is thresholds.MAX_PENETRATION_ATR
    assert indicators.SMA_SLOPE_LOOKBACK is thresholds.SMA_SLOPE_LOOKBACK
    assert indicators.PULLBACK_MAX_PCT is thresholds.PULLBACK_MAX_PCT
    assert indicators.MIN_CLV_BULL is thresholds.MIN_CLV_BULL
    assert indicators.MAX_CLV_BEAR is thresholds.MAX_CLV_BEAR


def test_sizing_constants_are_thresholds_aliases() -> None:
    assert sizing.ATR_STOP_MULT is thresholds.ATR_STOP_MULT
    assert sizing.TARGET_1_R is thresholds.TARGET_1_R
    assert sizing.SCALE_OUT_FRACTION is thresholds.SCALE_OUT_FRACTION
    assert sizing.MAX_PORTFOLIO_HEAT_R is thresholds.MAX_PORTFOLIO_HEAT_R
    assert sizing.MAX_LIMIT_ATR_FRACTION is thresholds.MAX_LIMIT_ATR_FRACTION
    assert sizing.MIN_SHARES_FOR_SCALE_OUT is thresholds.MIN_SHARES_FOR_SCALE_OUT


def test_universe_constants_are_thresholds_aliases() -> None:
    assert universe.MIN_MDDV_CAD is thresholds.MIN_MDDV_CAD
    assert universe.MIN_PRICE_CAD is thresholds.MIN_PRICE_CAD
    assert universe.MAX_OPEN_PER_SECTOR is thresholds.MAX_OPEN_PER_SECTOR
    assert universe.EARNINGS_BLACKOUT_AHEAD_DAYS is thresholds.EARNINGS_BLACKOUT_AHEAD_DAYS
    assert universe.EARNINGS_BLACKOUT_POST_DAYS is thresholds.EARNINGS_BLACKOUT_POST_DAYS
    assert universe.EARNINGS_BLACKOUT_DAYS is thresholds.EARNINGS_BLACKOUT_DAYS
    assert universe.NEWS_LOOKBACK_DAYS is thresholds.NEWS_LOOKBACK_DAYS
    assert universe.EXTREME_GREED_SCORE is thresholds.EXTREME_GREED_SCORE


def test_fundamentals_and_gates_use_thresholds() -> None:
    assert fundamentals.MIN_INTEREST_COVERAGE is thresholds.MIN_INTEREST_COVERAGE
    assert fundamentals.EARNINGS_BLACKOUT_AHEAD_DAYS is (thresholds.EARNINGS_BLACKOUT_AHEAD_DAYS)
    assert gates.WATCH_MAX_DIST_TO_200_PCT is thresholds.WATCH_MAX_DIST_TO_200_PCT


def test_watch_thresholds() -> None:
    assert thresholds.WATCH_INVALIDATION_BUFFER == 0.015
    assert thresholds.WATCH_R_ATR_MULT is thresholds.ATR_STOP_MULT
    assert 1.0 - thresholds.WATCH_INVALIDATION_BUFFER == pytest.approx(0.985)
