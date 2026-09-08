"""SQLite signal log with per-ticker, per-alert-type cooldown."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sizing import PositionSize

ISO_FORMAT = "%Y-%m-%dT%H:%M:%S%z"

ALERT_BUY = "buy"
ALERT_INVERSE = "inverse"
ALERT_WATCH = "watch"


@dataclass(frozen=True)
class RecordResult:
    inserted: bool
    reason: str


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            created_at TEXT NOT NULL,
            entry REAL NOT NULL,
            stop REAL NOT NULL,
            target_1 REAL NOT NULL,
            target_2 REAL NOT NULL,
            shares INTEGER NOT NULL,
            r REAL NOT NULL,
            atr REAL NOT NULL,
            risk_cad REAL NOT NULL,
            alert_type TEXT NOT NULL DEFAULT 'buy'
        )
        """
    )
    _ensure_alert_type_column(conn)
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_signals_ticker_type_created
        ON signals (ticker, alert_type, created_at)
        """
    )
    conn.commit()


def _ensure_alert_type_column(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(signals)").fetchall()}
    if "alert_type" not in cols:
        conn.execute(
            "ALTER TABLE signals ADD COLUMN alert_type TEXT NOT NULL DEFAULT 'buy'"
        )
        # Migrate legacy WATCH:TICKER rows into typed watch alerts.
        conn.execute(
            """
            UPDATE signals
            SET alert_type = 'watch',
                ticker = substr(ticker, 7)
            WHERE ticker LIKE 'WATCH:%'
            """
        )


def record_if_allowed(
    conn: sqlite3.Connection,
    ticker: str,
    size: PositionSize,
    cooldown_days: int,
    *,
    alert_type: str = ALERT_BUY,
    now: datetime | None = None,
) -> RecordResult:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)

    cutoff = moment - timedelta(days=cooldown_days)
    cutoff_iso = cutoff.strftime(ISO_FORMAT)

    existing = conn.execute(
        """
        SELECT id, created_at FROM signals
        WHERE ticker = ? AND alert_type = ? AND created_at >= ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (ticker, alert_type, cutoff_iso),
    ).fetchone()

    if existing:
        return RecordResult(
            inserted=False,
            reason=(
                f"cooldown: {alert_type}:{ticker} already recorded "
                f"at {existing['created_at']}"
            ),
        )

    conn.execute(
        """
        INSERT INTO signals (
            ticker, created_at, entry, stop, target_1, target_2,
            shares, r, atr, risk_cad, alert_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ticker,
            moment.strftime(ISO_FORMAT),
            size.entry,
            size.stop,
            size.target_1,
            0.0,
            size.shares,
            size.r,
            size.atr,
            size.risk_cad,
            alert_type,
        ),
    )
    conn.commit()
    return RecordResult(inserted=True, reason="recorded")


def count_signals(
    conn: sqlite3.Connection,
    ticker: str | None = None,
    *,
    alert_type: str | None = None,
) -> int:
    if ticker is None and alert_type is None:
        row = conn.execute("SELECT COUNT(*) AS n FROM signals").fetchone()
    elif ticker is not None and alert_type is None:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM signals WHERE ticker = ?", (ticker,)
        ).fetchone()
    elif ticker is None and alert_type is not None:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM signals WHERE alert_type = ?",
            (alert_type,),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n FROM signals
            WHERE ticker = ? AND alert_type = ?
            """,
            (ticker, alert_type),
        ).fetchone()
    return int(row["n"])
