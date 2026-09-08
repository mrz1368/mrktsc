"""Send institutional-grade HTML-formatted TSX setup alerts via Telegram."""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from indicators import MIN_ADX, MIN_RVOL
from sizing import PositionSize
from universe import (
    EARNINGS_BLACKOUT_AHEAD_DAYS,
    EARNINGS_BLACKOUT_POST_DAYS,
    EXTREME_GREED_SCORE,
)

TELEGRAM_API = "https://api.telegram.org"
EARNINGS_CLEAR_TEXT = (
    f"CLEAR (Outside -{EARNINGS_BLACKOUT_POST_DAYS}/"
    f"+{EARNINGS_BLACKOUT_AHEAD_DAYS} day window)"
)
EARNINGS_WARN_TEXT = (
    f"WARNING (Inside -{EARNINGS_BLACKOUT_POST_DAYS}/"
    f"+{EARNINGS_BLACKOUT_AHEAD_DAYS} day window)"
)


@dataclass(frozen=True)
class AlertContext:
    """Shared fields for setup / inverse / watch Telegram cards."""

    ticker: str
    sma50: float
    sma200: float
    rsi14: float
    ema20: float
    pullback_pct: float
    relative_strength: float
    sentiment_rating: str
    sentiment_score: float
    fund_notes: str
    adx14: float
    rvol: float
    sma50_slope: float
    candle_confirmed: bool
    has_earnings_conflict: bool
    debt_safe: bool
    fcf_positive: bool
    quality_ok: bool
    headlines_clean: bool
    news_notes: str
    cooldown_days: int
    sector: str = ""
    vix_close: float | None = None
    vix_mult: float = 1.0
    dynamic_risk_cad: float | None = None
    cash_etf: str = "CASH.TO"


def _validation_icons(ctx: AlertContext, *, bearish: bool = False) -> dict[str, str]:
    slope_ok = ctx.sma50_slope < 0 if bearish else ctx.sma50_slope > 0
    rs_ok = ctx.relative_strength < 0 if bearish else ctx.relative_strength > 0
    return {
        "adx": "✅" if ctx.adx14 >= MIN_ADX else "❌",
        "rvol": "✅" if ctx.rvol >= MIN_RVOL else "❌",
        "slope": "✅" if slope_ok else "❌",
        "rs": "✅" if rs_ok else "❌",
        "candle": "✅" if ctx.candle_confirmed else "❌",
        "sentiment": "❌" if ctx.sentiment_score > EXTREME_GREED_SCORE else "✅",
        "earnings": "❌" if ctx.has_earnings_conflict else "✅",
        "debt": "✅" if ctx.debt_safe else "❌",
        "fcf": "✅" if ctx.fcf_positive else "❌",
        "quality": "✅" if ctx.quality_ok else "❌",
        "news": "✅" if ctx.headlines_clean else "❌",
    }


