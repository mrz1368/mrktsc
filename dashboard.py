"""Generates the static HTML dashboard using Jinja2."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader

import thresholds as thr

ROOT = Path(__file__).resolve().parent
DIST_DIR = ROOT / "dist"
TEMPLATES_DIR = ROOT / "templates"


def generate_dashboard(
    cards: list[dict],
    regime: str,
    vix_val: float,
    vix_mult: float,
    sentiment_score: float,
    sentiment_rating: str,
    cash_etf: str = "CASH.TO",
) -> None:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=True)
    template = env.get_template("dashboard.html")

    now_eastern = datetime.now(ZoneInfo("America/Toronto")).strftime("%Y-%m-%d %H:%M %Z")

    html_out = template.render(
        cards=cards,
        regime=regime,
        vix_val=f"{vix_val:.1f}",
        vix_mult=f"{vix_mult:.2f}",
        sentiment_score=f"{sentiment_score:.0f}",
        sentiment_rating=sentiment_rating,
        cash_etf=cash_etf,
        generated_at=now_eastern,
        thresholds={
            "min_adx": thr.MIN_ADX,
            "min_rvol": thr.MIN_RVOL,
            "pullback_max_pct": thr.PULLBACK_MAX_PCT,
            "min_clv_bull": thr.MIN_CLV_BULL,
            "sma_slope_lookback": thr.SMA_SLOPE_LOOKBACK,
            "min_mddv_cad": thr.MIN_MDDV_CAD,
            "min_price_cad": thr.MIN_PRICE_CAD,
            "min_interest_coverage": thr.MIN_INTEREST_COVERAGE,
            "earnings_blackout_post_days": thr.EARNINGS_BLACKOUT_POST_DAYS,
            "earnings_blackout_ahead_days": thr.EARNINGS_BLACKOUT_AHEAD_DAYS,
            "news_lookback_days": thr.NEWS_LOOKBACK_DAYS,
            "extreme_greed_score": thr.EXTREME_GREED_SCORE,
        },
    )

    out_file = DIST_DIR / "index.html"
    out_file.write_text(html_out, encoding="utf-8")
    print(f" -> [DASHBOARD GENERATED] {out_file}")
