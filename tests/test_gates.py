"""Offline technical pre-screen and armed-setup composition."""

from __future__ import annotations

from gates import arm_setup, is_tech_buy, is_tech_inverse, is_tech_watch, screen_technical
from indicators import SetupFlags


def _flags(**overrides: object) -> SetupFlags:
    base: dict[str, object] = dict(
        is_macro_bullish=False,
        is_macro_bearish=False,
        pullback_pct=0.0,
        pullback_below_pct=0.0,
        is_in_pullback=False,
        is_at_resistance=False,
        dist_to_200_sma_pct=10.0,
        is_rs_leader=False,
        is_rs_laggard=False,
        is_bounce_confirmed=False,
        is_rejection_confirmed=False,
        rs_vs_xiu=0.0,
        rvol=0.0,
        is_slope_positive=False,
        is_slope_negative=False,
        is_volume_confirmed=False,
        is_support_intact=False,
        is_resistance_intact=False,
        is_trend_strong=False,
        is_bull_bulletproof=False,
        is_bear_bulletproof=False,
    )
    base.update(overrides)
    return SetupFlags(**base)  # type: ignore[arg-type]


def test_tech_buy_requires_bull_pullback_stack() -> None:
    flags = _flags(
        is_macro_bullish=True,
        is_in_pullback=True,
        is_rs_leader=True,
        is_bounce_confirmed=True,
        is_bull_bulletproof=True,
    )
    assert is_tech_buy(market_regime="BULL", flags=flags, illiquid=False) is True
    assert is_tech_buy(market_regime="BEAR", flags=flags, illiquid=False) is False
    assert is_tech_buy(market_regime="BULL", flags=flags, illiquid=True) is False
    # Extreme greed still screens as tech_buy (deep scan); arm_setup owns the veto.
    screen = screen_technical(
        market_regime="BULL",
        flags=flags,
        illiquid=False,
        is_extreme_greed=True,
    )
    assert screen.is_tech_buy is True
    assert screen.is_tech_watch is False
    assert screen.needs_deep_scan is True


def test_tech_inverse_requires_bear_rejection_stack() -> None:
    flags = _flags(
        is_macro_bearish=True,
        is_at_resistance=True,
        is_rs_laggard=True,
        is_rejection_confirmed=True,
        is_bear_bulletproof=True,
    )
    assert is_tech_inverse(market_regime="BEAR", flags=flags, illiquid=False) is True
    assert is_tech_inverse(market_regime="BULL", flags=flags, illiquid=False) is False
    assert is_tech_inverse(market_regime="BEAR", flags=flags, illiquid=True) is False


def test_tech_watch_is_bull_base_outside_pullback() -> None:
    near_base = _flags(is_macro_bullish=True, is_in_pullback=False, dist_to_200_sma_pct=3.0)
    assert (
        is_tech_watch(
            market_regime="BULL",
            flags=near_base,
            illiquid=False,
            is_extreme_greed=False,
        )
        is True
    )
    too_far = _flags(is_macro_bullish=True, is_in_pullback=False, dist_to_200_sma_pct=3.01)
    assert (
        is_tech_watch(
            market_regime="BULL",
            flags=too_far,
            illiquid=False,
            is_extreme_greed=False,
        )
        is False
    )
    assert (
        is_tech_watch(
            market_regime="BULL",
            flags=near_base,
            illiquid=False,
            is_extreme_greed=True,
        )
        is False
    )
    in_pullback = _flags(is_macro_bullish=True, is_in_pullback=True, dist_to_200_sma_pct=1.0)
    assert (
        is_tech_watch(
            market_regime="BULL",
            flags=in_pullback,
            illiquid=False,
            is_extreme_greed=False,
        )
        is False
    )


def test_illiquid_blocks_every_sleeve() -> None:
    flags = _flags(
        is_macro_bullish=True,
        is_macro_bearish=True,
        is_in_pullback=True,
        is_at_resistance=True,
        is_rs_leader=True,
        is_rs_laggard=True,
        is_bounce_confirmed=True,
        is_rejection_confirmed=True,
        is_bull_bulletproof=True,
        is_bear_bulletproof=True,
        dist_to_200_sma_pct=1.0,
    )
    screen = screen_technical(
        market_regime="BULL",
        flags=flags,
        illiquid=True,
        is_extreme_greed=False,
    )
    assert screen.needs_deep_scan is False


def _fund_ok_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = dict(
        earnings_conflict=False,
        debt_safe=True,
        fcf_positive=True,
        quality_ok=True,
        headlines_clean=True,
    )
    base.update(overrides)
    return base


def test_arm_setup_category_and_earnings_gate() -> None:
    tech = screen_technical(
        market_regime="BULL",
        flags=_flags(
            is_macro_bullish=True,
            is_in_pullback=True,
            is_rs_leader=True,
            is_bounce_confirmed=True,
            is_bull_bulletproof=True,
        ),
        illiquid=False,
        is_extreme_greed=False,
    )
    armed = arm_setup(tech, **_fund_ok_kwargs())  # type: ignore[arg-type]
    assert armed.is_valid_buy is True
    assert armed.category == "setup"

    blocked = arm_setup(tech, **_fund_ok_kwargs(earnings_conflict=True))  # type: ignore[arg-type]
    assert blocked.is_valid_buy is False
    assert blocked.category == "neutral"

    greed = arm_setup(
        tech,
        **_fund_ok_kwargs(is_extreme_greed=True),  # type: ignore[arg-type]
    )
    assert greed.is_valid_buy is False
    assert greed.blocked_extreme_greed is True
    assert greed.category == "blocked"

    quiet = screen_technical(
        market_regime="BULL",
        flags=_flags(),
        illiquid=False,
        is_extreme_greed=False,
    )
    neutral = arm_setup(
        quiet,
        **_fund_ok_kwargs(
            debt_safe=False,
            headlines_clean=False,
        ),  # type: ignore[arg-type]
    )
    assert neutral.category == "neutral"


def test_arm_setup_extreme_greed_does_not_block_inverse() -> None:
    tech = screen_technical(
        market_regime="BEAR",
        flags=_flags(
            is_macro_bearish=True,
            is_at_resistance=True,
            is_rs_laggard=True,
            is_rejection_confirmed=True,
            is_bear_bulletproof=True,
        ),
        illiquid=False,
        is_extreme_greed=True,
    )
    armed = arm_setup(
        tech,
        **_fund_ok_kwargs(is_extreme_greed=True),  # type: ignore[arg-type]
    )
    assert armed.is_valid_inverse is True
    assert armed.category == "setup"
    assert armed.blocked_extreme_greed is False


def test_fund_news_reject_reason_shared() -> None:
    from gates import fund_news_reject_reason

    assert (
        fund_news_reject_reason(
            earnings_conflict=False,
            debt_safe=True,
            fcf_positive=True,
            quality_ok=True,
            headlines_clean=True,
        )
        is None
    )
    assert "blackout" in (
        fund_news_reject_reason(
            earnings_conflict=True,
            debt_safe=True,
            fcf_positive=True,
            quality_ok=True,
            headlines_clean=True,
        )
        or ""
    ).lower()
    greed = fund_news_reject_reason(
        earnings_conflict=False,
        debt_safe=True,
        fcf_positive=True,
        quality_ok=True,
        headlines_clean=True,
        is_extreme_greed=True,
        extreme_greed_score=80.0,
    )
    assert greed is not None and "Extreme Greed" in greed and "80" in greed
