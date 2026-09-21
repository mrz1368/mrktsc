"""Smoke tests for book.py public surface (fill/exit sequencer)."""

from __future__ import annotations

import inspect

import book


def test_book_exports_public_fill_and_exit_entrypoints() -> None:
    assert callable(book.confirm_pending_opens)
    assert callable(book.manage_open_positions)
    # manage_open_positions must confirm pending opens first (heat/fill order).
    src = inspect.getsource(book.manage_open_positions)
    assert "confirm_pending_opens" in src
    assert src.index("confirm_pending_opens") < src.index("STATUS_OPEN")
