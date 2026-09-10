#!/usr/bin/env python3
"""TSX scanner orchestrator: regime gates, sector caps, and Telegram dispatch."""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from typing import Callable, TypeVar

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError

from config import Config, load_config
from dashboard import generate_dashboard
from db import (
    ALERT_BUY,
    ALERT_INVERSE,
    ALERT_WATCH,
    STATUS_OPEN,
    STATUS_PENDING_OPEN,
    ActivePosition,
    active_sectors,
    close_active_position,
    confirm_pending_position,
    connect,
    list_active_positions,
    open_active_position,
    record_if_allowed,
    update_active_position,
)
from exits import ExitAction, evaluate_institutional_exit
from fundamentals import FundamentalsResult, days_to_next_earnings, evaluate_fundamentals
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
    format_exit_html,
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
    SECTOR_INVERSE_MAP,
    TSX_WATCHLIST,
    inverse_etf_for_sector,
    inverse_leverage,
    liquidity_filter_reason,
    ticker_sector,
)

T = TypeVar("T")
TICKER_PAUSE_SEC = 0.8
RATE_LIMIT_BACKOFFS = (5.0, 15.0, 30.0)


def _with_yahoo_retries(label: str, fn: Callable[[], T]) -> T:
    """Retry Yahoo calls when rate-limited; re-raise after final backoff."""
    last_exc: YFRateLimitError | None = None
    for attempt, wait_sec in enumerate(RATE_LIMIT_BACKOFFS, start=1):
        try:
            return fn()
        except YFRateLimitError as exc:
            last_exc = exc
            print(
                f" -> [RATE LIMIT] {label}: attempt {attempt}/"
                f"{len(RATE_LIMIT_BACKOFFS)}; sleeping {wait_sec:.0f}s"
            )
            time.sleep(wait_sec)
    assert last_exc is not None
    raise last_exc


def _fetch_history(ticker: str, period: str = "18mo") -> pd.DataFrame:
    return _with_yahoo_retries(
        ticker,
        lambda: yf.Ticker(ticker).history(period=period, interval="1d"),
    )


def _position_as_dict(pos: ActivePosition) -> dict:
    return {
        "ticker": pos.ticker,
        "entry_date": pos.entry_date,
        "entry_price": pos.entry_price,
        "initial_stop": pos.initial_stop,
        "current_stop": pos.current_stop,
        "shares_total": pos.shares_total,
        "shares_remaining": pos.shares_remaining,
        "is_de_risked": int(pos.is_de_risked),
        "bars_held": pos.bars_held,
        "sector": pos.sector,
        "status": pos.status,
        "signal_price": pos.signal_price,
    }


def _confirm_pending_opens(conn: sqlite3.Connection) -> None:
    """Promote PENDING_OPEN rows using the next session's open print."""
    pending = list_active_positions(conn, status=STATUS_PENDING_OPEN)
    if not pending:
        return

    print(f" -> [FILLS] Confirming {len(pending)} pending open order(s).")
    for pos in pending:
        ticker = pos.ticker
        try:
            df = _fetch_history(ticker, period="10d")
            if df.empty or "Open" not in df.columns:
                print(f" -> [FILL SKIP] {ticker}: no open print available yet.")
                continue

            try:
                signal_day = pd.Timestamp(pos.entry_date).date()
            except (ValueError, TypeError, OSError):
                signal_day = None

            fill_price: float | None = None
            fill_date: str | None = None
            if signal_day is not None:
                for ts, row in df.iterrows():
                    bar_day = pd.Timestamp(ts).date()
                    if bar_day > signal_day:
                        fill_price = float(row["Open"])
                        fill_date = pd.Timestamp(ts).isoformat()
                        break

            if fill_price is None or fill_price <= 0:
                print(
                    f" -> [FILL WAIT] {ticker}: waiting for next session open "
                    f"(signal {pos.entry_date})."
                )
                continue

            confirmed = confirm_pending_position(
                conn,
                ticker=ticker,
                fill_price=fill_price,
                fill_date=fill_date,
            )
            if confirmed is None:
                continue
            gap_pct = ((fill_price - pos.signal_price) / pos.signal_price) * 100.0
            print(
                f" -> [FILL] {ticker}: signal ${pos.signal_price:.2f} -> "
                f"open ${fill_price:.2f} ({gap_pct:+.2f}%) | "
                f"stop ${confirmed.initial_stop:.2f}"
            )
            time.sleep(TICKER_PAUSE_SEC)
        except YFRateLimitError as exc:
            print(f" -> [FILL ERROR] {ticker}: Yahoo rate limit after retries: {exc}")
            time.sleep(RATE_LIMIT_BACKOFFS[-1])
        except (
            ValueError,
            TypeError,
            KeyError,
            IndexError,
            OSError,
            RuntimeError,
        ) as exc:
            print(f" -> [FILL ERROR] {ticker}: {exc}")


