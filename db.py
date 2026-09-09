"""SQLite signal log, cooldowns, and active position lifecycle."""

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


@dataclass(frozen=True)
class ActivePosition:
    ticker: str
    entry_date: str
    entry_price: float
    initial_stop: float
    current_stop: float
    shares_total: int
    shares_remaining: int
    is_de_risked: bool
    bars_held: int
    sector: str


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
    _ensure_active_positions_schema(conn)
    conn.commit()


def _ensure_alert_type_column(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(signals)").fetchall()}
    if "alert_type" not in cols:
        conn.execute(
            "ALTER TABLE signals ADD COLUMN alert_type TEXT NOT NULL DEFAULT 'buy'"
        )
        conn.execute(
            """
            UPDATE signals
            SET alert_type = 'watch',
                ticker = substr(ticker, 7)
            WHERE ticker LIKE 'WATCH:%'
            """
        )


def _create_active_positions_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS active_positions (
            ticker TEXT PRIMARY KEY,
            entry_date TEXT NOT NULL,
            entry_price REAL NOT NULL,
            initial_stop REAL NOT NULL,
            current_stop REAL NOT NULL,
            shares_total INTEGER NOT NULL,
            shares_remaining INTEGER NOT NULL,
            is_de_risked INTEGER NOT NULL DEFAULT 0,
            bars_held INTEGER NOT NULL DEFAULT 0,
            sector TEXT NOT NULL
        )
        """
    )


def _ensure_active_positions_schema(conn: sqlite3.Connection) -> None:
    cols = {
        row[1] for row in conn.execute("PRAGMA table_info(active_positions)").fetchall()
    }
    if not cols:
        _create_active_positions_table(conn)
        return
    if "shares_remaining" in cols and "current_stop" in cols:
        return

    # Migrate legacy retail schema -> tranche schema.
    conn.execute("ALTER TABLE active_positions RENAME TO active_positions_legacy")
    _create_active_positions_table(conn)
    legacy_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(active_positions_legacy)").fetchall()
    }
    if "shares" in legacy_cols:
        stop_expr = (
            "COALESCE(trailing_stop, initial_stop)"
            if "trailing_stop" in legacy_cols
            else "initial_stop"
        )
        conn.execute(
            f"""
            INSERT INTO active_positions (
                ticker, entry_date, entry_price, initial_stop, current_stop,
                shares_total, shares_remaining, is_de_risked, bars_held, sector
            )
            SELECT
                ticker, entry_date, entry_price, initial_stop, {stop_expr},
                shares, shares, 0, bars_held, sector
            FROM active_positions_legacy
            """
        )
    conn.execute("DROP TABLE active_positions_legacy")


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


def open_active_position(
    conn: sqlite3.Connection,
    *,
    ticker: str,
    size: PositionSize,
    sector: str,
    now: datetime | None = None,
) -> None:
    """Book a live trade into active_positions (upsert by ticker)."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)

    conn.execute(
        """
        INSERT INTO active_positions (
            ticker, entry_date, entry_price, initial_stop, current_stop,
            shares_total, shares_remaining, is_de_risked, bars_held, sector
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?)
        ON CONFLICT(ticker) DO UPDATE SET
            entry_date = excluded.entry_date,
            entry_price = excluded.entry_price,
            initial_stop = excluded.initial_stop,
            current_stop = excluded.current_stop,
            shares_total = excluded.shares_total,
            shares_remaining = excluded.shares_remaining,
            is_de_risked = 0,
            bars_held = 0,
            sector = excluded.sector
        """,
        (
            ticker,
            moment.strftime(ISO_FORMAT),
            size.entry,
            size.stop,
            size.stop,
            size.shares,
            size.shares,
            sector,
        ),
    )
    conn.commit()


def list_active_positions(conn: sqlite3.Connection) -> list[ActivePosition]:
    rows = conn.execute(
        """
        SELECT ticker, entry_date, entry_price, initial_stop, current_stop,
               shares_total, shares_remaining, is_de_risked, bars_held, sector
        FROM active_positions
        ORDER BY entry_date ASC
        """
    ).fetchall()
    return [
        ActivePosition(
            ticker=row["ticker"],
            entry_date=row["entry_date"],
            entry_price=float(row["entry_price"]),
            initial_stop=float(row["initial_stop"]),
            current_stop=float(row["current_stop"]),
            shares_total=int(row["shares_total"]),
            shares_remaining=int(row["shares_remaining"]),
            is_de_risked=bool(row["is_de_risked"]),
            bars_held=int(row["bars_held"]),
            sector=row["sector"],
        )
        for row in rows
    ]


def update_active_position(
    conn: sqlite3.Connection,
    *,
    ticker: str,
    current_stop: float,
    shares_remaining: int,
    is_de_risked: bool,
    bars_held: int,
) -> None:
    conn.execute(
        """
        UPDATE active_positions
        SET current_stop = ?, shares_remaining = ?, is_de_risked = ?, bars_held = ?
        WHERE ticker = ?
        """,
        (
            current_stop,
            shares_remaining,
            1 if is_de_risked else 0,
            bars_held,
            ticker,
        ),
    )
    conn.commit()


def close_active_position(conn: sqlite3.Connection, ticker: str) -> None:
    conn.execute("DELETE FROM active_positions WHERE ticker = ?", (ticker,))
    conn.commit()


def active_sectors(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT sector FROM active_positions
        WHERE shares_remaining > 0
        """
    ).fetchall()
    return {row["sector"] for row in rows}


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