def _bulletproof_block(ctx: AlertContext, *, bearish: bool = False) -> str:
    icons = _validation_icons(ctx, bearish=bearish)
    slope_label = "Falling Resistance" if bearish else "Rising Support"
    rs_label = "Market Laggard" if bearish else "Market Leader"
    candle_label = (
        "Bearish rejection in lower 50% range"
        if bearish
        else "Bullish close in upper 50% range"
    )
    earnings_text = (
        EARNINGS_WARN_TEXT if ctx.has_earnings_conflict else EARNINGS_CLEAR_TEXT
    )
    sentiment_label = html.escape(ctx.sentiment_rating)
    if ctx.sentiment_score <= 45:
        sentiment_extra = " - Favorable for longs"
    elif ctx.sentiment_score > EXTREME_GREED_SCORE:
        sentiment_extra = " - Extreme Greed trap"
    else:
        sentiment_extra = ""
    debt_text = (
        "Debt Service Safe (>2x Coverage)"
        if ctx.debt_safe
        else "High Debt Burden (Coverage < 2x)"
    )
    fcf_text = (
        "Positive TTM Free Cash Flow"
        if ctx.fcf_positive
        else "Negative Free Cash Flow"
    )
    news_text = html.escape(ctx.news_notes)
    return (
        f"<b>BULLETPROOF VALIDATION</b>\n"
        f"{icons['adx']} <b>Trend (ADX):</b> {ctx.adx14:.1f} (Escaping Chop)\n"
        f"{icons['rvol']} <b>Volume (RVOL):</b> {ctx.rvol:.2f}x "
        f"(Institutional Footprint)\n"
        f"{icons['slope']} <b>50 SMA Slope:</b> {ctx.sma50_slope:+.2f} ({slope_label})\n"
        f"{icons['rs']} <b>RS vs XIU.TO:</b> {ctx.relative_strength:+.1f}% ({rs_label})\n"
        f"{icons['candle']} <b>Candle Trigger:</b> {candle_label}\n"
        f"\n"
        f"<b>FUNDAMENTAL &amp; REGIME</b>\n"
        f"{icons['sentiment']} <b>Sentiment:</b> {ctx.sentiment_score:.0f}/100 "
        f"({sentiment_label}{sentiment_extra})\n"
        f"{icons['news']} <b>News Velocity:</b> {news_text}\n"
        f"{icons['earnings']} <b>Earnings:</b> {earnings_text}\n"
        f"{icons['debt']} <b>Balance Sheet:</b> {debt_text}\n"
        f"{icons['fcf']} <b>Cash Flow:</b> {fcf_text}\n"
        f"{icons['quality']} <b>Quality:</b> <i>{html.escape(ctx.fund_notes)}</i>\n"
    )


def _footer(cooldown_days: int) -> str:
    return (
        f"⏰ <b>Generated:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} | "
        f"<b>Valid:</b> Next Session Open | <b>Cooldown:</b> {cooldown_days} days"
    )


def _order_table(size: PositionSize, *, label: str = "Limit Order") -> str:
    stop_pct = ((size.stop - size.entry) / size.entry) * 100
    t1_pct = ((size.target_1 - size.entry) / size.entry) * 100
    return html.escape(
        f"Level        Price      Execution Rule\n"
        f"──────────────────────────────────────────\n"
        f"Entry       ${size.entry:>7.2f}    {label}\n"
        f"Initial SL  ${size.stop:>7.2f}   -${size.r:>5.2f} ({stop_pct:>5.2f}%)\n"
        f"Target 1    ${size.target_1:>7.2f}    Sell {size.t1_shares} shs ({t1_pct:>+5.2f}%)\n"
        f"Runner      Trailing    {size.runner_shares} shs: Close < 20 EMA"
    )


def format_setup_html(size: PositionSize, ctx: AlertContext) -> str:
    safe_ticker = html.escape(ctx.ticker)
    capital = size.shares * size.entry
    risk_shown = (
        size.risk_cad if ctx.dynamic_risk_cad is None else ctx.dynamic_risk_cad
    )
    sector_line = (
        f"• <b>Sector sleeve:</b> {html.escape(ctx.sector)} (max 1 open)\n"
        if ctx.sector
        else ""
    )
    vix_line = ""
    if ctx.vix_close is not None:
        vix_line = (
            f"• <b>VIX:</b> {ctx.vix_close:.1f} ➔ risk x{ctx.vix_mult:.2f} "
            f"(${risk_shown:,.0f} CAD / trade)\n"
        )

    return (
        f"🟢 <b>POSITION SETUP | TSX: {safe_ticker}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>EXECUTION &amp; SCALE-OUT</b>\n"
        f"<pre>{_order_table(size)}</pre>\n"
        f"<b>ALLOCATION &amp; RISK</b>\n"
        f"• <b>Total Units:</b> <code>{size.shares}</code> shares (${capital:,.2f} CAD)\n"
        f"• <b>Treasury sweep:</b> Sell ~${capital:,.2f} CAD of "
        f"{html.escape(ctx.cash_etf)} to fund this ticket\n"
        f"{sector_line}"
        f"{vix_line}"
        f"• <b>Tranche 1:</b> Sell <b>{size.t1_shares}</b> shares at +1.5R "
        f"➔ Stop to Breakeven\n"
        f"• <b>Tranche 2:</b> Trail <b>{size.runner_shares}</b> shares with "
        f"20 EMA (${ctx.ema20:.2f})\n"
        f"\n"
        f"<b>SETUP CONTEXT</b>\n"
        f"• <b>Macro Trend:</b> Price above 30-wk &amp; 200-day SMA (${ctx.sma200:.2f})\n"
        f"• <b>Pullback:</b> {ctx.pullback_pct:.2f}% from 50 SMA | RSI {ctx.rsi14:.1f}\n"
        f"• <b>50 SMA:</b> ${ctx.sma50:.2f}\n"
        f"\n"
        f"{_bulletproof_block(ctx)}\n"
        f"{_footer(ctx.cooldown_days)}"
    )


