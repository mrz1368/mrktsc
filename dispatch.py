"""Alert dispatch: buy / inverse / watch Telegram tickets and book opens."""

from __future__ import annotations

import sqlite3

from config import Config
from db import (
    ALERT_BUY,
    ALERT_INVERSE,
    ALERT_WATCH,
    open_active_position,
    record_if_allowed,
)
from fundamentals import FundamentalsResult
from indicators import BarSnapshot, SetupFlags
from regime import load_inverse_quote
from sentiment import MacroSentiment, NewsVelocityResult
from sizing import PositionSize, size_inverse_from_underlying, size_position
from telegram_notify import (
    AlertContext,
    format_inverse_html,
    format_setup_html,
    format_watchlist_html,
    send_html_message,
)
from thresholds import (
    EARNINGS_BLACKOUT_AHEAD_DAYS,
    EARNINGS_BLACKOUT_POST_DAYS,
    MAX_LIMIT_ATR_FRACTION,
    MAX_OPEN_PER_SECTOR,
    WATCH_INVALIDATION_BUFFER,
    WATCH_R_ATR_MULT,
)
from universe import CASH_ETF, inverse_etf_for_sector, inverse_leverage


def _build_alert_context(
    ticker: str,
    bar: BarSnapshot,
    flags: SetupFlags,
    fund: FundamentalsResult,
    macro: MacroSentiment,
    news: NewsVelocityResult,
    *,
    sector: str,
    cooldown_days: int,
    vix_close: float,
    vix_mult: float,
    dynamic_risk_cad: float,
    pullback_pct: float,
    candle_confirmed: bool,
) -> AlertContext:
    return AlertContext(
        ticker=ticker,
        sma50=bar.sma_50,
        sma200=bar.sma_200,
        rsi14=bar.rsi,
        ema20=bar.ema_20,
        pullback_pct=pullback_pct,
        relative_strength=flags.rs_vs_xiu,
        sentiment_rating=macro.rating.title(),
        sentiment_score=macro.score,
        fund_notes=fund.notes,
        adx14=bar.adx,
        rvol=flags.rvol,
        sma50_slope=bar.sma_50_slope,
        candle_confirmed=candle_confirmed,
        has_earnings_conflict=fund.earnings_conflict,
        debt_safe=fund.debt_safe,
        fcf_positive=fund.fcf_positive,
        quality_ok=fund.quality_ok,
        headlines_clean=news.headlines_clean,
        news_notes=news.notes,
        cooldown_days=cooldown_days,
        sector=sector,
        vix_close=vix_close,
        vix_mult=vix_mult,
        dynamic_risk_cad=dynamic_risk_cad,
        cash_etf=CASH_ETF,
    )


def _reject_fund_or_news(
    ticker: str,
    fund: FundamentalsResult,
    news: NewsVelocityResult,
    macro: MacroSentiment,
) -> str | None:
    if fund.earnings_conflict:
        return (
            f"Earnings inside -{EARNINGS_BLACKOUT_POST_DAYS}/"
            f"+{EARNINGS_BLACKOUT_AHEAD_DAYS} day blackout."
        )
    if not fund.debt_safe:
        return "Failed debt-service coverage floor (<2x)."
    if not fund.fcf_positive:
        return "Failed positive free-cash-flow / OCF check."
    if not fund.quality_ok:
        return "Failed operating-margin quality floor."
    if not news.headlines_clean:
        return f"Negative news velocity: {news.notes}"
    if macro.is_extreme_greed:
        return f"Extreme Greed regime ({macro.score:.0f}/100)."
    return None


def try_dispatch_buy(
    *,
    conn: sqlite3.Connection,
    cfg: Config,
    ticker: str,
    sector: str,
    bar: BarSnapshot,
    flags: SetupFlags,
    fund: FundamentalsResult,
    news: NewsVelocityResult,
    macro: MacroSentiment,
    vix_close: float,
    vix_mult: float,
    dynamic_risk_cad: float,
    alerted_sectors: set[str],
) -> bool:
    reject = _reject_fund_or_news(ticker, fund, news, macro)
    if reject:
        print(f" -> [REJECTED] {ticker}: {reject}")
        return False
    if sector in alerted_sectors:
        print(f" -> [SKIPPED] {ticker}: Sector {sector} limit ({MAX_OPEN_PER_SECTOR}) reached.")
        return False

    size = size_position(bar.close, bar.atr, dynamic_risk_cad)
    if size is None:
        return False

    result = record_if_allowed(conn, ticker, size, cfg.cooldown_days, alert_type=ALERT_BUY)
    if not result.inserted:
        print(f" -> [BUY COOLDOWN] {ticker}: {result.reason}")
        return False

    ctx = _build_alert_context(
        ticker,
        bar,
        flags,
        fund,
        macro,
        news,
        sector=sector,
        cooldown_days=cfg.cooldown_days,
        vix_close=vix_close,
        vix_mult=vix_mult,
        dynamic_risk_cad=dynamic_risk_cad,
        pullback_pct=flags.pullback_pct,
        candle_confirmed=flags.is_bounce_confirmed,
    )
    send_html_message(
        cfg.telegram_bot_token,
        cfg.telegram_chat_id,
        format_setup_html(size, ctx),
        context=f"setup {ticker}",
    )
    open_active_position(conn, ticker=ticker, size=size, sector=sector)
    alerted_sectors.add(sector)
    print(
        f" -> [POSITION SETUP] {ticker} [{sector}] PENDING_OPEN "
        f"@ signal ${size.entry:.2f} | risk ${dynamic_risk_cad:.0f} | sell {CASH_ETF}."
    )
    return True


