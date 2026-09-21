"""Telegram HTML formatters — visibility strings for fail-open and exit marks."""

from __future__ import annotations

from sizing import PositionSize
from telegram_notify import AlertContext, format_exit_html, format_idle_cash_html, format_setup_html


def _ctx(*, metadata_complete: bool = True) -> AlertContext:
    return AlertContext(
        ticker="RY.TO",
        sma50=100.0,
        sma200=90.0,
        rsi14=45.0,
        ema20=101.0,
        pullback_pct=1.5,
        relative_strength=3.0,
        sentiment_rating="Neutral",
        sentiment_score=50.0,
        fund_notes="Metadata unavailable — fail-open",
        adx14=28.0,
        rvol=1.4,
        sma50_slope=0.3,
        candle_confirmed=True,
        has_earnings_conflict=False,
        debt_safe=True,
        fcf_positive=True,
        quality_ok=True,
        headlines_clean=True,
        news_notes="clean",
        cooldown_days=5,
        sector="Financials",
        metadata_complete=metadata_complete,
    )


def test_setup_html_shows_unverified_fundamentals_banner() -> None:
    size = PositionSize(
        entry=100.0,
        atr=2.0,
        r=3.0,
        stop=97.0,
        target_1=104.5,
        shares=6,
        t1_shares=2,
        runner_shares=4,
        risk_cad=20.0,
        max_limit_price=101.0,
    )
    html = format_setup_html(size, _ctx(metadata_complete=False))
    assert "Unverified Fundamentals" in html
    assert "debt/FCF not confirmed" in html
    assert "⚠️" in html


def test_setup_html_omits_unverified_when_metadata_complete() -> None:
    size = PositionSize(
        entry=100.0,
        atr=2.0,
        r=3.0,
        stop=97.0,
        target_1=104.5,
        shares=6,
        t1_shares=2,
        runner_shares=4,
        risk_cad=20.0,
        max_limit_price=101.0,
    )
    html = format_setup_html(size, _ctx(metadata_complete=True))
    assert "Unverified Fundamentals" not in html


def test_exit_html_includes_eod_mark_disclaimer() -> None:
    html = format_exit_html(
        ticker="RY.TO",
        action="FULL_EXIT",
        entry_price=100.0,
        exit_price=105.0,
        shares_to_sell=6,
        shares_remaining=0,
        new_stop_price=0.0,
        message="Stop hit",
    )
    assert "EOD close" in html
    assert "tomorrow's open" in html
    assert "optimistic mark" in html


def test_idle_cash_html_notes_watches_on_radar() -> None:
    plain = format_idle_cash_html(
        cash_etf="CASH.TO",
        vix_close=18.0,
        vix_mult=1.0,
        is_bear=False,
        watches_on_radar=0,
    )
    assert "Watches on radar" not in plain

    with_watches = format_idle_cash_html(
        cash_etf="CASH.TO",
        vix_close=18.0,
        vix_mult=1.0,
        is_bear=False,
        watches_on_radar=3,
    )
    assert "Watches on radar:</b> 3" in with_watches
