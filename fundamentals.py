"""Production-safe fundamental filters with defensive yfinance null handling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import yfinance as yf

from universe import (
    EARNINGS_BLACKOUT_AHEAD_DAYS,
    EARNINGS_BLACKOUT_POST_DAYS,
)

MIN_INTEREST_COVERAGE = 2.0
ETF_SYMBOLS = {
    "XIU.TO",
    "XIC.TO",
    "VFV.TO",
    "XQQ.TO",
    "CASH.TO",
    "CFOD.TO",
    "NRGD.TO",
    "CNDI.TO",
}

# Mega-caps where missing Yahoo fundamentals should not veto a pristine technical setup.
TSX_BLUECHIP_FALLBACK = {
    "RY.TO",
    "TD.TO",
    "BMO.TO",
    "BNS.TO",
    "CNR.TO",
    "CP.TO",
    "ENB.TO",
    "TRP.TO",
    "SHOP.TO",
    "BAM.TO",
    "ATD.TO",
    "CSU.TO",
    "DOL.TO",
}


@dataclass(frozen=True)
class FundamentalsResult:
    earnings_conflict: bool
    debt_safe: bool
    fcf_positive: bool
    quality_ok: bool
    notes: str
    score: float = 0.0
    metadata_complete: bool = True

    @property
    def passes_fundamentals(self) -> bool:
        """Block only on explicit hard fails; missing Yahoo fields fail open."""
        return (
            not self.earnings_conflict
            and self.debt_safe
            and self.fcf_positive
            and self.quality_ok
        )


def _etf_fallback() -> FundamentalsResult:
    return FundamentalsResult(
        earnings_conflict=False,
        debt_safe=True,
        fcf_positive=True,
        quality_ok=True,
        notes="Broad Index / ETF - Diversified holdings",
        score=30.0,
        metadata_complete=True,
    )


def _metadata_fallback(reason: str) -> FundamentalsResult:
    return FundamentalsResult(
        earnings_conflict=False,
        debt_safe=True,
        fcf_positive=True,
        quality_ok=True,
        notes=reason,
        score=10.0,
        metadata_complete=False,
    )


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _as_utc(ts: pd.Timestamp) -> pd.Timestamp:
    if getattr(ts, "tzinfo", None) is None:
        return ts.tz_localize(timezone.utc)
    return ts.tz_convert(timezone.utc)


def _earnings_dates(calendar: object) -> list[object]:
    if calendar is None:
        return []
    if hasattr(calendar, "empty") and bool(calendar.empty):
        return []
    if isinstance(calendar, dict):
        dates = calendar.get("Earnings Date", [])
        if dates is None:
            return []
        return list(dates) if isinstance(dates, (list, tuple)) else [dates]
    index = getattr(calendar, "index", None)
    if index is not None and "Earnings Date" in index:
        values = calendar.loc["Earnings Date"]
        if hasattr(values, "tolist"):
            return list(values.tolist())
        return [values]
    return []


def _check_earnings_blackout(ticker_obj: yf.Ticker, notes: list[str]) -> bool:
    """Isolated calendar parse — Yahoo often breaks this for .TO names."""
    try:
        calendar = ticker_obj.calendar
        now = datetime.now(timezone.utc)
        for edate in _earnings_dates(calendar):
            if not edate:
                continue
            earnings_ts = _as_utc(pd.to_datetime(edate))
            days_to_earnings = (earnings_ts.to_pydatetime() - now).days
            if (
                -EARNINGS_BLACKOUT_POST_DAYS
                <= days_to_earnings
                <= EARNINGS_BLACKOUT_AHEAD_DAYS
            ):
                notes.append(f"Earnings conflict ({days_to_earnings:+d} days)")
                return True
    except (
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        AttributeError,
        OSError,
        RuntimeError,
    ):
        notes.append("Earnings calendar unverified")
    return False


def _interest_coverage(info: dict) -> float | None:
    coverage = _safe_float(info.get("interestCoverage"))
    if coverage is not None:
        return coverage

    ebitda = _safe_float(info.get("ebitda"))
    interest = _safe_float(info.get("interestExpense"))
    if ebitda is None or interest is None or interest == 0:
        return None
    return ebitda / abs(interest)


def _is_financial_issuer(info: dict) -> bool:
    sector = str(info.get("sector") or "").lower()
    industry = str(info.get("industry") or "").lower()
    quote_type = str(info.get("quoteType") or "").upper()
    if quote_type == "ETF":
        return False
    markers = ("bank", "financial", "insurance", "capital market", "asset management")
    return any(m in sector or m in industry for m in markers)


def _eval_debt(info: dict, notes: list[str], score: float) -> tuple[bool, float]:
    if _is_financial_issuer(info):
        notes.append("Financial issuer — leverage model N/A")
        return True, score

    total_debt = _safe_float(info.get("totalDebt"))
    coverage = _interest_coverage(info)

    if total_debt is None or coverage is None:
        notes.append("Debt metrics unverified (Assumed safe)")
        return True, score

    if total_debt > 0 and coverage < MIN_INTEREST_COVERAGE:
        notes.append(
            f"High debt burden (Interest coverage {coverage:.1f}x < "
            f"{MIN_INTEREST_COVERAGE:.0f}x)"
        )
        return False, score

    notes.append(f"Balance sheet debt safe ({coverage:.1f}x coverage)")
    return True, score + 10.0


def _eval_cash_flow(info: dict, notes: list[str], score: float) -> tuple[bool, float]:
    fcf = _safe_float(info.get("freeCashflow"))
    op_cash = _safe_float(info.get("operatingCashflow"))

    if fcf is not None:
        if fcf <= 0:
            notes.append("Negative Free Cash Flow")
            return False, score
        notes.append("Positive TTM Free Cash Flow")
        return True, score + 10.0

    if op_cash is not None:
        if op_cash <= 0:
            notes.append("Negative Operating Cash Flow")
            return False, score
        notes.append("Positive Operating Cash Flow")
        return True, score + 5.0

    notes.append("Cash flow metrics unverified")
    return True, score  # fail open


def _eval_quality(info: dict, notes: list[str], score: float) -> tuple[bool, float]:
    op_margins = _safe_float(info.get("operatingMargins"))
    if op_margins is None:
        notes.append("Operating margin unverified (Assumed ok)")
        return True, score

    if op_margins > 0.15:
        notes.append(f"Strong Operating Margin ({op_margins * 100:.1f}%)")
        return True, score + 20.0
    if op_margins > 0:
        notes.append(f"Positive Margin ({op_margins * 100:.1f}%)")
        return True, score + 10.0

    notes.append(f"Failed quality floor: operating margin {op_margins * 100:.1f}%")
    return False, score


def evaluate_fundamentals(ticker_obj: yf.Ticker) -> FundamentalsResult:
    """Safely evaluate fundamentals; missing Yahoo fields fail open with notes."""
    notes: list[str] = []
    score = 0.0
    has_earnings_conflict = False
    debt_safe = True
    fcf_positive = True
    quality_ok = True
    metadata_complete = True

    try:
        symbol = str(getattr(ticker_obj, "ticker", "") or "").upper()
        if symbol in ETF_SYMBOLS:
            return _etf_fallback()

        try:
            info = ticker_obj.info or {}
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as exc:
            print(f"Warning: {symbol or 'ticker'} info fetch failed: {exc}")
            return _metadata_fallback(
                "Metadata unavailable — passed via technical/macro fallback"
            )

        if not info:
            reason = "Metadata unavailable — passed via technical/macro fallback"
            if symbol in TSX_BLUECHIP_FALLBACK:
                reason = f"TSX blue-chip fallback ({symbol}): Yahoo fundamentals blank"
            return _metadata_fallback(reason)

        quote_type = str(info.get("quoteType") or "").upper()
        if quote_type in {"ETF", "MUTUALFUND", "INDEX"}:
            return _etf_fallback()

        has_earnings_conflict = _check_earnings_blackout(ticker_obj, notes)
        debt_safe, score = _eval_debt(info, notes, score)
        fcf_positive, score = _eval_cash_flow(info, notes, score)
        quality_ok, score = _eval_quality(info, notes, score)

        rev_growth = _safe_float(info.get("revenueGrowth"))
        if rev_growth is not None:
            if rev_growth > 0.05:
                score += 20.0
                notes.append(f"Revenue Growth (+{rev_growth * 100:.1f}%)")
            elif rev_growth >= 0:
                score += 10.0

        if any("unverified" in n.lower() for n in notes):
            metadata_complete = False
            if symbol in TSX_BLUECHIP_FALLBACK:
                notes.append(f"TSX blue-chip technical fallback ({symbol})")

    except (
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        OSError,
        AttributeError,
        RuntimeError,
    ) as exc:
        print(f"Warning: Fundamental evaluation exception: {exc}")
        return _metadata_fallback(
            "Fundamental check skipped due to API exception — fail-open"
        )

    return FundamentalsResult(
        earnings_conflict=has_earnings_conflict,
        debt_safe=debt_safe,
        fcf_positive=fcf_positive,
        quality_ok=quality_ok,
        notes=", ".join(notes) if notes else "Standard fundamental health",
        score=score,
        metadata_complete=metadata_complete,
    )
