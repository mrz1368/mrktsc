"""Fixed-dollar risk share sizing with multi-week scale-out targets."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

ATR_STOP_MULT = 1.5
TARGET_1_R = 1.5
# Shared harvest fraction for Telegram sizing and live exits (must stay in sync).
SCALE_OUT_FRACTION = 1.0 / 3.0
# Cap open portfolio risk before new buys are blocked (scanner heat veto).
MAX_PORTFOLIO_HEAT_R = 6.0


class HeatPosition(Protocol):
    entry_price: float
    current_stop: float
    shares_remaining: int


def tranche_one_shares(total_shares: int) -> int:
    """Shares to sell at +1.5R — ceil(total × ⅓), matching alert tickets."""
    if total_shares <= 0:
        return 0
    return max(1, math.ceil(total_shares * SCALE_OUT_FRACTION))


def portfolio_heat_r(
    positions: Iterable[HeatPosition],
    unit_risk_cad: float,
) -> float:
    """Sum open risk in R-multiples. Free rolls (stop ≥ entry) contribute 0."""
    if unit_risk_cad <= 0:
        return 0.0
    total = 0.0
    for pos in positions:
        risk_per_share = max(0.0, float(pos.entry_price) - float(pos.current_stop))
        total += (risk_per_share * int(pos.shares_remaining)) / unit_risk_cad
    return total


@dataclass(frozen=True)
class PositionSize:
    entry: float
    atr: float
    r: float
    stop: float
    target_1: float
    shares: int
    t1_shares: int
    runner_shares: int
    risk_cad: float
    max_limit_price: float


def size_position(entry: float, atr: float, risk_cad: float) -> PositionSize | None:
    if entry <= 0 or atr <= 0 or risk_cad <= 0:
        return None

    r = ATR_STOP_MULT * atr
    if r <= 0:
        return None

    shares = math.floor(risk_cad / r)
    if shares < 3:
        # Require at least 3 shares to execute a 1/3 scale-out
        return None

    t1_shares = tranche_one_shares(shares)
    runner_shares = shares - t1_shares
    # Do not pay more than 15% of ATR above yesterday's close.
    max_limit = entry + (0.15 * atr)

    return PositionSize(
        entry=entry,
        atr=atr,
        r=r,
        stop=entry - r,
        target_1=entry + (TARGET_1_R * r),
        shares=shares,
        t1_shares=t1_shares,
        runner_shares=runner_shares,
        risk_cad=risk_cad,
        max_limit_price=max_limit,
    )
