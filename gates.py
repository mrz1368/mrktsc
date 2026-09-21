"""Technical pre-screen and armed-setup composition. No I/O."""

from __future__ import annotations

from dataclasses import dataclass

from indicators import SetupFlags
from thresholds import WATCH_MAX_DIST_TO_200_PCT


@dataclass(frozen=True)
class TechScreen:
    is_tech_buy: bool
    is_tech_inverse: bool
    is_tech_watch: bool

    @property
    def needs_deep_scan(self) -> bool:
        return self.is_tech_buy or self.is_tech_inverse or self.is_tech_watch


def is_tech_buy(
    *,
    market_regime: str,
    flags: SetupFlags,
    illiquid: bool,
) -> bool:
    return (
        market_regime == "BULL"
        and flags.is_macro_bullish
        and flags.is_in_pullback
        and flags.is_rs_leader
        and flags.is_bounce_confirmed
        and flags.is_bull_bulletproof
        and not illiquid
    )


def is_tech_inverse(
    *,
    market_regime: str,
    flags: SetupFlags,
    illiquid: bool,
) -> bool:
    return (
        market_regime == "BEAR"
        and flags.is_macro_bearish
        and flags.is_at_resistance
        and flags.is_rs_laggard
        and flags.is_rejection_confirmed
        and flags.is_bear_bulletproof
        and not illiquid
    )


def is_tech_watch(
    *,
    market_regime: str,
    flags: SetupFlags,
    illiquid: bool,
    is_extreme_greed: bool,
) -> bool:
    return (
        market_regime == "BULL"
        and flags.is_macro_bullish
        and not flags.is_in_pullback
        and flags.dist_to_200_sma_pct <= WATCH_MAX_DIST_TO_200_PCT
        and not is_extreme_greed
        and not illiquid
    )


def screen_technical(
    *,
    market_regime: str,
    flags: SetupFlags,
    illiquid: bool,
    is_extreme_greed: bool,
) -> TechScreen:
    """Compose the three technical sleeves. Deep scan only if any sleeve arms."""
    return TechScreen(
        is_tech_buy=is_tech_buy(
            market_regime=market_regime,
            flags=flags,
            illiquid=illiquid,
        ),
        is_tech_inverse=is_tech_inverse(
            market_regime=market_regime,
            flags=flags,
            illiquid=illiquid,
        ),
        is_tech_watch=is_tech_watch(
            market_regime=market_regime,
            flags=flags,
            illiquid=illiquid,
            is_extreme_greed=is_extreme_greed,
        ),
    )


@dataclass(frozen=True)
class ArmedSetup:
    is_valid_buy: bool
    is_valid_inverse: bool
    is_watch: bool

    @property
    def category(self) -> str:
        if self.is_valid_buy or self.is_valid_inverse:
            return "setup"
        if self.is_watch:
            return "watch"
        return "neutral"


def arm_setup(
    tech: TechScreen,
    *,
    passes_fundamentals: bool,
    headlines_clean: bool,
    earnings_conflict: bool,
) -> ArmedSetup:
    """Final confirmation with fundamental / news gates."""
    return ArmedSetup(
        is_valid_buy=(
            tech.is_tech_buy and passes_fundamentals and headlines_clean and not earnings_conflict
        ),
        is_valid_inverse=(
            tech.is_tech_inverse
            and passes_fundamentals
            and headlines_clean
            and not earnings_conflict
        ),
        is_watch=(tech.is_tech_watch and passes_fundamentals and headlines_clean),
    )
