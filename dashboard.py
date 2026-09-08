"""Generates the static HTML dashboard using Jinja2."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader

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

    now_eastern = datetime.now(ZoneInfo("America/Toronto")).strftime(
        "%Y-%m-%d %H:%M %Z"
    )

    html_out = template.render(
        cards=cards,
        regime=regime,
        vix_val=f"{vix_val:.1f}",
        vix_mult=f"{vix_mult:.2f}",
        sentiment_score=f"{sentiment_score:.0f}",
        sentiment_rating=sentiment_rating,
        cash_etf=cash_etf,
        generated_at=now_eastern,
    )

    out_file = DIST_DIR / "index.html"
    out_file.write_text(html_out, encoding="utf-8")
    print(f" -> [DASHBOARD GENERATED] {out_file}")
