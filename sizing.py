"""Fixed-dollar risk share sizing with multi-week scale-out targets."""

from __future__ import annotations

import math
from dataclasses import dataclass

ATR_STOP_MULT = 1.5
TARGET_1_R = 1.5


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

    t1_shares = math.ceil(shares / 3.0)
    runner_shares = shares - t1_shares

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
    )
