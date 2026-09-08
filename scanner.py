#!/usr/bin/env python3
"""TSX scanner orchestrator: regime gates, sector caps, and Telegram dispatch."""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone

import yfinance as yf

from config import Config, load_config
from dashboard import generate_dashboard
from db import (
    ALERT_BUY,
    ALERT_INVERSE,
    ALERT_WATCH,
    connect,
    record_if_allowed,
)
from fundamentals import FundamentalsResult, evaluate_fundamentals
from indicators import (
    BarSnapshot,
    SetupFlags,
    add_technical_indicators,
    evaluate_setup_flags,
    snapshot_from_bar,
)
from regime import get_vix_multiplier, load_benchmark_state, load_inverse_quote
from sentiment import (
    MacroSentiment,
    NewsVelocityResult,
    evaluate_news_velocity,
    get_macro_sentiment,
)
from sizing import PositionSize, size_position
from telegram_notify import (
    AlertContext,
    format_idle_cash_html,
    format_inverse_html,
    format_setup_html,
    format_watchlist_html,
    send_html_message,
)
from universe import (
    BENCHMARK_TICKER,
    CASH_ETF,
    EARNINGS_BLACKOUT_AHEAD_DAYS,
    EARNINGS_BLACKOUT_POST_DAYS,
    MAX_OPEN_PER_SECTOR,
    MIN_AVG_VOLUME,
    TSX_WATCHLIST,
    inverse_etf_for_sector,
    ticker_sector,
)


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


def _dashboard_card(
    ticker: str,
    sector: str,
    bar: BarSnapshot,
    flags: SetupFlags,
    fund: FundamentalsResult,
    news: NewsVelocityResult,
    category: str,
    extra_note: str = "",
) -> dict:
    notes = fund.notes
    if extra_note:
        notes = f"{extra_note} {notes}".strip()
    return {
        "ticker": ticker,
        "sector": sector,
        "close": bar.close,
        "category": category,
        "adx": bar.adx,
        "rvol": flags.rvol,
        "sma50_slope": bar.sma_50_slope,
        "rs_vs_xiu": flags.rs_vs_xiu,
        "pullback_pct": flags.pullback_pct,
        "debt_safe": fund.debt_safe,
        "fcf_positive": fund.fcf_positive,
        "earnings_conflict": fund.earnings_conflict,
        "headlines_clean": news.headlines_clean,
        "fund_notes": notes,
    }


def _skipped_dashboard_card(ticker: str, reason: str, close: float = 0.0) -> dict:
    return {
        "ticker": ticker,
        "sector": ticker_sector(ticker),
        "close": close,
        "category": "neutral",
        "adx": 0.0,
        "rvol": 0.0,
        "sma50_slope": 0.0,
        "rs_vs_xiu": 0.0,
        "pullback_pct": 0.0,
        "debt_safe": False,
        "fcf_positive": False,
        "earnings_conflict": False,
        "headlines_clean": True,
        "fund_notes": reason,
    }


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


def _try_dispatch_buy(
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
        print(
            f" -> [SKIPPED] {ticker}: Sector {sector} limit "
            f"({MAX_OPEN_PER_SECTOR}) reached."
        )
        return False

    size = size_position(bar.close, bar.atr, dynamic_risk_cad)
    if size is None:
        return False

    result = record_if_allowed(
        conn, ticker, size, cfg.cooldown_days, alert_type=ALERT_BUY
    )
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
    )
    alerted_sectors.add(sector)
    print(
        f" -> [POSITION SETUP] {ticker} [{sector}] "
        f"risk ${dynamic_risk_cad:.0f} | sell {CASH_ETF}."
    )
    return True


def _try_dispatch_inverse(
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
        print(
            f" -> [SKIPPED] {ticker}: Sector {sector} limit "
            f"({MAX_OPEN_PER_SECTOR}) reached."
        )
        return False
    if inverse_ticker in alerted_vehicles:
        print(
            f" -> [SKIPPED] {ticker}: Inverse vehicle "
            f"{inverse_ticker} already used."
        )
        return False

    inv_close = load_inverse_quote(inverse_ticker)
    if inv_close is None:
        print(f" -> [SKIPPED] {inverse_ticker}: could not load inverse quote.")
        return False
    if bar.close <= 0 or bar.atr <= 0:
        return False

    stop_pct = (1.5 * bar.atr) / bar.close
    inv_atr = (inv_close * stop_pct) / 1.5
    size = size_position(inv_close, inv_atr, dynamic_risk_cad)
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
    )
    alerted_sectors.add(sector)
    alerted_vehicles.add(inverse_ticker)
    print(f" -> [INVERSE SETUP] BUY {inverse_ticker} via {ticker} [{sector}].")
    return True


