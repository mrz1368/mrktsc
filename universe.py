"""Institutional dynamic universe and execution mapping.

Liquid constituent registry, Median Daily Dollar Volume (MDDV) filters,
GICS sector hierarchies, and liquid inverse hedging vehicles.
"""

from __future__ import annotations

import pandas as pd

# =====================================================================
# INSTITUTIONAL CONSTRAINTS & THRESHOLDS
# =====================================================================
MIN_MDDV_CAD = 5_000_000.0  # $5M CAD daily turnover (eliminates slippage)
MIN_PRICE_CAD = 5.00  # Floor against penny-stock manipulation
MAX_OPEN_PER_SECTOR = 1  # Portfolio concentration risk ceiling
EARNINGS_BLACKOUT_AHEAD_DAYS = 7  # Pre-earnings announcement lockout
EARNINGS_BLACKOUT_POST_DAYS = 2  # Post-earnings reaction digestion lockout
EARNINGS_BLACKOUT_DAYS = EARNINGS_BLACKOUT_AHEAD_DAYS  # backward-compatible alias
NEWS_LOOKBACK_DAYS = 7
EXTREME_GREED_SCORE = 75.0  # Regime sentiment threshold

# Macro Benchmarks & Sweeps
BENCHMARK_TICKER = "XIU.TO"  # iShares S&P/TSX 60 (primary trend arbiter)
BROAD_MARKET_TICKER = "XIC.TO"  # iShares Core S&P/TSX Capped Composite
CASH_ETF = "CASH.TO"  # Horizons High Interest Savings ETF

# =====================================================================
# GICS LEVEL 1 SECTOR TAXONOMY
# =====================================================================
GICS_SECTORS: dict[str, list[str]] = {
    "Financials": [
        "RY.TO",
        "TD.TO",
        "BMO.TO",
        "BNS.TO",
        "CM.TO",
        "BAM.TO",
        "BN.TO",
        "MFC.TO",
        "SLF.TO",
        "POW.TO",
        "IFC.TO",
    ],
    "Energy": [
        "CNQ.TO",
        "SU.TO",
        "ENB.TO",
        "TRP.TO",
        "CVE.TO",
        "TOU.TO",
        "IMO.TO",
        "ARX.TO",
        "WCP.TO",
    ],
    "Industrials": [
        "CNR.TO",
        "CP.TO",
        "WCN.TO",
        "TFII.TO",
        "TRI.TO",
        "CAE.TO",
        "TIH.TO",
        "STN.TO",
    ],
    "Information_Technology": [
        "SHOP.TO",
        "CSU.TO",
        "GIB-A.TO",
        "OTEX.TO",
        "DSG.TO",
        "CTS.TO",
    ],
    "Materials": [
        "ABX.TO",
        "AEM.TO",
        "WPM.TO",
        "NTR.TO",
        "TECK-B.TO",
        "FNV.TO",
        "IVN.TO",
    ],
    "Consumer_Staples": [
        "ATD.TO",
        "L.TO",
        "WN.TO",
        "MRU.TO",
        "EMP-A.TO",
    ],
    "Consumer_Discretionary": [
        "DOL.TO",
        "BYD.TO",
        "QSR.TO",
        "GOOS.TO",
    ],
    "Utilities_Pipelines": [
        "FTS.TO",
        "EMA.TO",
        "AQN.TO",
        "H.TO",
    ],
    "Communication_Services": [
        "BCE.TO",
        "T.TO",
        "RCI-B.TO",
    ],
}

# =====================================================================
# LIQUID INVERSE HEDGING MAP
# =====================================================================
# Strictly liquid instruments. Unhedged sectors fall back to broad market.
SECTOR_INVERSE_MAP: dict[str, str] = {
    "Financials": "CFOD.TO",  # BetaPro Canadian Financials -2x Daily Bear
    "Energy": "NRGD.TO",  # BetaPro Canadian Energy -2x Daily Bear
    "Broad_Market": "CNDI.TO",  # BetaPro S&P/TSX 60 Daily Inverse (-1x)
}

# Absolute leverage used when translating underlying ATR risk onto the inverse ETF.
INVERSE_LEVERAGE: dict[str, float] = {
    "CFOD.TO": 2.0,
    "NRGD.TO": 2.0,
    "CNDI.TO": 1.0,
}


def inverse_etf_for_sector(sector: str) -> str:
    """Return the designated inverse ETF, defaulting to broad market."""
    return SECTOR_INVERSE_MAP.get(sector, SECTOR_INVERSE_MAP["Broad_Market"])


def inverse_leverage(ticker: str) -> float:
    """Absolute daily leverage factor for an inverse vehicle (default 1.0)."""
    return float(INVERSE_LEVERAGE.get(ticker, 1.0))


def ticker_sector(ticker: str) -> str:
    """Identify GICS sector sleeve with reverse lookup."""
    for sector, tickers in GICS_SECTORS.items():
        if ticker in tickers:
            return sector
    return "Broad_Market"


def get_all_universe_tickers() -> list[str]:
    """Flatten the GICS sector registry into an active screening list."""
    tickers: list[str] = []
    for sector_tickers in GICS_SECTORS.values():
        tickers.extend(sector_tickers)
    return sorted(set(tickers))


def liquidity_filter_reason(
    df: pd.DataFrame,
    min_mddv: float = MIN_MDDV_CAD,
    min_price: float = MIN_PRICE_CAD,
) -> str | None:
    """Return a skip note if MDDV or price fails; None if the name is liquid."""
    if df.empty or len(df) < 20:
        return "Insufficient bars for 20-day MDDV screen."

    recent = df.tail(20)
    current_price = float(recent["Close"].iloc[-1])
    if current_price < min_price:
        return f"Price ${current_price:.2f} below ${min_price:.2f} floor."

    daily_dollar_volume = recent["Close"] * recent["Volume"]
    mddv = float(daily_dollar_volume.median())
    if mddv < min_mddv:
        return f"MDDV ${mddv:,.0f} below ${min_mddv:,.0f} CAD floor."
    return None


def filter_by_dollar_liquidity(
    df: pd.DataFrame,
    min_mddv: float = MIN_MDDV_CAD,
    min_price: float = MIN_PRICE_CAD,
) -> bool:
    """Evaluate 20-day Median Daily Dollar Volume (MDDV) and price floor.

    Prevents allocating into micro-liquidity traps or executing inside
    wide bid-ask spreads where market impact eats alpha.
    """
    return liquidity_filter_reason(df, min_mddv=min_mddv, min_price=min_price) is None


# Active scanning universe (flat list ready for the orchestrator loop)
TSX_WATCHLIST = get_all_universe_tickers()
