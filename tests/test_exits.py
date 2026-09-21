"""Offline unit tests for institutional exit evaluator."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from exits import ExitAction, evaluate_institutional_exit
from sizing import TARGET_1_R, tranche_one_shares


def _base_position(**overrides: object) -> dict:
    pos = {
        "ticker": "SHOP.TO",
        "entry_price": 100.0,
        "shares_total": 9,
        "shares_remaining": 9,
        "is_de_risked": 0,
        "initial_stop": 97.0,
        "current_stop": 97.0,
        "bars_held": 2,
    }
    pos.update(overrides)
    return pos


def _df_from_closes(
    closes: list[float],
    *,
    last_high: float | None = None,
    last_low: float | None = None,
    last_open: float | None = None,
    dividends: float | None = None,
) -> pd.DataFrame:
    """Enough bars for EMA20 / SMA50; override the final bar OHLC as needed."""
    n = max(60, len(closes))
    if len(closes) < n:
        pad = [closes[0]] * (n - len(closes))
        closes = pad + closes
    closes_arr = np.asarray(closes, dtype=float)
    opens = np.concatenate([[closes_arr[0]], closes_arr[:-1]])
    highs = closes_arr + 1.0
    lows = closes_arr - 1.0
    if last_high is not None:
        highs[-1] = last_high
    if last_low is not None:
        lows[-1] = last_low
    if last_open is not None:
        opens[-1] = last_open
    data: dict[str, object] = {
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes_arr,
        "Volume": np.full(n, 1_000_000.0),
    }
    if dividends is not None:
        divs = np.zeros(n)
        divs[-1] = dividends
        data["Dividends"] = divs
    return pd.DataFrame(data)


def test_hard_stop_hit() -> None:
    pos = _base_position(current_stop=97.0)
    # Flat then close through stop
    closes = [100.0] * 55 + [96.0]
    signal, _ = evaluate_institutional_exit(
        pos, _df_from_closes(closes), days_to_earnings=None, macro_regime_is_bull=True
    )
    assert signal.action == ExitAction.FULL_EXIT_STOP
    assert signal.shares_to_sell == 9


def test_scale_out_one_third_and_breakeven_stop() -> None:
    entry, initial_stop = 100.0, 97.0
    r = entry - initial_stop
    target = entry + TARGET_1_R * r  # 104.5
    shares_total = 9
    pos = _base_position(
        entry_price=entry,
        initial_stop=initial_stop,
        current_stop=initial_stop,
        shares_total=shares_total,
        shares_remaining=shares_total,
        is_de_risked=0,
    )
    closes = [100.0] * 55 + [target]
    signal, updated = evaluate_institutional_exit(
        pos, _df_from_closes(closes), days_to_earnings=None, macro_regime_is_bull=True
    )
    expected_t1 = tranche_one_shares(shares_total)
    assert expected_t1 == 3  # not 50% (would be 5)
    assert signal.action == ExitAction.PARTIAL_SCALE
    assert signal.shares_to_sell == expected_t1
    assert updated["is_de_risked"] == 1
    assert updated["shares_remaining"] == shares_total - expected_t1
    assert updated["current_stop"] == pytest.approx(entry * 1.002)


def test_scale_out_not_fifty_percent() -> None:
    """Regression: harvest must be ~⅓ of shares_total, never half."""
    shares_total = 12
    pos = _base_position(
        shares_total=shares_total,
        shares_remaining=shares_total,
        entry_price=100.0,
        initial_stop=97.0,
        current_stop=97.0,
    )
    closes = [100.0] * 55 + [105.0]
    signal, _ = evaluate_institutional_exit(
        pos, _df_from_closes(closes), days_to_earnings=None, macro_regime_is_bull=True
    )
    assert signal.action == ExitAction.PARTIAL_SCALE
    assert signal.shares_to_sell == tranche_one_shares(shares_total) == 4
    assert signal.shares_to_sell != shares_total // 2


def test_trail_exit_below_50_sma() -> None:
    # Rising series so SMA50 is elevated; final close slips under SMA50 but
    # stays above the hard stop so the trail branch (not stop) fires.
    base = list(np.linspace(90.0, 110.0, 55))
    closes = base + [100.0]
    pos = _base_position(
        is_de_risked=1,
        shares_remaining=6,
        shares_total=9,
        current_stop=90.0,
        entry_price=100.0,
        initial_stop=97.0,
    )
    df = _df_from_closes(closes)
    sma50 = float(df["Close"].rolling(50).mean().iloc[-1])
    assert df["Close"].iloc[-1] < sma50
    assert df["Close"].iloc[-1] > pos["current_stop"]
    signal, _ = evaluate_institutional_exit(
        pos, df, days_to_earnings=None, macro_regime_is_bull=True
    )
    assert signal.action == ExitAction.FULL_EXIT_TRAIL
    assert signal.shares_to_sell == 6


def test_earnings_purge() -> None:
    pos = _base_position()
    closes = [100.0] * 56
    signal, _ = evaluate_institutional_exit(
        pos, _df_from_closes(closes), days_to_earnings=1, macro_regime_is_bull=True
    )
    assert signal.action == ExitAction.FULL_EXIT_EARNINGS
    assert signal.shares_to_sell == 9


def test_regime_purge_longs_in_bear() -> None:
    pos = _base_position()
    closes = [100.0] * 56
    signal, _ = evaluate_institutional_exit(
        pos, _df_from_closes(closes), days_to_earnings=None, macro_regime_is_bull=False
    )
    assert signal.action == ExitAction.FULL_EXIT_REGIME


def test_regime_purge_inverse_in_bull() -> None:
    pos = _base_position(ticker="HXD.TO")
    closes = [50.0] * 56
    signal, _ = evaluate_institutional_exit(
        pos,
        _df_from_closes(closes),
        days_to_earnings=None,
        macro_regime_is_bull=True,
        is_inverse_vehicle=True,
    )
    assert signal.action == ExitAction.FULL_EXIT_REGIME


def test_dividend_adjusts_stop_without_false_trigger() -> None:
    """Ex-div cash drop: stop ratchets down by dividend; close still holds."""
    pos = _base_position(current_stop=97.0, initial_stop=97.0)
    # Close would breach unadjusted stop (96.5 < 97) but after $1 dividend
    # stop becomes 96.0 and close 96.5 still holds → HOLD (or scale if at T1).
    closes = [100.0] * 55 + [96.5]
    df = _df_from_closes(closes, dividends=1.0)
    signal, updated = evaluate_institutional_exit(
        pos, df, days_to_earnings=None, macro_regime_is_bull=True
    )
    assert signal.action != ExitAction.FULL_EXIT_STOP
    assert updated["current_stop"] == pytest.approx(96.0)
    assert updated["initial_stop"] == pytest.approx(96.0)