def _try_dispatch_watch(
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
        r=max(bar.atr * 1.5, 0.01),
        stop=bar.sma_200 * 0.985,
        target_1=bar.sma_50,
        shares=0,
        t1_shares=0,
        runner_shares=0,
        risk_cad=0.0,
    )
    result = record_if_allowed(
        conn, ticker, watch_size, cfg.cooldown_days, alert_type=ALERT_WATCH
    )
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
    )
    print(f" -> [WATCHLIST RADAR] {ticker} sent to Telegram.")


def scan_market() -> None:
    cfg = load_config()
    conn = connect(cfg.signals_db)

    macro = get_macro_sentiment()
    vix_mult, vix_close = get_vix_multiplier()
    dynamic_risk_cad = cfg.portfolio_risk_cad * vix_mult
    bench = load_benchmark_state()
    market_regime = bench.market_regime
    is_extreme_greed = macro.is_extreme_greed
    alerted_sectors: set[str] = set()
    alerted_vehicles: set[str] = set()
    setups_dispatched = 0
    dashboard_cards: list[dict] = []

    print(f"Current Market Regime: {market_regime}")
    print(
        f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}] "
        f"Scanning {len(TSX_WATCHLIST)} TSX tickers | "
        f"Base risk ${cfg.portfolio_risk_cad:.0f} x VIX {vix_mult:.2f} "
        f"= ${dynamic_risk_cad:.0f} (VIX {vix_close:.1f}) | "
        f"Sentiment: {macro.rating} {macro.score:.0f}/100 | "
        f"XIU {market_regime} 3M {bench.roc63 * 100:+.2f}%"
    )
    if is_extreme_greed:
        print(" -> [REGIME VETO] Extreme Greed: new long setups are blocked.")
    if market_regime == "BEAR":
        print(
            f" -> [BEAR REGIME] {BENCHMARK_TICKER} below 200 SMA "
            f"(${bench.close:.2f} < ${bench.sma200:.2f}). Scanning for inverse."
        )

    try:
        for ticker in TSX_WATCHLIST:
            try:
                ticker_obj = yf.Ticker(ticker)
                df = ticker_obj.history(period="18mo", interval="1d")
                if df.empty:
                    reason = "No price history from Yahoo."
                    print(f" -> [SKIP] {ticker}: {reason}")
                    dashboard_cards.append(_skipped_dashboard_card(ticker, reason))
                    continue
                if len(df) < 200:
                    reason = f"Insufficient history ({len(df)} bars, need 200)."
                    print(f" -> [SKIP] {ticker}: {reason}")
                    close = float(df["Close"].iloc[-1]) if "Close" in df.columns else 0.0
                    dashboard_cards.append(
                        _skipped_dashboard_card(ticker, reason, close=close)
                    )
                    continue

                df = add_technical_indicators(df)
                bar = snapshot_from_bar(df.iloc[-1])

                flags = evaluate_setup_flags(bar, bench.roc63)
                fund = evaluate_fundamentals(ticker_obj)
                news = evaluate_news_velocity(ticker_obj)
                sector = ticker_sector(ticker)
                thin_volume = bar.vol_sma < MIN_AVG_VOLUME
                volume_note = (
                    f"Avg volume {bar.vol_sma:,.0f} below {MIN_AVG_VOLUME:,} floor."
                    if thin_volume
                    else ""
                )
                print(
                    f" {ticker} [{sector}]: RS={flags.rs_vs_xiu:+.1f}% "
                    f"bounce={flags.is_bounce_confirmed} "
                    f"RVOL={flags.rvol:.2f} ADX={bar.adx:.1f} "
                    f"slope={'UP' if flags.is_slope_positive else 'DN'} "
                    f"fund={'OK' if fund.passes_fundamentals else 'FAIL'} "
                    f"news={'OK' if news.headlines_clean else 'HOT'}"
                    f"{' EARNINGS BLACKOUT' if fund.earnings_conflict else ''}"
                    f"{' THIN VOLUME' if thin_volume else ''}"
                )

                is_valid_buy = (
                    market_regime == "BULL"
                    and flags.is_macro_bullish
                    and flags.is_in_pullback
                    and flags.is_rs_leader
                    and flags.is_bounce_confirmed
                    and flags.is_bull_bulletproof
                    and not thin_volume
                )
                is_valid_inverse = (
                    market_regime == "BEAR"
                    and flags.is_macro_bearish
                    and flags.is_at_resistance
                    and flags.is_rs_laggard
                    and flags.is_rejection_confirmed
                    and flags.is_bear_bulletproof
                    and not thin_volume
                )
                is_watch = (
                    market_regime == "BULL"
                    and flags.is_macro_bullish
                    and not flags.is_in_pullback
                    and flags.dist_to_200_sma_pct <= 3.0
                    and fund.passes_fundamentals
                    and news.headlines_clean
                    and not is_extreme_greed
                    and not thin_volume
                )

                category = "neutral"
                if is_valid_buy or is_valid_inverse:
                    category = "setup"
                elif is_watch:
                    category = "watch"
                dashboard_cards.append(
                    _dashboard_card(
                        ticker,
                        sector,
                        bar,
                        flags,
                        fund,
                        news,
                        category,
                        extra_note=volume_note,
                    )
                )

                if thin_volume:
                    print(f" -> [SKIP] {ticker}: {volume_note}")
                    time.sleep(0.3)
                    continue

                if is_valid_buy:
                    if _try_dispatch_buy(
                        conn=conn,
                        cfg=cfg,
                        ticker=ticker,
                        sector=sector,
                        bar=bar,
                        flags=flags,
                        fund=fund,
                        news=news,
                        macro=macro,
                        vix_close=vix_close,
                        vix_mult=vix_mult,
                        dynamic_risk_cad=dynamic_risk_cad,
                        alerted_sectors=alerted_sectors,
                    ):
                        setups_dispatched += 1
                elif is_valid_inverse:
                    if _try_dispatch_inverse(
                        conn=conn,
                        cfg=cfg,
                        ticker=ticker,
                        sector=sector,
                        bar=bar,
                        flags=flags,
                        fund=fund,
                        news=news,
                        macro=macro,
                        vix_close=vix_close,
                        vix_mult=vix_mult,
                        dynamic_risk_cad=dynamic_risk_cad,
                        alerted_sectors=alerted_sectors,
                        alerted_vehicles=alerted_vehicles,
                    ):
                        setups_dispatched += 1
                elif is_watch:
                    _try_dispatch_watch(
                        conn=conn,
                        cfg=cfg,
                        ticker=ticker,
                        bar=bar,
                        flags=flags,
                        fund=fund,
                        news=news,
                        macro=macro,
                        sector=sector,
                        vix_close=vix_close,
                        vix_mult=vix_mult,
                        dynamic_risk_cad=dynamic_risk_cad,
                    )
                elif fund.earnings_conflict:
                    print(f" -> [SKIP] {ticker}: {fund.notes}")
                elif flags.is_macro_bullish and flags.is_in_pullback:
                    print(
                        f" -> [NO TIER] {ticker}: "
                        f"pullback={flags.pullback_pct:.2f}% "
                        f"RS={flags.rs_vs_xiu:+.1f}% "
                        f"bounce={flags.is_bounce_confirmed} "
                        f"RVOL={flags.rvol:.2f} ADX={bar.adx:.1f} "
                        f"bp={flags.is_bull_bulletproof} "
                        f"fund={'OK' if fund.passes_fundamentals else 'FAIL'} "
                        f"news={'OK' if news.headlines_clean else 'HOT'} "
                        f"dist200={flags.dist_to_200_sma_pct:.2f}%"
                    )

                time.sleep(0.3)

            except (
                ValueError,
                TypeError,
                KeyError,
                IndexError,
                OSError,
                RuntimeError,
            ) as exc:
                print(f"Error scanning {ticker}: {exc}")

        if setups_dispatched == 0:
            send_html_message(
                cfg.telegram_bot_token,
                cfg.telegram_chat_id,
                format_idle_cash_html(
                    cash_etf=CASH_ETF,
                    vix_close=vix_close,
                    vix_mult=vix_mult,
                    is_bear=market_regime == "BEAR",
                ),
            )
            print(f" -> [IDLE CASH] No setups. Remain 100% in {CASH_ETF}.")

        generate_dashboard(
            cards=dashboard_cards,
            regime=market_regime,
            vix_val=vix_close,
            vix_mult=vix_mult,
            sentiment_score=macro.score,
            sentiment_rating=macro.rating,
            cash_etf=CASH_ETF,
        )

    finally:
        conn.close()
        print("Scan complete.")


if __name__ == "__main__":
    scan_market()
