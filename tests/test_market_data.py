"""Offline market_data retry helper tests — fake callables, no network."""

from __future__ import annotations

import pytest
from yfinance.exceptions import YFRateLimitError

import market_data
from market_data import with_yahoo_retries


def test_with_yahoo_retries_succeeds_after_rate_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(market_data, "RATE_LIMIT_BACKOFFS", (0.0, 0.0, 0.0))
    sleeps: list[float] = []
    monkeypatch.setattr(market_data.time, "sleep", sleeps.append)

    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise YFRateLimitError()
        return "ok"

    assert with_yahoo_retries("unit", flaky) == "ok"
    assert calls["n"] == 3
    assert sleeps == [0.0, 0.0]


def test_with_yahoo_retries_retries_transient_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(market_data, "RATE_LIMIT_BACKOFFS", (0.0, 0.0))
    monkeypatch.setattr(market_data.time, "sleep", lambda _s: None)

    calls = {"n": 0}

    def flaky() -> int:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("blip")
        return 42

    assert with_yahoo_retries("unit", flaky) == 42
    assert calls["n"] == 2


def test_with_yahoo_retries_exhausts_and_reraises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(market_data, "RATE_LIMIT_BACKOFFS", (0.0, 0.0, 0.0))
    monkeypatch.setattr(market_data.time, "sleep", lambda _s: None)

    def always_fail() -> None:
        raise YFRateLimitError()

    with pytest.raises(YFRateLimitError):
        with_yahoo_retries("unit", always_fail)
