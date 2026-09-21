"""Dashboard card DTOs. Field names match templates/dashboard.html."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from fundamentals import FundamentalsResult
from indicators import BarSnapshot, SetupFlags
from sentiment import NewsVelocityResult
from universe import ticker_sector


def daily_change_pct(df: pd.DataFrame, close: float) -> float:
    """Percent change vs prior session close."""
    if len(df) < 2 or "Close" not in df.columns:
        return 0.0
    prev_close = float(df["Close"].iloc[-2])
    if prev_close <= 0:
        return 0.0
    return ((close - prev_close) / prev_close) * 100.0


@dataclass(frozen=True)
class DashboardCard:
    ticker: str
    sector: str
    close: float
    change_pct: float
    category: str
    adx: float
    rvol: float
    sma50: float
    sma200: float
    sma50_slope: float
    rs_vs_xiu: float
    pullback_pct: float
    dist_to_50_pct: float
    above_sma50: bool
    dist_to_200_pct: float
    invalidation_price: float
    debt_safe: bool
    fcf_positive: bool
    earnings_conflict: bool
    headlines_clean: bool
    fund_notes: str

    def to_template_dict(self) -> dict[str, Any]:
        """Flat dict for Jinja. Keys are the template contract."""
        return asdict(self)


def dashboard_card(
    ticker: str,
    sector: str,
    bar: BarSnapshot,
    df: pd.DataFrame,
    flags: SetupFlags,
    fund: FundamentalsResult,
    news: NewsVelocityResult,
    category: str,
    extra_note: str = "",
) -> DashboardCard:
    notes = fund.notes
    if extra_note:
        notes = f"{extra_note} {notes}".strip()
    return DashboardCard(
        ticker=ticker,
        sector=sector,
        close=bar.close,
        change_pct=daily_change_pct(df, bar.close),
        category=category,
        adx=bar.adx,
        rvol=flags.rvol,
        sma50=bar.sma_50,
        sma200=bar.sma_200,
        sma50_slope=bar.sma_50_slope,
        rs_vs_xiu=flags.rs_vs_xiu,
        pullback_pct=flags.pullback_pct,
        # Signed: + above 50 SMA, − below (pullback_pct is abs-only).
        dist_to_50_pct=(
            ((bar.close - bar.sma_50) / bar.sma_50) * 100.0 if bar.sma_50 > 0 else 0.0
        ),
        above_sma50=bar.close >= bar.sma_50,
        dist_to_200_pct=flags.dist_to_200_sma_pct,
        invalidation_price=bar.sma_200 * 0.985,
        debt_safe=fund.debt_safe,
        fcf_positive=fund.fcf_positive,
        earnings_conflict=fund.earnings_conflict,
        headlines_clean=news.headlines_clean,
        fund_notes=notes,
    )


def skipped_dashboard_card(
    ticker: str,
    reason: str,
    close: float = 0.0,
    change_pct: float = 0.0,
) -> DashboardCard:
    return DashboardCard(
        ticker=ticker,
        sector=ticker_sector(ticker),
        close=close,
        change_pct=change_pct,
        category="neutral",
        adx=0.0,
        rvol=0.0,
        sma50=0.0,
        sma200=0.0,
        sma50_slope=0.0,
        rs_vs_xiu=0.0,
        pullback_pct=0.0,
        dist_to_50_pct=0.0,
        above_sma50=False,
        dist_to_200_pct=0.0,
        invalidation_price=0.0,
        debt_safe=False,
        fcf_positive=False,
        earnings_conflict=False,
        headlines_clean=True,
        fund_notes=reason,
    )
