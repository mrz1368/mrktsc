"""Technical pre-screen and armed-setup composition. No I/O."""

from __future__ import annotations

from dataclasses import dataclass

from indicators import SetupFlags
from thresholds import (
    EARNINGS_BLACKOUT_AHEAD_DAYS,
    EARNINGS_BLACKOUT_POST_DAYS,
    WATCH_MAX_DIST_TO_200_PCT,
)


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
    blocked_extreme_greed: bool = False

    @property
    def category(self) -> str:
        if self.is_valid_buy or self.is_valid_inverse:
            return "setup"
        if self.is_watch:
            return "watch"
        if self.blocked_extreme_greed:
            return "blocked"
        return "neutral"


def fund_news_reject_reason(
    *,
    earnings_conflict: bool,
    debt_safe: bool,
    fcf_positive: bool,
    quality_ok: bool,
    headlines_clean: bool,
    is_extreme_greed: bool = False,
    extreme_greed_score: float | None = None,
    news_notes: str | None = None,
) -> str | None:
    """Shared fund / news / earnings / greed veto reasons (SSOT for arm + dispatch).

    Extreme greed is optional: pass ``is_extreme_greed=True`` for long-buy
    rejects; leave False when arming inverses / computing fund_ok.
    """
    if earnings_conflict:
        return (
            f"Earnings inside -{EARNINGS_BLACKOUT_POST_DAYS}/"
            f"+{EARNINGS_BLACKOUT_AHEAD_DAYS} day blackout."
        )
    if not debt_safe:
        return "Failed debt-service coverage floor (<2x)."
    if not fcf_positive:
        return "Failed positive free-cash-flow / OCF check."
    if not quality_ok:
        return "Failed operating-margin quality floor."
    if not headlines_clean:
        if news_notes:
            return f"Negative news velocity: {news_notes}"
        return "Negative news velocity."
    if is_extreme_greed:
        if extreme_greed_score is not None:
            return f"Extreme Greed regime ({extreme_greed_score:.0f}/100)."
        return "Extreme Greed regime."
    return None


def arm_setup(
    tech: TechScreen,
    *,
    earnings_conflict: bool,
    debt_safe: bool,
    fcf_positive: bool,
    quality_ok: bool,
    headlines_clean: bool,
    is_extreme_greed: bool = False,
) -> ArmedSetup:
    """Final confirmation with fundamental / news / regime gates.

    Extreme greed blocks long buys (dashboard + dispatch stay aligned) but does
    not block bear-regime inverses. Watch is already gated in ``is_tech_watch``.
    """
    fund_ok = (
        fund_news_reject_reason(
            earnings_conflict=earnings_conflict,
            debt_safe=debt_safe,
            fcf_positive=fcf_positive,
            quality_ok=quality_ok,
            headlines_clean=headlines_clean,
            is_extreme_greed=False,
        )
        is None
    )
    would_buy = tech.is_tech_buy and fund_ok
    blocked_extreme_greed = would_buy and is_extreme_greed
    return ArmedSetup(
        is_valid_buy=would_buy and not is_extreme_greed,
        is_valid_inverse=tech.is_tech_inverse and fund_ok,
        is_watch=tech.is_tech_watch and fund_ok,
        blocked_extreme_greed=blocked_extreme_greed,
    )
