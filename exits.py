"""Institutional multi-tranche exit evaluator.

Implements:
- Staged scaling (50% harvest at 1.5R, trail remainder)
- Stop-to-breakeven ratchet
- Trend continuation trailing stop (20 EMA / 50 SMA)
- Event risk liquidation (pre-earnings T-2)
- Macro regime override
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd


class ExitAction(str, Enum):
    HOLD = "HOLD"
    PARTIAL_SCALE = "PARTIAL_SCALE_PROFIT"
    FULL_EXIT_STOP = "FULL_EXIT_STOP"
    FULL_EXIT_TRAIL = "FULL_EXIT_TRAIL"
    FULL_EXIT_EARNINGS = "FULL_EXIT_PRE_EARNINGS"
    FULL_EXIT_REGIME = "FULL_EXIT_REGIME_VETO"
    FULL_EXIT_STAGNANT = "FULL_EXIT_STAGNANT"


@dataclass(frozen=True)
class ExitSignal:
    ticker: str
    action: ExitAction
    shares_to_sell: int
    new_stop_price: float
    reason: str
    exit_price: float
    mark_price: float


def evaluate_institutional_exit(
    position: dict,
    df_daily: pd.DataFrame,
    days_to_earnings: int | None,
    macro_regime_is_bull: bool,
    *,
    apply_regime_veto: bool = True,
    max_holding_bars: int = 15,
) -> tuple[ExitSignal, dict]:
    """Evaluate an active position using an institutional tranche lifecycle."""
    ticker = str(position["ticker"])
    entry_price = float(position["entry_price"])
    shares_held = int(position["shares_remaining"])
    is_de_risked = bool(position["is_de_risked"])
    initial_stop = float(position["initial_stop"])
    current_stop = float(position["current_stop"])

    current_close = float(df_daily["Close"].iloc[-1])
    ema20 = float(df_daily["Close"].ewm(span=20, adjust=False).mean().iloc[-1])
    sma50 = float(df_daily["Close"].rolling(50).mean().iloc[-1])
    bars_held = int(position["bars_held"]) + 1

    updated_pos = dict(position)
    updated_pos["bars_held"] = bars_held

    def _signal(
        action: ExitAction,
        shares_to_sell: int,
        new_stop: float,
        reason: str,
        *,
        exit_price: float | None = None,
    ) -> tuple[ExitSignal, dict]:
        price = current_close if exit_price is None else exit_price
        return (
            ExitSignal(
                ticker=ticker,
                action=action,
                shares_to_sell=shares_to_sell,
                new_stop_price=new_stop,
                reason=reason,
                exit_price=price,
                mark_price=current_close,
            ),
            updated_pos,
        )

    # 1. Exogenous override: mandatory pre-earnings liquidation
    if days_to_earnings is not None and 0 <= days_to_earnings <= 2:
        return _signal(
            ExitAction.FULL_EXIT_EARNINGS,
            shares_held,
            0.0,
            f"Mandatory catalyst de-risking: Earnings in {days_to_earnings} days.",
        )

    # 2. Macro regime override: systematic long beta purge
    if apply_regime_veto and not macro_regime_is_bull:
        return _signal(
            ExitAction.FULL_EXIT_REGIME,
            shares_held,
            0.0,
            "Benchmark broke 200 SMA (Bear Regime). Systematic risk purge.",
        )

    # 3. Capital preservation: close-confirmed hard stop
    if current_close <= current_stop:
        return _signal(
            ExitAction.FULL_EXIT_STOP,
            shares_held,
            0.0,
            f"Close (${current_close:.2f}) violated stop level (${current_stop:.2f}).",
        )

    # 4. Tranche 1: de-risking scale at 1.5R (close-confirmed)
    r_distance = entry_price - initial_stop
    target_1_5r = entry_price + (1.5 * r_distance)

    if not is_de_risked and r_distance > 0 and current_close >= target_1_5r:
        half_shares = max(1, shares_held // 2)
        if half_shares >= shares_held:
            half_shares = max(1, shares_held - 1) if shares_held > 1 else shares_held
        new_stop = max(current_stop, entry_price * 1.001)
        remaining = shares_held - half_shares
        updated_pos["is_de_risked"] = 1
        updated_pos["shares_remaining"] = remaining
        updated_pos["current_stop"] = new_stop
        return _signal(
            ExitAction.PARTIAL_SCALE,
            half_shares,
            new_stop,
            f"Hit 1.5R (${target_1_5r:.2f}). Sell 50%, ratchet stop to Break-Even.",
            exit_price=current_close,
        )

    # 5. Tranche 2: core trend trail (post de-risking)
    if is_de_risked:
        trailing_benchmark = min(ema20, sma50)
        if trailing_benchmark > current_stop:
            updated_pos["current_stop"] = trailing_benchmark
            current_stop = trailing_benchmark

        if current_close < sma50:
            return _signal(
                ExitAction.FULL_EXIT_TRAIL,
                shares_held,
                0.0,
                "Core runner closed below 50 SMA. Trend exhaustion confirmed.",
            )

    # 6. Time stagnation: dead capital velocity exit (pre de-risk only)
    if bars_held >= max_holding_bars and not is_de_risked:
        pnl_pct = ((current_close - entry_price) / entry_price) * 100.0
        if abs(pnl_pct) < 1.0:
            return _signal(
                ExitAction.FULL_EXIT_STAGNANT,
                shares_held,
                0.0,
                (
                    f"Capital velocity dead-lock ({bars_held} bars near 0% PnL). "
                    "Recycle."
                ),
            )

    return _signal(
        ExitAction.HOLD,
        0,
        float(updated_pos.get("current_stop", current_stop)),
        "Trend intact. Hold.",
    )