def format_inverse_html(
    size: PositionSize,
    ctx: AlertContext,
    *,
    inverse_ticker: str,
) -> str:
    """Bear-regime card: buy a BetaPro inverse ETF after sector weakness."""
    safe_inv = html.escape(inverse_ticker)
    safe_und = html.escape(ctx.ticker)
    capital = size.shares * size.entry
    risk_shown = (
        size.risk_cad if ctx.dynamic_risk_cad is None else ctx.dynamic_risk_cad
    )
    vix_close = ctx.vix_close if ctx.vix_close is not None else 0.0
    checklist = _bulletproof_block(ctx, bearish=True)

    return (
        f"🔴 <b>INVERSE SETUP | BUY: {safe_inv} (via {safe_und} Weakness)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>EXECUTION &amp; SCALE-OUT</b>\n"
        f"<pre>{_order_table(size, label=f'Limit Buy ({inverse_ticker})')}</pre>\n"
        f"<b>ALLOCATION &amp; RISK</b>\n"
        f"• <b>Total Units:</b> <code>{size.shares}</code> shares (${capital:,.2f} CAD)\n"
        f"• <b>Treasury sweep:</b> Sell ~${capital:,.2f} CAD of "
        f"{html.escape(ctx.cash_etf)}\n"
        f"• <b>Sector sleeve:</b> {html.escape(ctx.sector)} (max 1 open)\n"
        f"• <b>VIX:</b> {vix_close:.1f} ➔ risk x{ctx.vix_mult:.2f} "
        f"(${risk_shown:,.0f} CAD / trade)\n"
        f"• <b>Tranche 1:</b> Sell <b>{size.t1_shares}</b> shares at +1.5R "
        f"➔ Stop to Breakeven\n"
        f"• <b>Tranche 2:</b> Trail <b>{size.runner_shares}</b> shares\n"
        f"\n"
        f"<b>BEARISH CONFLUENCE ({safe_und})</b>\n"
        f"• <b>Macro Trend:</b> TSX Benchmark is in Bear Regime\n"
        f"• <b>Setup:</b> {safe_und} rejected at 50-day SMA (${ctx.sma50:.2f})\n"
        f"\n"
        f"{checklist}\n"
        f"💡 <i>Execution Note: You are BUYING the Bear ETF to profit from the decline "
        f"in the {html.escape(ctx.sector)} sector. No short locates or margin required.</i>\n"
        f"\n"
        f"{_footer(ctx.cooldown_days)}"
    )