def _manage_open_positions(
    conn: sqlite3.Connection,
    cfg: Config,
    *,
    macro_regime_is_bull: bool,
) -> None:
    """Confirm pending fills, then evaluate tranche exits on OPEN positions."""
    _confirm_pending_opens(conn)

    positions = list_active_positions(conn, status=STATUS_OPEN)
    if not positions:
        print(" -> [EXITS] No open positions to manage.")
        return

    inverse_vehicles = set(SECTOR_INVERSE_MAP.values())
    print(f" -> [EXITS] Evaluating {len(positions)} open position(s).")
    for pos in positions:
        ticker = pos.ticker
        try:
            df = _fetch_history(ticker, period="6mo")
            if df.empty or len(df) < 50:
                print(f" -> [EXIT SKIP] {ticker}: insufficient history for exit gates.")
                continue

            is_inverse = ticker in inverse_vehicles
            days_earn: int | None = None
            if not is_inverse:
                ticker_obj = yf.Ticker(ticker)
                days_earn = _with_yahoo_retries(
                    f"{ticker} earnings horizon",
                    lambda t=ticker_obj: days_to_next_earnings(t),
                )
            signal, updated = evaluate_institutional_exit(
                _position_as_dict(pos),
                df,
                days_earn,
                macro_regime_is_bull,
                is_inverse_vehicle=is_inverse,
            )

            if signal.action == ExitAction.HOLD:
                update_active_position(
                    conn,
                    ticker=ticker,
                    current_stop=float(updated["current_stop"]),
                    shares_remaining=int(updated["shares_remaining"]),
                    is_de_risked=bool(updated["is_de_risked"]),
                    bars_held=int(updated["bars_held"]),
                )
                print(
                    f" -> [HOLD] {ticker}: stop=${updated['current_stop']:.2f} "
                    f"bars={updated['bars_held']} "
                    f"de_risked={bool(updated['is_de_risked'])} "
                    f"qty={updated['shares_remaining']}"
                )
            elif signal.action == ExitAction.PARTIAL_SCALE:
                remaining = int(updated["shares_remaining"])
                if remaining <= 0:
                    send_html_message(
                        cfg.telegram_bot_token,
                        cfg.telegram_chat_id,
                        format_exit_html(
                            ticker=ticker,
                            action=signal.action.value,
                            entry_price=pos.entry_price,
                            exit_price=signal.exit_price,
                            shares_to_sell=signal.shares_to_sell,
                            shares_remaining=0,
                            new_stop_price=0.0,
                            message=signal.reason,
                            cash_etf=CASH_ETF,
                        ),
                    )
                    close_active_position(conn, ticker)
                    print(
                        f" -> [EXIT] {ticker}: scale emptied book "
                        f"@ ${signal.exit_price:.2f}"
                    )
                else:
                    update_active_position(
                        conn,
                        ticker=ticker,
                        current_stop=float(updated["current_stop"]),
                        shares_remaining=remaining,
                        is_de_risked=True,
                        bars_held=int(updated["bars_held"]),
                    )
                    send_html_message(
                        cfg.telegram_bot_token,
                        cfg.telegram_chat_id,
                        format_exit_html(
                            ticker=ticker,
                            action=signal.action.value,
                            entry_price=pos.entry_price,
                            exit_price=signal.exit_price,
                            shares_to_sell=signal.shares_to_sell,
                            shares_remaining=remaining,
                            new_stop_price=signal.new_stop_price,
                            message=signal.reason,
                            cash_etf=CASH_ETF,
                        ),
                    )
                    print(
                        f" -> [SCALE] {ticker}: sold {signal.shares_to_sell} @ "
                        f"${signal.exit_price:.2f}; runner={remaining} "
                        f"stop=${signal.new_stop_price:.2f}"
                    )
            else:
                send_html_message(
                    cfg.telegram_bot_token,
                    cfg.telegram_chat_id,
                    format_exit_html(
                        ticker=ticker,
                        action=signal.action.value,
                        entry_price=pos.entry_price,
                        exit_price=signal.exit_price,
                        shares_to_sell=signal.shares_to_sell,
                        shares_remaining=0,
                        new_stop_price=0.0,
                        message=signal.reason,
                        cash_etf=CASH_ETF,
                    ),
                )
                close_active_position(conn, ticker)
                print(
                    f" -> [EXIT] {ticker}: {signal.action.value} "
                    f"sold {signal.shares_to_sell} @ ${signal.exit_price:.2f}"
                )
            time.sleep(TICKER_PAUSE_SEC)
        except YFRateLimitError as exc:
            print(f" -> [EXIT ERROR] {ticker}: Yahoo rate limit after retries: {exc}")
            time.sleep(RATE_LIMIT_BACKOFFS[-1])
        except (
            ValueError,
            TypeError,
            KeyError,
            IndexError,
            OSError,
            RuntimeError,
        ) as exc:
            print(f" -> [EXIT ERROR] {ticker}: {exc}")



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
    open_active_position(conn, ticker=ticker, size=size, sector=sector)
    alerted_sectors.add(sector)
    print(
        f" -> [POSITION SETUP] {ticker} [{sector}] PENDING_OPEN "
        f"@ signal ${size.entry:.2f} | risk ${dynamic_risk_cad:.0f} | sell {CASH_ETF}."
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

    # Translate underlying ATR risk onto the inverse, scaled by |leverage|.
    leverage_factor = inverse_leverage(inverse_ticker)
    underlying_stop_pct = (1.5 * bar.atr) / bar.close
    inv_stop_pct = underlying_stop_pct * leverage_factor
    inv_atr = (inv_close * inv_stop_pct) / 1.5
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
    open_active_position(conn, ticker=inverse_ticker, size=size, sector=sector)
    alerted_sectors.add(sector)
    alerted_vehicles.add(inverse_ticker)
    print(
        f" -> [INVERSE SETUP] BUY {inverse_ticker} via {ticker} [{sector}] "
        f"PENDING_OPEN @ signal ${size.entry:.2f} "
        f"(leverage {inverse_leverage(inverse_ticker):.0f}x)."
    )
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
        max_limit_price=bar.close + (0.15 * bar.atr),
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
    alerted_sectors: set[str] = set(active_sectors(conn))
    alerted_vehicles: set[str] = set()
    setups_dispatched = 0
    dashboard_cards: list[dict] = []

    # Placeholder rows for names that fail the technical pre-screen (no Yahoo I/O).
    dummy_fund = FundamentalsResult(
        earnings_conflict=False,
        debt_safe=True,
        fcf_positive=True,
        quality_ok=True,
        notes="Skipped API Fetch (Failed Tech Gates)",
        score=0.0,
        metadata_complete=False,
    )
    dummy_news = NewsVelocityResult(
        headlines_clean=True,
        hit_count=0,
        notes="Skipped API Fetch (Failed Tech Gates)",
    )

    print(f"Current Market Regime: {market_regime}")
    print(
        f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}] "
        f"Scanning {len(TSX_WATCHLIST)} TSX tickers | "
        f"Base risk ${cfg.portfolio_risk_cad:.0f} x VIX {vix_mult:.2f} "
        f"= ${dynamic_risk_cad:.0f} (VIX {vix_close:.1f}) | "
        f"Sentiment: {macro.rating} {macro.score:.0f}/100 | "
        f"XIU {market_regime} 3M {bench.roc63 * 100:+.2f}%"
    )
    if alerted_sectors:
        print(
            " -> [BOOK] Active sector sleeves: " + ", ".join(sorted(alerted_sectors))
        )
    if is_extreme_greed:
        print(" -> [REGIME VETO] Extreme Greed: new long setups are blocked.")
    if market_regime == "BEAR":
        print(
            f" -> [BEAR REGIME] {BENCHMARK_TICKER} below 200 SMA "
            f"(${bench.close:.2f} < ${bench.sma200:.2f}). Scanning for inverse."
        )

    try:
        _manage_open_positions(
            conn,
            cfg,
            macro_regime_is_bull=market_regime == "BULL",
        )
        alerted_sectors = set(active_sectors(conn))

        # Global portfolio heat: block new buys once open risk hits 6.0R.
        # Free rolls (stop >= entry after 1.5R de-risk) count as 0.0R.
        MAX_PORTFOLIO_HEAT_R = 6.0
        current_open_r = 0.0
        unit_risk = cfg.portfolio_risk_cad
        if unit_risk > 0:
            for pos in list_active_positions(conn):
                risk_per_share = max(0.0, pos.entry_price - pos.current_stop)
                open_risk_cad = risk_per_share * pos.shares_remaining
                current_open_r += open_risk_cad / unit_risk
        heat_veto = current_open_r >= MAX_PORTFOLIO_HEAT_R
        if heat_veto:
            print(
                f" -> [HEAT VETO] Portfolio at {current_open_r:.1f}R open risk. "
                "New buys blocked."
            )
        elif current_open_r > 0:
            print(
                f" -> [HEAT] Open portfolio risk: "
                f"{current_open_r:.1f}R / {MAX_PORTFOLIO_HEAT_R:.0f}R"
            )

        for ticker in TSX_WATCHLIST:
            try:
                # 1. Price history first (1 Yahoo call per ticker).
                df = _fetch_history(ticker)
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

                # 2. Vectorized technical math (no extra network I/O).
                df = add_technical_indicators(df)
                bar = snapshot_from_bar(df.iloc[-1])
                flags = evaluate_setup_flags(bar, bench.roc63)
                sector = ticker_sector(ticker)
                liquidity_note = liquidity_filter_reason(df) or ""
                illiquid = bool(liquidity_note)

                # 3. Technical pre-screen.
                is_tech_buy = (
                    market_regime == "BULL"
                    and flags.is_macro_bullish
                    and flags.is_in_pullback
                    and flags.is_rs_leader
                    and flags.is_bounce_confirmed
                    and flags.is_bull_bulletproof
                    and not illiquid
                )
                is_tech_inverse = (
                    market_regime == "BEAR"
                    and flags.is_macro_bearish
                    and flags.is_at_resistance
                    and flags.is_rs_laggard
                    and flags.is_rejection_confirmed
                    and flags.is_bear_bulletproof
                    and not illiquid
                )
                is_tech_watch = (
                    market_regime == "BULL"
                    and flags.is_macro_bullish
                    and not flags.is_in_pullback
                    and flags.dist_to_200_sma_pct <= 3.0
                    and not is_extreme_greed
                    and not illiquid
                )
                needs_deep_scan = is_tech_buy or is_tech_inverse or is_tech_watch

                # 4. Inverted funnel: fundamentals/news only if technicals arm.
                if needs_deep_scan:
                    ticker_obj = yf.Ticker(ticker)
                    fund = _with_yahoo_retries(
                        f"{ticker} fundamentals",
                        lambda t=ticker_obj: evaluate_fundamentals(t),
                    )
                    news = _with_yahoo_retries(
                        f"{ticker} news",
                        lambda t=ticker_obj: evaluate_news_velocity(t),
                    )
                else:
                    fund = dummy_fund
                    news = dummy_news

                # 5. Final confirmation with fundamental / news gates.
                is_valid_buy = (
                    is_tech_buy
                    and fund.passes_fundamentals
                    and news.headlines_clean
                    and not fund.earnings_conflict
                )
                is_valid_inverse = (
                    is_tech_inverse
                    and fund.passes_fundamentals
                    and news.headlines_clean
                    and not fund.earnings_conflict
                )
                is_watch = (
                    is_tech_watch
                    and fund.passes_fundamentals
                    and news.headlines_clean
                )

                if is_valid_buy or is_valid_inverse:
                    category = "setup"
                elif is_watch:
                    category = "watch"
                else:
                    category = "neutral"

                dashboard_cards.append(
                    _dashboard_card(
                        ticker,
                        sector,
                        bar,
                        flags,
                        fund,
                        news,
                        category,
                        extra_note=liquidity_note,
                    )
                )

                print(
                    f" {ticker} [{sector}]: RS={flags.rs_vs_xiu:+.1f}% "
                    f"bounce={flags.is_bounce_confirmed} "
                    f"RVOL={flags.rvol:.2f} ADX={bar.adx:.1f} "
                    f"slope={'UP' if flags.is_slope_positive else 'DN'} "
                    f"deep_scan={needs_deep_scan}"
                    f"{' ILLIQUID' if illiquid else ''}"
                )

                if illiquid:
                    print(f" -> [SKIP] {ticker}: {liquidity_note}")
                    time.sleep(TICKER_PAUSE_SEC)
                    continue

                if is_valid_buy and not heat_veto:
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
                elif needs_deep_scan and fund.earnings_conflict:
                    print(f" -> [SKIP] {ticker}: {fund.notes}")
                elif flags.is_macro_bullish and flags.is_in_pullback:
                    print(
                        f" -> [NO TIER] {ticker}: "
                        f"pullback={flags.pullback_pct:.2f}% "
                        f"RS={flags.rs_vs_xiu:+.1f}% "
                        f"bounce={flags.is_bounce_confirmed} "
                        f"RVOL={flags.rvol:.2f} ADX={bar.adx:.1f} "
                        f"bp={flags.is_bull_bulletproof} "
                        f"deep_scan={needs_deep_scan}"
                    )

                time.sleep(TICKER_PAUSE_SEC)

            except YFRateLimitError as exc:
                reason = f"Yahoo rate limit after retries: {exc}"
                print(f"Error scanning {ticker}: {reason}")
                dashboard_cards.append(_skipped_dashboard_card(ticker, reason))
                time.sleep(RATE_LIMIT_BACKOFFS[-1])
            except (
                ValueError,
                TypeError,
                KeyError,
                IndexError,
                OSError,
                RuntimeError,
            ) as exc:
                print(f"Error scanning {ticker}: {exc}")
                dashboard_cards.append(
                    _skipped_dashboard_card(ticker, f"Scan error: {exc}")
                )

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
