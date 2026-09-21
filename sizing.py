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


def stops_after_pending_fill(
    *,
    fill_price: float,
    old_entry: float,
    old_initial_stop: float,
) -> tuple[float, float]:
    """Preserve signal R-distance when confirming a fill at next open.

    Returns ``(new_initial_stop, new_current_stop)`` — both equal to
    ``fill_price - max(old_entry - old_initial_stop, 0)``.
    """
    r_distance = max(old_entry - old_initial_stop, 0.0)
    new_stop = fill_price - r_distance
    return new_stop, new_stop


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
class HeatVeto:
    """Open-risk reading. `veto` blocks new buys; `log_line` is None when flat."""

    open_r: float
    veto: bool
    log_line: str | None


def evaluate_heat_veto(
    positions: Iterable[HeatPosition],
    unit_risk_cad: float,
    *,
    max_heat_r: float = MAX_PORTFOLIO_HEAT_R,
) -> HeatVeto:
    """Compare open risk to the heat cap and assemble the scanner log line."""
    open_r = portfolio_heat_r(positions, unit_risk_cad)
    veto = open_r >= max_heat_r
    if veto:
        log_line = (
            f" -> [HEAT VETO] Portfolio at {open_r:.1f}R open risk. "
            "New buys blocked."
        )
    elif open_r > 0:
        log_line = (
            f" -> [HEAT] Open portfolio risk: {open_r:.1f}R / {max_heat_r:.0f}R"
        )
    else:
        log_line = None
    return HeatVeto(open_r=open_r, veto=veto, log_line=log_line)


def size_inverse_from_underlying(
    *,
    underlying_close: float,
    underlying_atr: float,
    inverse_close: float,
    leverage_factor: float,
    risk_cad: float,
) -> PositionSize | None:
    """Translate underlying ATR risk onto the inverse, scaled by |leverage|."""
    if underlying_close <= 0 or underlying_atr <= 0:
        return None
    underlying_stop_pct = (ATR_STOP_MULT * underlying_atr) / underlying_close
    inv_stop_pct = underlying_stop_pct * leverage_factor
    inv_atr = (inverse_close * inv_stop_pct) / ATR_STOP_MULT
    return size_position(inverse_close, inv_atr, risk_cad)


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
