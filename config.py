"""Load environment config; fail fast if required secrets are missing."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    telegram_chat_id: str
    portfolio_risk_cad: float
    cooldown_days: int
    signals_db: Path


def load_config() -> Config:
    load_dotenv(ROOT / ".env", override=True)

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    risk_raw = os.getenv("PORTFOLIO_RISK_CAD", "").strip()
    cooldown_raw = os.getenv("COOLDOWN_DAYS", "5").strip()
    db_raw = os.getenv("SIGNALS_DB", "signals.db").strip()

    missing = [
        name
        for name, value in (
            ("TELEGRAM_BOT_TOKEN", token),
            ("TELEGRAM_CHAT_ID", chat_id),
            ("PORTFOLIO_RISK_CAD", risk_raw),
        )
        if not value
    ]
    if missing:
        raise SystemExit(
            "Missing required env vars: "
            + ", ".join(missing)
            + ". Copy .env.example to .env and fill in values."
        )

    if token.startswith("your-") or chat_id.startswith("your-"):
        raise SystemExit(
            "Replace placeholder TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env "
            "with values from BotFather and getUpdates."
        )

    try:
        risk = float(risk_raw)
    except ValueError as exc:
        raise SystemExit("PORTFOLIO_RISK_CAD must be a number.") from exc
    if risk <= 0:
        raise SystemExit("PORTFOLIO_RISK_CAD must be greater than 0.")

    try:
        cooldown_days = int(cooldown_raw)
    except ValueError as exc:
        raise SystemExit("COOLDOWN_DAYS must be an integer.") from exc
    if cooldown_days < 1:
        raise SystemExit("COOLDOWN_DAYS must be at least 1.")

    db_path = Path(db_raw)
    if not db_path.is_absolute():
        db_path = ROOT / db_path

    return Config(
        telegram_bot_token=token,
        telegram_chat_id=chat_id,
        portfolio_risk_cad=risk,
        cooldown_days=cooldown_days,
        signals_db=db_path,
    )
