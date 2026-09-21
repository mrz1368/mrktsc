"""Offline fundamentals fail-open tests — fake ticker objects, no yfinance network."""

from __future__ import annotations

import pytest

from fundamentals import (
    FundamentalsResult,
    _eval_cash_flow,
    _eval_debt,
    _eval_quality,
    _metadata_fallback,
    evaluate_fundamentals,
)


class FakeTicker:
    def __init__(
        self,
        symbol: str = "TEST.TO",
        info: dict | None = None,
        *,
        info_raises: Exception | None = None,
        calendar: object = None,
    ) -> None:
        self.ticker = symbol
        self._info = info
        self._info_raises = info_raises
        self.calendar = calendar

    @property
    def info(self) -> dict:
        if self._info_raises is not None:
            raise self._info_raises
        if self._info is None:
            return {}
        return self._info


def test_metadata_fallback_passes() -> None:
    result = _metadata_fallback("unavailable")
    assert isinstance(result, FundamentalsResult)
    assert result.passes_fundamentals is True
    assert result.metadata_complete is False
    assert result.debt_safe and result.fcf_positive and result.quality_ok
    assert result.earnings_conflict is False


def test_info_fetch_failure_fail_open() -> None:
    result = evaluate_fundamentals(
        FakeTicker(info_raises=RuntimeError("yahoo down"))  # type: ignore[arg-type]
    )
    assert result.passes_fundamentals is True
    assert result.metadata_complete is False
    assert "fallback" in result.notes.lower() or "unavailable" in result.notes.lower()


def test_empty_info_fail_open() -> None:
    result = evaluate_fundamentals(FakeTicker(info={}))  # type: ignore[arg-type]
    assert result.passes_fundamentals is True
    assert result.metadata_complete is False


def test_missing_debt_metrics_assumed_safe() -> None:
    notes: list[str] = []
    ok, _ = _eval_debt({"sector": "Technology"}, notes, 0.0)
    assert ok is True
    assert any("unverified" in n.lower() for n in notes)


def test_missing_cash_flow_fail_open() -> None:
    notes: list[str] = []
    ok, _ = _eval_cash_flow({}, notes, 0.0)
    assert ok is True
    assert any("unverified" in n.lower() for n in notes)


def test_missing_quality_fail_open() -> None:
    notes: list[str] = []
    ok, _ = _eval_quality({}, notes, 0.0)
    assert ok is True
    assert any("unverified" in n.lower() or "assumed" in n.lower() for n in notes)


def test_etf_symbol_short_circuit() -> None:
    result = evaluate_fundamentals(FakeTicker(symbol="XIU.TO", info=None))  # type: ignore[arg-type]
    assert result.passes_fundamentals is True
    assert "ETF" in result.notes or "Index" in result.notes


def test_hard_fail_still_blocks() -> None:
    """Fail-open is for missing data only — explicit bad FCF still fails."""
    result = evaluate_fundamentals(
        FakeTicker(
            symbol="JUNK.TO",
            info={
                "quoteType": "EQUITY",
                "sector": "Technology",
                "totalDebt": 1_000_000.0,
                "interestCoverage": 5.0,
                "freeCashflow": -1.0,
                "operatingMargins": 0.20,
            },
        )  # type: ignore[arg-type]
    )
    assert result.fcf_positive is False
    assert result.passes_fundamentals is False


def test_retryable_yahoo_error_propagates() -> None:
    """Rate limits / transient I/O must escape fail-open so call_ticker can retry."""
    from yfinance.exceptions import YFRateLimitError

    with pytest.raises(YFRateLimitError):
        evaluate_fundamentals(
            FakeTicker(info_raises=YFRateLimitError())  # type: ignore[arg-type]
        )
