#!/usr/bin/env python3
"""TSX scan sequencer: load context, manage the book, screen, dispatch, publish."""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone

import pandas as pd
from yfinance.exceptions import YFRateLimitError

from cards import DashboardCard, daily_change_pct, dashboard_card, skipped_dashboard_card
from config import Config, load_config
from dashboard import generate_dashboard
from db import (
    STATUS_OPEN,
    STATUS_PENDING_OPEN,
    ActivePosition,
    active_sectors,
    close_active_position,
    confirm_pending_position,
    connect,
    list_active_positions,
    update_active_position,
)
from dispatch import try_dispatch_buy, try_dispatch_inverse, try_dispatch_watch
from exits import ExitAction, evaluate_institutional_exit
from fundamentals import FundamentalsResult, days_to_next_earnings, evaluate_fundamentals
from gates import arm_setup, screen_technical
from indicators import (
    add_technical_indicators,
    evaluate_setup_flags,
    snapshot_from_bar,
)
from market_data import (
    RATE_LIMIT_BACKOFFS,
    call_ticker,
    safe_fetch_batch,
)
from regime import get_vix_multiplier, load_benchmark_state
from sentiment import (
    NewsVelocityResult,
    evaluate_news_velocity,
    get_macro_sentiment,
)
from sizing import (
    evaluate_heat_veto,
    stops_after_pending_fill,
)
from telegram_notify import (
    format_exit_html,
    format_idle_cash_html,
    send_html_message,
)
from universe import (
    BENCHMARK_TICKER,
    CASH_ETF,
    SECTOR_INVERSE_MAP,
    TSX_WATCHLIST,
    liquidity_filter_reason,
    ticker_sector,
)

