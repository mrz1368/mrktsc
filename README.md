# mrktsc — TSX Pullback Signal Engine

Local Python scanner that watches a curated TSX universe for high-quality pullbacks, sizes fixed-risk tickets, and pushes HTML alerts to Telegram.

It is built for **swing holds of roughly 3–6 weeks**: trend + bounce confirmation first, then hard fundamental and sentiment vetoes so charts alone cannot force a trade.

> **Not financial advice.** This is a personal research / alert tool. You are responsible for every order you place.

---

## Features

| Layer | What it does |
|--------|----------------|
| **Regime** | XIU vs 200-day SMA → bull longs or bear inverse ETFs; VIX scales risk |
| **Technicals** | 50/150/200 SMA structure, RS vs XIU, bounce/rejection candle, RVOL, ADX, SMA50 slope, ATR penetration |
| **Fundamentals** | Earnings blackout (−2 / +7 days), interest coverage, FCF/OCF, operating margin — with fail-open handling for messy Yahoo `.TO` metadata |
| **Sentiment** | CNN Fear & Greed (blocks Extreme Greed); 7-day negative headline velocity |
| **Risk** | Fixed CAD risk per trade, 1.5×ATR stop, ⅓ scale-out at +1.5R, runner trails 20 EMA |
| **Portfolio rules** | Max one open alert per sector; idle cash → stay in `CASH.TO` |
| **Delivery** | Telegram cards with ✅/❌ bulletproof checklist |

---

## Alert types

| Card | When |
|------|------|
| 🟢 **POSITION SETUP** | Bull regime + pullback bounce + all hard gates |
| 🔴 **INVERSE SETUP** | Bear regime + resistance rejection → BetaPro inverse ETF |
| 🟡 **WATCHLIST RADAR** | Near 200 SMA retest; diagnostic checklist, no size |
| 💤 **IDLE CASH** | No setups passed — remain in `CASH.TO` |

SQLite cooldown prevents re-alerting the same `(ticker, alert_type)` within `COOLDOWN_DAYS`.

---

## Project layout

```text
mrktsc/
├── scanner.py           # Orchestrator (scan → gate → dispatch)
├── indicators.py        # SMA/EMA/ATR/ADX/RVOL + setup flags
├── fundamentals.py      # Debt / FCF / earnings (yfinance-safe)
├── sentiment.py         # Fear & Greed + news velocity
├── regime.py            # VIX multiplier + XIU bull/bear
├── universe.py          # Watchlist, sectors, inverse map
├── sizing.py            # Fixed-risk position sizing
├── db.py                # SQLite cooldown store
├── telegram_notify.py   # HTML alert formatters
├── config.py            # .env loader
├── test_run.py          # Smoke test (sizing + DB + Telegram)
├── .env.example
└── requirements.txt
```

---

## Requirements

- Python **3.9+**
- A Telegram bot token ([BotFather](https://t.me/BotFather)) and your chat ID
- Network access for Yahoo Finance, CNN Fear & Greed, and Telegram

---

## Setup

```bash
git clone https://github.com/mrz1368/mrktsc.git
cd mrktsc

python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# Edit .env with real Telegram credentials and risk settings
```

### Environment

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | yes | BotFather token |
| `TELEGRAM_CHAT_ID` | yes | Destination chat / channel ID |
| `PORTFOLIO_RISK_CAD` | yes | CAD risked per trade (stop distance = 1.5×ATR) |
| `COOLDOWN_DAYS` | no | Default `5` |
| `SIGNALS_DB` | no | Default `signals.db` |

`.env` is gitignored. Never commit tokens.

---

## Usage

**Smoke test** (sizing math, SQLite cooldown, one Telegram setup card):

```bash
source venv/bin/activate
python test_run.py
```

**Live scan** (full watchlist):

```bash
python scanner.py
```

Typical schedule: run after the TSX close (or via GitHub Actions) so daily bars are settled.

### GitHub Actions (daily scan)

Workflow: [`.github/workflows/daily_scan.yml`](.github/workflows/daily_scan.yml)

- Runs **Mon–Fri at 16:15 America/Toronto** (4:15 PM ET), plus manual **Run workflow**
- Add repository secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- Caches `signals.db` between CI jobs so cooldowns persist (does not commit the DB to `main`)
- Writes `dist/index.html` and deploys it to **GitHub Pages** (Settings → Pages → Source: GitHub Actions)

---

## How a long setup is scored

A 🟢 alert only fires when **all** of the following hold:

1. **Market:** XIU in bull regime; Fear & Greed ≤ 75  
2. **Structure:** Price above 150 & 200 SMA; 50 SMA above 200  
3. **Pullback:** Close within ~2.5% of the 50 SMA  
4. **Leadership:** 63-day ROC beats XIU  
5. **Trigger:** Bullish close in the upper half of the day’s range  
6. **Bulletproof tech:** Rising 50 SMA (5-day slope), RVOL ≥ 1.0, low ≥ SMA50 − 1×ATR, ADX ≥ 25  
7. **Fundamentals:** No earnings blackout; debt/FCF/margin pass when Yahoo provides data (missing fields fail **open** with a note)  
8. **News:** No negative headline cluster in the last 7 days  
9. **Book:** Sector sleeve free; cooldown clear; position sizeable at current risk

Bear path mirrors this into BetaPro inverses (`HFD.TO` / `HED.TO` / `HXD.TO`).

---

## Sizing example

With `PORTFOLIO_RISK_CAD=20`, entry `100`, ATR `2`:

- Risk unit \(R = 1.5 \times ATR = 3\)  
- Shares = `floor(20 / 3) = 6` (minimum 3 for a 1/3 scale-out)  
- Stop `97`, T1 `104.50` (sell 2), runner 4 shares trail 20 EMA  

VIX scales the risk budget (e.g. &lt;15 → 1.25×, &gt;25 → 0.5×).

---

## Data caveats

Market data and fundamentals come from **yfinance**. Canadian `.TO` fundamentals are often incomplete. The fundamental engine treats missing fields as **unverified / assumed safe** and only hard-rejects on *explicit* bad readings (e.g. known coverage &lt; 2×, negative FCF). For larger capital, consider a paid fundamentals API (FMP, EODHD, etc.).

---

## Lint

```bash
pip install ruff
ruff check .
```

---

## License

Use and modify for personal research. No warranty; trading involves risk of loss.