def format_watchlist_html(
    ctx: AlertContext,
    *,
    current_price: float,
    composite_score: float = 0.0,
    tech_score: float = 0.0,
    fund_score: float = 0.0,
) -> str:
    """Formats a developing setup for manual watchlist monitoring."""
    safe_ticker = html.escape(ctx.ticker)
    invalidation = ctx.sma200 * 0.985
    # ctx.pullback_pct for watch cards is signed: (SMA50 - close) / SMA50 * 100.
    # Positive => below the 50 SMA; negative => above.
    if ctx.pullback_pct > 0:
        distance_line = (
            f"• <b>Distance to 50 SMA:</b> {ctx.pullback_pct:.2f}% below "
            f"(${ctx.sma50:.2f})\n"
        )
    elif ctx.pullback_pct < 0:
        distance_line = (
            f"• <b>Distance to 50 SMA:</b> {abs(ctx.pullback_pct):.2f}% above "
            f"(${ctx.sma50:.2f})\n"
        )
    else:
        distance_line = f"• <b>Distance to 50 SMA:</b> At ${ctx.sma50:.2f}\n"

    table = html.escape(
        f"Level             Price      Significance\n"
        f"─────────────────────────────────────────────\n"
        f"Major Support    ${ctx.sma200:>7.2f}    200-day SMA Base\n"
        f"Reclaim Pivot    ${ctx.sma50:>7.2f}    50-day SMA Overhead\n"
        f"Invalidation     ${invalidation:>7.2f}    Macro Breakdown Zone"
    )

    return (
        f"🟡 <b>WATCHLIST RADAR | TSX: {safe_ticker}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>MONITORING STATUS:</b> Dynamic Support Retest\n"
        f"• <b>Current Price:</b> ${current_price:.2f} CAD\n"
        f"{distance_line}"
        f"\n"
        f"<b>KEY LEVELS TO WATCH</b>\n"
        f"<pre>{table}</pre>\n"
        f"<b>WATCH CONDITIONS &amp; TRIGGERS</b>\n"
        f"• <b>Condition 1 (Bounce):</b> Reversal candle near 200 SMA "
        f"(${ctx.sma200:.2f}).\n"
        f"• <b>Condition 2 (Strength):</b> Daily close back above 50 SMA "
        f"(${ctx.sma50:.2f}).\n"
        f"\n"
        f"{_bulletproof_block(ctx)}\n"
        f"<b>COMPOSITE SCORE: {composite_score:.0f}/100</b>\n"
        f"• Tech: {tech_score:.0f}/40 | Fund: {fund_score:.0f}/40 | "
        f"Sentiment: {ctx.sentiment_score:.0f}/20 "
        f"({html.escape(ctx.sentiment_rating)})\n"
        f"\n"
        f"💡 <i>Action: Set price alerts at ${ctx.sma200:.2f} and "
        f"${ctx.sma50:.2f}. No live orders.</i>\n"
        f"<i>⏰ <b>Generated:</b> "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}</i>"
    )


def format_idle_cash_html(
    *,
    cash_etf: str,
    vix_close: float,
    vix_mult: float,
    is_bear: bool,
) -> str:
    regime = "BEAR (XIU below 200 SMA)" if is_bear else "BULL"
    safe_cash = html.escape(cash_etf)
    return (
        f"💤 <b>IDLE CASH SWEEP | {safe_cash}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"No new position setups passed all gates today.\n"
        f"• <b>Action:</b> Keep 100% parked in {safe_cash} (T-bill / HISA yield).\n"
        f"• <b>Market regime:</b> {regime}\n"
        f"• <b>VIX:</b> {vix_close:.1f} ➔ risk multiplier x{vix_mult:.2f}\n"
        f"• When a 🟢/🔴 setup fires, sell only the CAD needed from {safe_cash}.\n"
        f"\n"
        f"⏰ <b>Generated:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}"
    )


def send_html_message(token: str, chat_id: str, text: str, timeout: int = 20) -> dict:
    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    response = requests.post(
        url,
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=timeout,
    )
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Telegram returned non-JSON ({response.status_code}): {response.text[:200]}"
        ) from exc

    if response.status_code != 200 or not payload.get("ok"):
        description = payload.get("description", response.text[:200])
        raise RuntimeError(f"Telegram sendMessage failed: {description}")
    return payload
