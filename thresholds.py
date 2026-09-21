"""Central trading and UI gate thresholds.

Single source of truth for strategy constants so indicators, gates, sizing,
universe filters, Telegram icons, and the dashboard cannot drift.
Env secrets stay in ``config.py``; ticker maps stay in ``universe.py``.
"""

from __future__ import annotations

# --- Technical / indicators -------------------------------------------------
MIN_RVOL = 1.0
MIN_ADX = 25.0
MAX_PENETRATION_ATR = 1.0
SMA_SLOPE_LOOKBACK = 5
PULLBACK_MAX_PCT = 2.5
MIN_CLV_BULL = 0.60
MAX_CLV_BEAR = 0.40

# --- Technical gates --------------------------------------------------------
WATCH_MAX_DIST_TO_200_PCT = 3.0
# Watch / radar invalidation = SMA200 * (1 - buffer).
WATCH_INVALIDATION_BUFFER = 0.015

# --- Sizing / portfolio heat ------------------------------------------------
ATR_STOP_MULT = 1.5
WATCH_R_ATR_MULT = ATR_STOP_MULT  # watch R uses the same 1.5×ATR multiple as stops
TARGET_1_R = 1.5
SCALE_OUT_FRACTION = 1.0 / 3.0
MAX_PORTFOLIO_HEAT_R = 6.0
MAX_LIMIT_ATR_FRACTION = 0.15
MIN_SHARES_FOR_SCALE_OUT = 3

# --- Universe / liquidity / concentration / calendar ------------------------
MIN_MDDV_CAD = 5_000_000.0  # $5M CAD daily turnover
MIN_PRICE_CAD = 5.00
MAX_OPEN_PER_SECTOR = 1
EARNINGS_BLACKOUT_AHEAD_DAYS = 7
EARNINGS_BLACKOUT_POST_DAYS = 2
EARNINGS_BLACKOUT_DAYS = EARNINGS_BLACKOUT_AHEAD_DAYS  # backward-compatible alias
NEWS_LOOKBACK_DAYS = 7
EXTREME_GREED_SCORE = 75.0

# --- Fundamentals -----------------------------------------------------------
MIN_INTEREST_COVERAGE = 2.0

# --- Regime / VIX risk scaling ----------------------------------------------
VIX_LOW = 15.0
VIX_HIGH = 25.0
VIX_MULT_LOW = 1.25
VIX_MULT_HIGH = 0.50
VIX_MULT_NEUTRAL = 1.0
VIX_FALLBACK = 20.0

# --- Sentiment display (Telegram favorable band) ----------------------------
SENTIMENT_FAVORABLE_MAX = 45.0
