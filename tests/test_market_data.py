"""Offline market_data retry helper and batch-download unwrap tests — no network."""

from __future__ import annotations

import pandas as pd
import pytest
from yfinance.exceptions import YFRateLimitError

import market_data
from market_data import (
    fetch_history_batch,
    frames_from_download,
    safe_fetch_batch,
    with_yahoo_retries,
)


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


def _ohlcv(closes: list[float], *, dividends: list[float] | None = None) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="B")
    data: dict[str, object] = {
        "Open": closes,
        "High": [c + 1 for c in closes],
        "Low": [c - 1 for c in closes],
        "Close": closes,
        "Volume": [1_000_000] * len(closes),
    }
    if dividends is not None:
        data["Dividends"] = dividends
        data["Stock Splits"] = [0.0] * len(closes)
    return pd.DataFrame(data, index=idx)


def test_frames_from_download_group_by_ticker() -> None:
    a = _ohlcv([10.0, 11.0], dividends=[0.0, 0.25])
    b = _ohlcv([20.0, 21.0])
    raw = pd.concat({"AAA.TO": a, "BBB.TO": b}, axis=1)
    raw.columns.names = ["Ticker", "Price"]

    frames = frames_from_download(raw, ["AAA.TO", "BBB.TO", "MISSING.TO"])
    assert set(frames) == {"AAA.TO", "BBB.TO", "MISSING.TO"}
    assert list(frames["AAA.TO"]["Close"]) == [10.0, 11.0]
    assert float(frames["AAA.TO"]["Dividends"].iloc[-1]) == 0.25
    assert list(frames["BBB.TO"]["Close"]) == [20.0, 21.0]
    assert frames["MISSING.TO"].empty


def test_frames_from_download_group_by_column() -> None:
    a = _ohlcv([10.0, 11.0])
    b = _ohlcv([20.0, 21.0])
    by_ticker = pd.concat({"AAA.TO": a, "BBB.TO": b}, axis=1)
    raw = by_ticker.swaplevel(0, 1, axis=1).sort_index(axis=1)
    raw.columns.names = ["Price", "Ticker"]

    frames = frames_from_download(raw, ["AAA.TO", "BBB.TO"])
    assert list(frames["AAA.TO"]["Close"]) == [10.0, 11.0]
    assert list(frames["BBB.TO"]["Close"]) == [20.0, 21.0]


def test_frames_from_download_single_flat() -> None:
    raw = _ohlcv([10.0, 11.0])
    frames = frames_from_download(raw, ["SOLO.TO"])
    assert list(frames["SOLO.TO"]["Close"]) == [10.0, 11.0]


def test_frames_from_download_empty_close_column() -> None:
    idx = pd.date_range("2024-01-01", periods=2, freq="B")
    emptyish = pd.DataFrame(
        {
            "Open": [float("nan"), float("nan")],
            "High": [float("nan"), float("nan")],
            "Low": [float("nan"), float("nan")],
            "Close": [float("nan"), float("nan")],
            "Volume": [float("nan"), float("nan")],
        },
        index=idx,
    )
    raw = pd.concat({"DEAD.TO": emptyish, "LIVE.TO": _ohlcv([5.0, 6.0])}, axis=1)
    frames = frames_from_download(raw, ["DEAD.TO", "LIVE.TO"])
    assert frames["DEAD.TO"].empty
    assert list(frames["LIVE.TO"]["Close"]) == [5.0, 6.0]


def test_frames_from_download_none_or_empty_raw() -> None:
    assert frames_from_download(None, ["A.TO"])["A.TO"].empty
    assert frames_from_download(pd.DataFrame(), ["A.TO"])["A.TO"].empty


def test_fetch_history_batch_mocks_yf_download(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(market_data, "RATE_LIMIT_BACKOFFS", (0.0,))
    monkeypatch.setattr(market_data.time, "sleep", lambda _s: None)

    a = _ohlcv([10.0, 11.0], dividends=[0.0, 0.1])
    b = _ohlcv([20.0, 21.0])
    raw = pd.concat({"AAA.TO": a, "BBB.TO": b}, axis=1)

    calls: list[object] = []

    def fake_download(tickers: object, **kwargs: object) -> pd.DataFrame:
        calls.append((tickers, kwargs))
        return raw

    monkeypatch.setattr(market_data.yf, "download", fake_download)

    frames = fetch_history_batch(["AAA.TO", "BBB.TO", "AAA.TO", "GONE.TO"], period="6mo")
    assert len(calls) == 1
    tickers_arg, kwargs = calls[0]
    assert tickers_arg == ["AAA.TO", "BBB.TO", "GONE.TO"]
    assert kwargs["period"] == "6mo"
    assert kwargs["interval"] == "1d"
    assert kwargs["auto_adjust"] is True
    assert kwargs["actions"] is True
    assert kwargs["group_by"] == "ticker"
    assert kwargs["progress"] is False

    assert list(frames["AAA.TO"]["Close"]) == [10.0, 11.0]
    assert float(frames["AAA.TO"]["Dividends"].iloc[-1]) == pytest.approx(0.1)
    assert list(frames["BBB.TO"]["Close"]) == [20.0, 21.0]
    assert frames["GONE.TO"].empty
    assert "AAA.TO" in frames and list(frames) == ["AAA.TO", "BBB.TO", "GONE.TO"]


def test_fetch_history_batch_empty_input() -> None:
    assert fetch_history_batch([]) == {}
    assert fetch_history_batch(["", "  "]) == {}


def test_safe_fetch_batch_soft_fails_on_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(market_data, "RATE_LIMIT_BACKOFFS", (0.0, 0.0))
    sleeps: list[float] = []
    monkeypatch.setattr(market_data.time, "sleep", sleeps.append)

    def boom(*_a: object, **_k: object) -> dict[str, pd.DataFrame]:
        raise YFRateLimitError()

    monkeypatch.setattr(market_data, "fetch_history_batch", boom)
    frames, err = safe_fetch_batch(["AAA.TO"], error_label="FILL ERROR")
    assert frames == {}
    assert err is not None and "rate limit" in err.lower()
    assert sleeps == [0.0]  # final backoff only (retries live inside fetch_history_batch)


def test_safe_fetch_batch_soft_fails_on_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(market_data.time, "sleep", lambda _s: None)

    def boom(*_a: object, **_k: object) -> dict[str, pd.DataFrame]:
        raise ValueError("bad batch")

    monkeypatch.setattr(market_data, "fetch_history_batch", boom)
    frames, err = safe_fetch_batch(["AAA.TO"], error_label="EXIT ERROR")
    assert frames == {}
    assert err == "Batch history error: bad batch"


def test_safe_fetch_batch_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = {"AAA.TO": _ohlcv([1.0, 2.0])}

    def ok(*_a: object, **_k: object) -> dict[str, pd.DataFrame]:
        return expected

    monkeypatch.setattr(market_data, "fetch_history_batch", ok)
    frames, err = safe_fetch_batch(["AAA.TO"], period="10d")
    assert err is None
    assert frames is expected