TICKER_PAUSE_SEC = 0.8


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
    tickers = [pos.ticker for pos in pending]
    histories, err = safe_fetch_batch(tickers, period="10d", error_label="FILL ERROR")
    if err is not None:
        return

    for pos in pending:
        ticker = pos.ticker
        try:
            df = histories.get(ticker, pd.DataFrame())
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

            new_initial_stop, new_current_stop = stops_after_pending_fill(
                fill_price=fill_price,
                old_entry=pos.entry_price,
                old_initial_stop=pos.initial_stop,
            )
            confirmed = confirm_pending_position(
                conn,
                ticker=ticker,
                fill_price=fill_price,
                initial_stop=new_initial_stop,
                current_stop=new_current_stop,
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
    tickers = [pos.ticker for pos in positions]
    histories, err = safe_fetch_batch(tickers, period="6mo", error_label="EXIT ERROR")
    if err is not None:
        return

    for pos in positions:
        ticker = pos.ticker
        try:
            df = histories.get(ticker, pd.DataFrame())
            if df.empty or len(df) < 50:
                print(f" -> [EXIT SKIP] {ticker}: insufficient history for exit gates.")
                continue

            is_inverse = ticker in inverse_vehicles
            days_earn: int | None = None
            if not is_inverse:
                days_earn = call_ticker(
                    f"{ticker} earnings horizon",
                    ticker,
                    days_to_next_earnings,
                )
                time.sleep(TICKER_PAUSE_SEC)
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
                    # Telegram False is non-fatal — book update always proceeds.
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
                        context=f"exit {ticker}",
                    )
                    close_active_position(conn, ticker)
                    print(f" -> [EXIT] {ticker}: scale emptied book @ ${signal.exit_price:.2f}")
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
                        context=f"scale {ticker}",
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
                    context=f"exit {ticker}",
                )
                close_active_position(conn, ticker)
                print(
                    f" -> [EXIT] {ticker}: {signal.action.value} "
                    f"sold {signal.shares_to_sell} @ ${signal.exit_price:.2f}"
                )
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
    dashboard_cards: list[DashboardCard] = []

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
        print(" -> [BOOK] Active sector sleeves: " + ", ".join(sorted(alerted_sectors)))
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

        # Global portfolio heat: block new buys once open risk hits the cap.
        # Free rolls (stop >= entry after 1.5R de-risk) count as 0.0R.
        heat = evaluate_heat_veto(list_active_positions(conn), cfg.portfolio_risk_cad)
        if heat.log_line is not None:
            print(heat.log_line)

        print(f" -> [BATCH] Downloading OHLCV for {len(TSX_WATCHLIST)} tickers...")
        histories, batch_err = safe_fetch_batch(TSX_WATCHLIST, error_label="BATCH ERROR")
        batch_ok = batch_err is None
        if batch_err is not None:
            for ticker in TSX_WATCHLIST:
                dashboard_cards.append(skipped_dashboard_card(ticker, batch_err))

        for ticker in TSX_WATCHLIST if batch_ok else ():
            try:
                # 1. In-memory OHLCV from the batch download.
                df = histories.get(ticker, pd.DataFrame())
                if df.empty:
                    reason = "No price history from Yahoo."
                    print(f" -> [SKIP] {ticker}: {reason}")
                    dashboard_cards.append(skipped_dashboard_card(ticker, reason))
                    continue
                if len(df) < 200:
                    reason = f"Insufficient history ({len(df)} bars, need 200)."
                    print(f" -> [SKIP] {ticker}: {reason}")
                    close = float(df["Close"].iloc[-1]) if "Close" in df.columns else 0.0
                    dashboard_cards.append(
                        skipped_dashboard_card(
                            ticker,
                            reason,
                            close=close,
                            change_pct=daily_change_pct(df, close),
                        )
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
                tech = screen_technical(
                    market_regime=market_regime,
                    flags=flags,
                    illiquid=illiquid,
                    is_extreme_greed=is_extreme_greed,
                )

                # 4. Inverted funnel: fundamentals/news only if technicals arm.
                if tech.needs_deep_scan:
                    fund = call_ticker(
                        f"{ticker} fundamentals",
                        ticker,
                        evaluate_fundamentals,
                    )
                    news = call_ticker(
                        f"{ticker} news",
                        ticker,
                        evaluate_news_velocity,
                    )
                    time.sleep(TICKER_PAUSE_SEC)
                else:
                    fund = dummy_fund
                    news = dummy_news

                # 5. Final confirmation with fundamental / news gates.
                armed = arm_setup(
                    tech,
                    passes_fundamentals=fund.passes_fundamentals,
                    headlines_clean=news.headlines_clean,
                    earnings_conflict=fund.earnings_conflict,
                )

                dashboard_cards.append(
                    dashboard_card(
                        ticker,
                        sector,
                        bar,
                        df,
                        flags,
                        fund,
                        news,
                        armed.category,
                        extra_note=liquidity_note,
                    )
                )

                print(
                    f" {ticker} [{sector}]: RS={flags.rs_vs_xiu:+.1f}% "
                    f"bounce={flags.is_bounce_confirmed} "
                    f"RVOL={flags.rvol:.2f} ADX={bar.adx:.1f} "
                    f"slope={'UP' if flags.is_slope_positive else 'DN'} "
                    f"deep_scan={tech.needs_deep_scan}"
                    f"{' ILLIQUID' if illiquid else ''}"
                )

                if illiquid:
                    print(f" -> [SKIP] {ticker}: {liquidity_note}")
                    continue

                if armed.is_valid_buy and not heat.veto:
                    if try_dispatch_buy(
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
                elif armed.is_valid_inverse:
                    if try_dispatch_inverse(
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
                elif armed.is_watch:
                    try_dispatch_watch(
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
                elif tech.needs_deep_scan and fund.earnings_conflict:
                    print(f" -> [SKIP] {ticker}: {fund.notes}")
                elif flags.is_macro_bullish and flags.is_in_pullback:
                    print(
                        f" -> [NO TIER] {ticker}: "
                        f"pullback={flags.pullback_pct:.2f}% "
                        f"RS={flags.rs_vs_xiu:+.1f}% "
                        f"bounce={flags.is_bounce_confirmed} "
                        f"RVOL={flags.rvol:.2f} ADX={bar.adx:.1f} "
                        f"bp={flags.is_bull_bulletproof} "
                        f"deep_scan={tech.needs_deep_scan}"
                    )

            except YFRateLimitError as exc:
                reason = f"Yahoo rate limit after retries: {exc}"
                print(f"Error scanning {ticker}: {reason}")
                dashboard_cards.append(skipped_dashboard_card(ticker, reason))
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
                dashboard_cards.append(skipped_dashboard_card(ticker, f"Scan error: {exc}"))

        if setups_dispatched == 0:
            sent = send_html_message(
                cfg.telegram_bot_token,
                cfg.telegram_chat_id,
                format_idle_cash_html(
                    cash_etf=CASH_ETF,
                    vix_close=vix_close,
                    vix_mult=vix_mult,
                    is_bear=market_regime == "BEAR",
                ),
                context="idle cash",
            )
            if sent:
                print(f" -> [IDLE CASH] No setups. Remain 100% in {CASH_ETF}.")
            else:
                print(
                    f" -> [IDLE CASH] No setups. Remain 100% in {CASH_ETF} "
                    "(Telegram idle alert not delivered)."
                )

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