def try_dispatch_inverse(
    *,
    conn: sqlite3.Connection,
    cfg: Config,
    ticker: str,
    sector: str,
    bar: BarSnapshot,
    flags: SetupFlags,
    fund: FundamentalsResult,
    news: NewsVelocityResult,
    macro: MacroSentiment,
    vix_close: float,
    vix_mult: float,
    dynamic_risk_cad: float,
    alerted_sectors: set[str],
    alerted_vehicles: set[str],
) -> bool:
    inverse_ticker = inverse_etf_for_sector(sector)
    if sector in alerted_sectors:
        print(f" -> [SKIPPED] {ticker}: Sector {sector} limit ({MAX_OPEN_PER_SECTOR}) reached.")
        return False
    if inverse_ticker in alerted_vehicles:
        print(f" -> [SKIPPED] {ticker}: Inverse vehicle {inverse_ticker} already used.")
        return False

    inv_close = load_inverse_quote(inverse_ticker)
    if inv_close is None:
        print(f" -> [SKIPPED] {inverse_ticker}: could not load inverse quote.")
        return False
    size = size_inverse_from_underlying(
        underlying_close=bar.close,
        underlying_atr=bar.atr,
        inverse_close=inv_close,
        leverage_factor=inverse_leverage(inverse_ticker),
        risk_cad=dynamic_risk_cad,
    )
    if size is None:
        return False

    result = record_if_allowed(
        conn, inverse_ticker, size, cfg.cooldown_days, alert_type=ALERT_INVERSE
    )
    if not result.inserted:
        print(f" -> [INVERSE COOLDOWN] {inverse_ticker}: {result.reason}")
        alerted_vehicles.add(inverse_ticker)
        return False

    ctx = _build_alert_context(
        ticker,
        bar,
        flags,
        fund,
        macro,
        news,
        sector=sector,
        cooldown_days=cfg.cooldown_days,
        vix_close=vix_close,
        vix_mult=vix_mult,
        dynamic_risk_cad=dynamic_risk_cad,
        pullback_pct=flags.pullback_pct,
        candle_confirmed=flags.is_rejection_confirmed,
    )
    send_html_message(
        cfg.telegram_bot_token,
        cfg.telegram_chat_id,
        format_inverse_html(size, ctx, inverse_ticker=inverse_ticker),
        context=f"inverse {inverse_ticker} via {ticker}",
    )
    open_active_position(conn, ticker=inverse_ticker, size=size, sector=sector)
    alerted_sectors.add(sector)
    alerted_vehicles.add(inverse_ticker)
    print(
        f" -> [INVERSE SETUP] BUY {inverse_ticker} via {ticker} [{sector}] "
        f"PENDING_OPEN @ signal ${size.entry:.2f} "
        f"(leverage {inverse_leverage(inverse_ticker):.0f}x)."
    )
    return True


def try_dispatch_watch(
    *,
    conn: sqlite3.Connection,
    cfg: Config,
    ticker: str,
    bar: BarSnapshot,
    flags: SetupFlags,
    fund: FundamentalsResult,
    news: NewsVelocityResult,
    macro: MacroSentiment,
    sector: str,
    vix_close: float,
    vix_mult: float,
    dynamic_risk_cad: float,
) -> None:
    watch_size = PositionSize(
        entry=bar.close,
        atr=bar.atr,
        r=max(bar.atr * WATCH_R_ATR_MULT, 0.01),
        stop=bar.sma_200 * (1.0 - WATCH_INVALIDATION_BUFFER),
        target_1=bar.sma_50,
        shares=0,
        t1_shares=0,
        runner_shares=0,
        risk_cad=0.0,
        max_limit_price=bar.close + (MAX_LIMIT_ATR_FRACTION * bar.atr),
    )
    result = record_if_allowed(conn, ticker, watch_size, cfg.cooldown_days, alert_type=ALERT_WATCH)
    if not result.inserted:
        print(f" -> [WATCH COOLDOWN] {ticker}: {result.reason}")
        return

    ctx = _build_alert_context(
        ticker,
        bar,
        flags,
        fund,
        macro,
        news,
        sector=sector,
        cooldown_days=cfg.cooldown_days,
        vix_close=vix_close,
        vix_mult=vix_mult,
        dynamic_risk_cad=dynamic_risk_cad,
        pullback_pct=flags.pullback_below_pct,
        candle_confirmed=flags.is_bounce_confirmed,
    )
    send_html_message(
        cfg.telegram_bot_token,
        cfg.telegram_chat_id,
        format_watchlist_html(
            ctx,
            current_price=bar.close,
            fund_score=40.0 if fund.passes_fundamentals else 0.0,
        ),
        context=f"watch {ticker}",
    )
    print(f" -> [WATCHLIST RADAR] {ticker} sent to Telegram.")
