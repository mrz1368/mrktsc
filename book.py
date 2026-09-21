"""Active-book maintenance: pending-open fills and open-position exits."""

from __future__ import annotations

import sqlite3
import time

import pandas as pd
from yfinance.exceptions import YFRateLimitError

from config import Config
from db import (
    STATUS_OPEN,
    STATUS_PENDING_OPEN,
    ActivePosition,
    close_active_position,
    confirm_pending_position,
    list_active_positions,
    pending_fill_abort_reason,
    update_active_position,
)
from exits import ExitAction, evaluate_institutional_exit
from fundamentals import days_to_next_earnings
from market_data import (
    RATE_LIMIT_BACKOFFS,
    call_ticker,
    safe_fetch_batch,
)
from sizing import stops_after_pending_fill
from telegram_notify import format_exit_html, send_html_message
from universe import CASH_ETF, SECTOR_INVERSE_MAP

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


def _abort_pending_fill(
    conn: sqlite3.Connection,
    cfg: Config,
    *,
    ticker: str,
    reason: str,
) -> None:
    """Cancel a PENDING_OPEN that failed fill-safety checks; Telegram is best-effort."""
    close_active_position(conn, ticker)
    print(f" -> [FILL ABORT] {ticker}: {reason}; PENDING_OPEN cancelled.")
    send_html_message(
        cfg.telegram_bot_token,
        cfg.telegram_chat_id,
        f"<b>FILL ABORT</b> {ticker}: {reason}. Pending open cancelled.",
        context=f"fill abort {ticker}",
    )


def confirm_pending_opens(conn: sqlite3.Connection, cfg: Config) -> None:
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
                        raw_open = row["Open"]
                        try:
                            fill_price = float(raw_open)
                        except (TypeError, ValueError):
                            fill_price = float("nan")
                        fill_date = pd.Timestamp(ts).isoformat()
                        break

            if fill_price is None:
                print(
                    f" -> [FILL WAIT] {ticker}: waiting for next session open "
                    f"(signal {pos.entry_date})."
                )
                continue

            abort = pending_fill_abort_reason(
                fill_price=fill_price,
                stop=pos.initial_stop,
                max_limit_price=pos.max_limit_price,
            )
            if abort is not None:
                _abort_pending_fill(conn, cfg, ticker=ticker, reason=abort)
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


def manage_open_positions(
    conn: sqlite3.Connection,
    cfg: Config,
    *,
    macro_regime_is_bull: bool,
) -> None:
    """Confirm pending fills, then evaluate tranche exits on OPEN positions."""
    confirm_pending_opens(conn, cfg)

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
