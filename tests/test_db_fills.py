"""Offline tests for live upsert refuse and pending fill abort safety."""

from __future__ import annotations

from pathlib import Path

from db import (
    STATUS_PENDING_OPEN,
    close_active_position,
    confirm_pending_position,
    connect,
    list_active_positions,
    open_active_position,
    pending_fill_abort_reason,
)
from sizing import PositionSize


def _size(
    *,
    entry: float = 100.0,
    atr: float = 2.0,
    stop: float = 97.0,
    shares: int = 9,
    max_limit: float = 100.3,
) -> PositionSize:
    return PositionSize(
        entry=entry,
        atr=atr,
        r=entry - stop,
        stop=stop,
        target_1=entry + 1.5 * (entry - stop),
        shares=shares,
        t1_shares=3,
        runner_shares=shares - 3,
        risk_cad=20.0,
        max_limit_price=max_limit,
    )


def test_open_active_position_refuses_live_overwrite(tmp_path: Path) -> None:
    conn = connect(tmp_path / "signals.db")
    size = _size()
    assert open_active_position(conn, ticker="SHOP.TO", size=size, sector="Tech") is True
    refused = open_active_position(
        conn, ticker="SHOP.TO", size=_size(entry=101.0), sector="Tech"
    )
    assert refused is False

    rows = list_active_positions(conn, status=STATUS_PENDING_OPEN)
    assert len(rows) == 1
    assert rows[0].entry_price == 100.0
    assert rows[0].max_limit_price == 100.3


def test_open_active_position_allows_rebook_after_close(tmp_path: Path) -> None:
    conn = connect(tmp_path / "signals.db")
    assert open_active_position(conn, ticker="SHOP.TO", size=_size(), sector="Tech") is True
    close_active_position(conn, "SHOP.TO")
    rebooked = open_active_position(
        conn, ticker="SHOP.TO", size=_size(entry=102.0), sector="Tech"
    )
    assert rebooked is True
    rows = list_active_positions(conn)
    assert len(rows) == 1
    assert rows[0].entry_price == 102.0


def test_pending_fill_abort_through_stop() -> None:
    reason = pending_fill_abort_reason(
        fill_price=96.5,
        stop=97.0,
        max_limit_price=100.3,
    )
    assert reason is not None
    assert "gap through stop" in reason


def test_pending_fill_abort_over_max_limit() -> None:
    reason = pending_fill_abort_reason(
        fill_price=100.5,
        stop=97.0,
        max_limit_price=100.3,
    )
    assert reason is not None
    assert "exceeds max limit" in reason


def test_pending_fill_abort_invalid_open() -> None:
    assert pending_fill_abort_reason(
        fill_price=float("nan"),
        stop=97.0,
        max_limit_price=100.3,
    ) == "next open is missing/invalid"
    assert pending_fill_abort_reason(
        fill_price=0.0,
        stop=97.0,
        max_limit_price=100.3,
    ) == "next open is missing/invalid"


def test_pending_fill_ok_within_band() -> None:
    assert (
        pending_fill_abort_reason(
            fill_price=100.1,
            stop=97.0,
            max_limit_price=100.3,
        )
        is None
    )


def test_confirm_pending_stores_and_promotes(tmp_path: Path) -> None:
    conn = connect(tmp_path / "signals.db")
    assert open_active_position(conn, ticker="SHOP.TO", size=_size(), sector="Tech") is True
    confirmed = confirm_pending_position(
        conn,
        ticker="SHOP.TO",
        fill_price=100.1,
        initial_stop=97.1,
        current_stop=97.1,
        fill_date="2026-09-22T00:00:00+0000",
    )
    assert confirmed is not None
    assert confirmed.status == "OPEN"
    assert confirmed.entry_price == 100.1
    assert confirmed.max_limit_price == 100.3
