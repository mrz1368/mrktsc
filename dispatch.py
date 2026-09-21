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
from gates import fund_news_reject_reason
from indicators import BarSnapshot, SetupFlags
from regime import load_inverse_quote
from sentiment import MacroSentiment, NewsVelocityResult
from sizing import (
    PositionSize,
    size_inverse_from_underlying,
    size_position,
    slippage_destroys_edge,
)
from telegram_notify import (
    AlertContext,
    format_inverse_html,
    format_setup_html,
    format_watchlist_html,
    send_html_message,
)
from thresholds import (
    ATR_STOP_MULT,
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
        metadata_complete=fund.metadata_complete,
    )


def _reject_fund_or_news(
    ticker: str,
    fund: FundamentalsResult,
    news: NewsVelocityResult,
    macro: MacroSentiment,
) -> str | None:
    _ = ticker
    return fund_news_reject_reason(
        earnings_conflict=fund.earnings_conflict,
        debt_safe=fund.debt_safe,
        fcf_positive=fund.fcf_positive,
        quality_ok=fund.quality_ok,
        headlines_clean=news.headlines_clean,
        is_extreme_greed=macro.is_extreme_greed,
        extreme_greed_score=macro.score,
        news_notes=news.notes,
    )


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

    size = size_position(
        bar.close,
        bar.atr,
        dynamic_risk_cad,
        addv=bar.addv,
        hist_vol=bar.hist_vol,
    )
    if size is None:
        r = ATR_STOP_MULT * bar.atr
        if slippage_destroys_edge(bar.close, r, bar.addv, bar.hist_vol):
            print(f" -> [REJECTED] {ticker}: Expected slippage/friction exceeds target edge.")
        return False

    result = record_if_allowed(conn, ticker, size, cfg.cooldown_days, alert_type=ALERT_BUY)
    if not result.inserted:
        print(f" -> [BUY COOLDOWN] {ticker}: {result.reason}")
        return False

    if not open_active_position(conn, ticker=ticker, size=size, sector=sector):
        print(
            f" -> [UPSERT REFUSED] {ticker}: live position already open "
            f"(shares_remaining > 0); skip booking."
        )
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
    sent = send_html_message(
        cfg.telegram_bot_token,
        cfg.telegram_chat_id,
        format_setup_html(size, ctx),
        context=f"setup {ticker}",
    )
    alerted_sectors.add(sector)
    if not sent:
        print(f" -> [TELEGRAM] setup {ticker}: alert not delivered; PENDING_OPEN still booked.")
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

    inv = load_inverse_quote(inverse_ticker)
    if inv is None:
        print(f" -> [SKIPPED] {inverse_ticker}: could not load inverse quote.")
        return False
    # Size and slip on the inverse vehicle's ADDV/hist_vol; underlying bar
    # still drives technical flags and ATR stop translation.
    size = size_inverse_from_underlying(
        underlying_close=bar.close,
        underlying_atr=bar.atr,
        inverse_close=inv.close,
        leverage_factor=inverse_leverage(inverse_ticker),
        risk_cad=dynamic_risk_cad,
        addv=inv.addv,
        hist_vol=inv.hist_vol,
    )
    if size is None:
        if bar.close > 0:
            inv_stop_pct = ((ATR_STOP_MULT * bar.atr) / bar.close) * inverse_leverage(
                inverse_ticker
            )
            inv_atr = (inv.close * inv_stop_pct) / ATR_STOP_MULT
            inv_r = ATR_STOP_MULT * inv_atr
            if slippage_destroys_edge(inv.close, inv_r, inv.addv, inv.hist_vol):
                print(
                    f" -> [REJECTED] {inverse_ticker}: "
                    "Expected slippage/friction exceeds target edge."
                )
        return False

    result = record_if_allowed(
        conn, inverse_ticker, size, cfg.cooldown_days, alert_type=ALERT_INVERSE
    )
    if not result.inserted:
        print(f" -> [INVERSE COOLDOWN] {inverse_ticker}: {result.reason}")
        alerted_vehicles.add(inverse_ticker)
        return False

    if not open_active_position(conn, ticker=inverse_ticker, size=size, sector=sector):
        print(
            f" -> [UPSERT REFUSED] {inverse_ticker}: live position already open "
            f"(shares_remaining > 0); skip booking."
        )
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
    sent = send_html_message(
        cfg.telegram_bot_token,
        cfg.telegram_chat_id,
        format_inverse_html(size, ctx, inverse_ticker=inverse_ticker),
        context=f"inverse {inverse_ticker} via {ticker}",
    )
    alerted_sectors.add(sector)
    alerted_vehicles.add(inverse_ticker)
    if not sent:
        print(
            f" -> [TELEGRAM] inverse {inverse_ticker}: alert not delivered; "
            "PENDING_OPEN still booked."
        )
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
) -> bool:
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
        pullback_pct=flags.pullback_below_pct,
        candle_confirmed=flags.is_bounce_confirmed,
    )
    sent = send_html_message(
        cfg.telegram_bot_token,
        cfg.telegram_chat_id,
        format_watchlist_html(
            ctx,
            current_price=bar.close,
            fund_score=40.0 if fund.passes_fundamentals else 0.0,
        ),
        context=f"watch {ticker}",
    )
    if sent:
        print(f" -> [WATCHLIST RADAR] {ticker} sent to Telegram.")
    else:
        print(f" -> [WATCHLIST RADAR] {ticker}: Telegram send failed; scan continues.")
    return True
