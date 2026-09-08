#!/usr/bin/env python3
"""Phase 1 smoke test: sizing, SQLite cooldown, and Telegram HTML delivery."""

from __future__ import annotations

import math
import sys

from config import load_config
from db import connect, count_signals, record_if_allowed
from sizing import size_position
from telegram_notify import AlertContext, format_setup_html, send_html_message

# Synthetic SHOP.TO fixture (no market download).
TICKER = "SHOP.TO"
ENTRY = 100.0
ATR = 2.0
SMA50 = 101.0
SMA200 = 95.0
RSI14 = 45.0
PULLBACK_PCT = 0.99
EXPECTED_R = 3.0
EXPECTED_STOP = 97.0
EXPECTED_T1 = 104.50
EMA20 = 100.50
RELATIVE_STRENGTH = 2.4


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FAIL: {message}")


def main() -> int:
    cfg = load_config()
    print(f"PORTFOLIO_RISK_CAD={cfg.portfolio_risk_cad:.0f}")
    print(f"SIGNALS_DB={cfg.signals_db}")
    print(f"COOLDOWN_DAYS={cfg.cooldown_days}")

    expected_shares = math.floor(cfg.portfolio_risk_cad / EXPECTED_R)
    _assert(expected_shares >= 3, "PORTFOLIO_RISK_CAD is too small to size a 1/3 scale-out")

    size = size_position(ENTRY, ATR, cfg.portfolio_risk_cad)
    _assert(size is not None, "sizing returned None")
    expected_t1 = math.ceil(expected_shares / 3.0)
    expected_runner = expected_shares - expected_t1
    print(
        f"sizing: entry={size.entry:.2f} ATR={size.atr:.2f} R={size.r:.2f} "
        f"stop={size.stop:.2f} T1={size.target_1:.2f} shares={size.shares} "
        f"t1_shares={size.t1_shares} runner={size.runner_shares}"
    )
    _assert(size.r == EXPECTED_R, f"R expected {EXPECTED_R}, got {size.r}")
    _assert(
        size.shares == expected_shares,
        f"shares expected {expected_shares}, got {size.shares}",
    )
    _assert(size.stop == EXPECTED_STOP, f"stop expected {EXPECTED_STOP}, got {size.stop}")
    _assert(
        size.target_1 == EXPECTED_T1,
        f"T1 expected {EXPECTED_T1}, got {size.target_1}",
    )
    _assert(
        size.t1_shares == expected_t1,
        f"t1_shares expected {expected_t1}, got {size.t1_shares}",
    )
    _assert(
        size.runner_shares == expected_runner,
        f"runner_shares expected {expected_runner}, got {size.runner_shares}",
    )

    conn = connect(cfg.signals_db)
    try:
        conn.execute("DELETE FROM signals WHERE ticker = ?", (TICKER,))
        conn.commit()

        first = record_if_allowed(conn, TICKER, size, cfg.cooldown_days)
        print(f"first insert: inserted={first.inserted} ({first.reason})")
        _assert(first.inserted, f"first insert should succeed: {first.reason}")

        second = record_if_allowed(conn, TICKER, size, cfg.cooldown_days)
        print(f"second insert: inserted={second.inserted} ({second.reason})")
        _assert(not second.inserted, "cooldown did not block duplicate alert")
        _assert("cooldown" in second.reason.lower(), f"unexpected block reason: {second.reason}")

        rows = count_signals(conn, TICKER)
        print(f"signals.db {TICKER} rows={rows}")
        _assert(rows >= 1, "expected at least one recorded signal")
    finally:
        conn.close()

    ctx = AlertContext(
        ticker=TICKER,
        sma50=SMA50,
        sma200=SMA200,
        rsi14=RSI14,
        ema20=EMA20,
        pullback_pct=PULLBACK_PCT,
        relative_strength=RELATIVE_STRENGTH,
        sentiment_rating="Fear",
        sentiment_score=38.0,
        fund_notes="Strong Operating Margin (12.5%), Revenue Growth (+21.0%)",
        adx14=28.5,
        rvol=1.45,
        sma50_slope=0.42,
        candle_confirmed=True,
        has_earnings_conflict=False,
        debt_safe=True,
        fcf_positive=True,
        quality_ok=True,
        headlines_clean=True,
        news_notes="No negative headline cluster (7d)",
        cooldown_days=cfg.cooldown_days,
    )
    html_body = format_setup_html(size, ctx)
    _assert("✅" in html_body, "setup card should include validation checkmarks")
    _assert("Balance Sheet" in html_body, "setup card should show debt gate")
    _assert("Cash Flow" in html_body, "setup card should show FCF gate")
    _assert("News Velocity" in html_body, "setup card should show news gate")
    _assert("❌" not in html_body, "passing fixture should have all green ticks")
    send_html_message(cfg.telegram_bot_token, cfg.telegram_chat_id, html_body)
    print("Telegram HTML setup sent.")
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
