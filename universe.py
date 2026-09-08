"""Universe constants: watchlist, sector map, and inverse ETF vehicles."""

from __future__ import annotations

BENCHMARK_TICKER = "XIU.TO"
DEFAULT_INVERSE_ETF = "HXD.TO"
CASH_ETF = "CASH.TO"
EXTREME_GREED_SCORE = 75.0
EARNINGS_BLACKOUT_AHEAD_DAYS = 7
EARNINGS_BLACKOUT_POST_DAYS = 2
EARNINGS_BLACKOUT_DAYS = EARNINGS_BLACKOUT_AHEAD_DAYS  # backward-compatible alias
NEWS_LOOKBACK_DAYS = 7
MAX_OPEN_PER_SECTOR = 1
MIN_AVG_VOLUME = 50_000

INVERSE_ETF_MAP = {
    "Financials": "HFD.TO",  # -2x Daily Bear Financials
    "Energy": "HED.TO",  # -2x Daily Bear Energy
    "Broad_Market": "HXD.TO",  # -2x Daily Bear S&P/TSX 60
}

SECTOR_MAP = {
    "Financials": ["RY.TO", "TD.TO", "BMO.TO", "BNS.TO", "BAM.TO"],
    "Energy": ["ENB.TO", "TRP.TO", "CNQ.TO", "SU.TO"],
    "Industrials": ["CNR.TO", "CP.TO", "TRI.TO"],
    "Tech/Retail": ["SHOP.TO", "ATD.TO", "CSU.TO", "DOL.TO"],
    "Index": ["XIU.TO", "VFV.TO", "XQQ.TO"],
    "Real Estate": ["ZRE.TO", "VRE.TO"],
}

TSX_WATCHLIST = [
    "SHOP.TO",
    "RY.TO",
    "TD.TO",
    "BMO.TO",
    "BNS.TO",
    "CNR.TO",
    "CP.TO",
    "ENB.TO",
    "TRP.TO",
    "ATD.TO",
    "CSU.TO",
    "BAM.TO",
    "DOL.TO",
    "XIU.TO",
    "VFV.TO",
    "XQQ.TO",
    "XGD.TO",
    "ZRE.TO",
    "VRE.TO",
]


def ticker_sector(ticker: str) -> str:
    for sector, names in SECTOR_MAP.items():
        if ticker in names:
            if sector == "Index":
                return "Broad_Market"
            return sector
    return "Broad_Market"


def inverse_etf_for_sector(sector: str) -> str:
    return INVERSE_ETF_MAP.get(sector, DEFAULT_INVERSE_ETF)
