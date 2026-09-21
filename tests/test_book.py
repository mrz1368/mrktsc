"""Smoke and fill-path tests for book.py (offline; mocked Yahoo batch)."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

import book
from db import (
    STATUS_PENDING_OPEN,
    connect,
    list_active_positions,
    open_active_position,
)
from sizing import PositionSize
from thresholds import MAX_LIMIT_ATR_FRACTION


def test_book_exports_public_fill_and_exit_entrypoints() -> None:
    assert callable(book.confirm_pending_opens)
    assert callable(book.manage_open_positions)
    # manage_open_positions must confirm pending opens first (heat/fill order).
    src = inspect.getsource(book.manage_open_positions)
    assert "confirm_pending_opens" in src
    assert src.index("confirm_pending_opens") < src.index("STATUS_OPEN")


def _size(*, entry: float = 100.0, stop: float = 97.0, max_limit: float = 100.3) -> PositionSize:
    return PositionSize(
        entry=entry,
        atr=2.0,
        r=entry - stop,
        stop=stop,
        target_1=entry + 1.5 * (entry - stop),
        shares=9,
        t1_shares=3,
        runner_shares=6,
        risk_cad=20.0,
        max_limit_price=max_limit,
    )


@dataclass(frozen=True)
class _Cfg:
    telegram_bot_token: str = "tok"
    telegram_chat_id: str = "chat"
    portfolio_risk_cad: float = 20.0
    cooldown_days: int = 5
    signals_db: Path = Path("unused.db")


def test_confirm_pending_aborts_gap_through_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_ohlcv
) -> None:
    conn = connect(tmp_path / "book.db")
    signal_day = datetime(2026, 9, 18, tzinfo=timezone.utc)
    size = _size(entry=100.0, stop=97.0, max_limit=100.3)
    assert open_active_position(
        conn, ticker="SHOP.TO", size=size, sector="Tech", now=signal_day
    )

    df = make_ohlcv([100.0, 100.0, 96.0], atr_pad=1.0)
    df.index = pd.to_datetime(["2026-09-18", "2026-09-19", "2026-09-22"])
    # First session after signal day — gap through stop.
    df.loc[pd.Timestamp("2026-09-19"), "Open"] = 96.0

    monkeypatch.setattr(
        book,
        "safe_fetch_batch",
        lambda *_a, **_k: ({"SHOP.TO": df}, None),
    )
    monkeypatch.setattr(book, "send_html_message", lambda *_a, **_k: True)

    book.confirm_pending_opens(conn, _Cfg())  # type: ignore[arg-type]
    assert list_active_positions(conn, status=STATUS_PENDING_OPEN) == []
    assert list_active_positions(conn) == []


def test_legacy_max_limit_zero_recomputed_and_aborts_overshoot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_ohlcv
) -> None:
    conn = connect(tmp_path / "book_legacy.db")
    signal_day = datetime(2026, 9, 18, tzinfo=timezone.utc)
    size = _size(entry=100.0, stop=97.0, max_limit=0.0)
    assert open_active_position(
        conn, ticker="SHOP.TO", size=size, sector="Tech", now=signal_day
    )
    pending = list_active_positions(conn, status=STATUS_PENDING_OPEN)[0]
    assert pending.max_limit_price == 0.0

    df = make_ohlcv([100.0] * 40, atr_pad=1.0)
    idx = pd.date_range("2026-09-18", periods=40, freq="B")
    df.index = idx
    fill_idx = idx[1]
    df.loc[fill_idx, "Open"] = 100.50

    monkeypatch.setattr(
        book,
        "safe_fetch_batch",
        lambda *_a, **_k: ({"SHOP.TO": df}, None),
    )
    monkeypatch.setattr(book, "send_html_message", lambda *_a, **_k: True)

    book.confirm_pending_opens(conn, _Cfg())  # type: ignore[arg-type]
    assert list_active_positions(conn) == []


def test_legacy_max_limit_helper_signal_plus_atr_fraction(make_ohlcv) -> None:
    df = make_ohlcv([100.0] * 40, atr_pad=2.0)
    limit = book._legacy_max_limit_from_history(df, signal_price=100.0)
    assert limit > 100.0
    atr_implied = (limit - 100.0) / MAX_LIMIT_ATR_FRACTION
    # atr_pad=2 → High-Low=4; Wilder ATR warms near that range.
    assert atr_implied == pytest.approx(4.0, rel=0.1)
