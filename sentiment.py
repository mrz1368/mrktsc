"""Macro Fear & Greed plus per-ticker headline velocity checks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests
import yfinance as yf

from universe import EXTREME_GREED_SCORE, NEWS_LOOKBACK_DAYS

CNN_FNG_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

NEGATIVE_HEADLINE_KEYWORDS = (
    "lawsuit",
    "sued",
    "fraud",
    "investigation",
    "probe",
    "downgrade",
    "resign",
    "stepping down",
    "fired",
    "bankruptcy",
    "short seller",
    "short-seller",
    "sec charge",
    "restatement",
    "accounting irregular",
    "misses estimates",
    "misses expectations",
    "cuts guidance",
    "cut guidance",
    "guidance cut",
    "layoffs",
    "trading halt",
    "delisting",
    "class action",
    "whistleblower",
    "insider selling",
)


@dataclass(frozen=True)
class MacroSentiment:
    score: float
    rating: str
    sentiment_points: float

    @property
    def is_extreme_greed(self) -> bool:
        return self.score > EXTREME_GREED_SCORE


@dataclass(frozen=True)
class NewsVelocityResult:
    headlines_clean: bool
    hit_count: int
    notes: str


def get_macro_sentiment() -> MacroSentiment:
    """Returns the current Fear & Greed score (0-100) and rating category."""
    try:
        res = requests.get(CNN_FNG_URL, headers=HEADERS, timeout=10)
        res.raise_for_status()
        data = res.json().get("fear_and_greed", {})
        score = round(float(data.get("score", 50.0)), 1)
        rating = str(data.get("rating", "neutral")).lower()

        # Contrarian points: fear supports long pullbacks; extreme greed vetoes them.
        if score <= 25:
            sentiment_points = 20.0
        elif score <= 45:
            sentiment_points = 16.0
        elif score <= 55:
            sentiment_points = 10.0
        elif score <= EXTREME_GREED_SCORE:
            sentiment_points = 5.0
        else:
            sentiment_points = 0.0

        return MacroSentiment(
            score=score,
            rating=rating,
            sentiment_points=sentiment_points,
        )
    except (
        requests.RequestException,
        ValueError,
        TypeError,
        AttributeError,
        KeyError,
    ) as exc:
        print(f"Failed to fetch Fear & Greed: {exc}")
        return MacroSentiment(score=50.0, rating="neutral", sentiment_points=10.0)


def _parse_news_timestamp(item: dict) -> datetime | None:
    content = item.get("content") if isinstance(item.get("content"), dict) else {}
    candidates = [
        item.get("providerPublishTime"),
        item.get("pubDate"),
        content.get("pubDate"),
        content.get("displayTime"),
    ]
    for raw in candidates:
        if raw is None:
            continue
        try:
            if isinstance(raw, (int, float)):
                return datetime.fromtimestamp(float(raw), tz=timezone.utc)
            ts = pd_to_datetime(raw)
            if ts is not None:
                return ts
        except (TypeError, ValueError, OSError, OverflowError):
            continue
    return None


def pd_to_datetime(raw: object) -> datetime | None:
    """Parse Yahoo news timestamps without importing pandas at module import cost."""
    text = str(raw).strip()
    if not text:
        return None
    # ISO-ish strings from Yahoo content blocks.
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _headline_text(item: dict) -> str:
    content = item.get("content") if isinstance(item.get("content"), dict) else {}
    title = item.get("title") or content.get("title") or ""
    summary = content.get("summary") or item.get("summary") or ""
    return f"{title} {summary}".lower()


def evaluate_news_velocity(ticker_obj: yf.Ticker) -> NewsVelocityResult:
    """Flag active negative headline cycles over the lookback window."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=NEWS_LOOKBACK_DAYS)
    hits: list[str] = []

    try:
        news_items = ticker_obj.news or []
    except (TypeError, AttributeError, OSError, ValueError) as exc:
        return NewsVelocityResult(
            headlines_clean=True,
            hit_count=0,
            notes=f"News unavailable ({exc.__class__.__name__})",
        )

    for item in news_items:
        if not isinstance(item, dict):
            continue
        published = _parse_news_timestamp(item)
        if published is not None and published < cutoff:
            continue
        text = _headline_text(item)
        if not text.strip():
            continue
        matched = next((kw for kw in NEGATIVE_HEADLINE_KEYWORDS if kw in text), None)
        if matched:
            title = (
                item.get("title")
                or (item.get("content") or {}).get("title")
                or matched
            )
            hits.append(str(title)[:80])

    if hits:
        sample = hits[0]
        return NewsVelocityResult(
            headlines_clean=False,
            hit_count=len(hits),
            notes=f"Negative news cycle ({len(hits)} hits): {sample}",
        )

    return NewsVelocityResult(
        headlines_clean=True,
        hit_count=0,
        notes=f"No negative headline cluster ({NEWS_LOOKBACK_DAYS}d)",
    )
