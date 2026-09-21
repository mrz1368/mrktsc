# mrktsc

Personal TSX pullback **alert desk**: screens a curated liquid universe, sizes fixed-risk tickets, books local `PENDING_OPEN` / exit state in SQLite, and pushes HTML cards to Telegram (plus a static dashboard). Aimed at multi-week swing holds with trend + bounce first, then fundamental and sentiment vetoes.

**Not an OMS.** It does not place broker orders. You execute manually. Not financial advice.

---

## How a daily run works

`python scanner.py` roughly:

1. **Context** — load `.env`, open `signals.db`, CNN Fear & Greed, VIX risk multiplier, XIU bull/bear regime.
2. **Book** — confirm `PENDING_OPEN` fills at the next session open (abort if fill fails stop/max-limit checks); evaluate exits on `OPEN` positions.
3. **Heat** — if open risk ≥ `MAX_PORTFOLIO_HEAT_R` (6R), block new buys/inverses (free rolls with stop ≥ entry count as 0R).
4. **Screen** — batch OHLCV for the watchlist; technical pre-screen; fundamentals + news only when tech arms (“deep scan”).
5. **Dispatch** — buy / inverse / watch Telegram tickets + cooldown + sector sleeve rules; idle-cash alert if no setups.
6. **Publish** — write `dist/index.html`.

Typical schedule: after TSX close (or via GitHub Actions) so daily bars are settled.

---

## Strategy gates

Constants live in [`thresholds.py`](thresholds.py). Hard failures block; missing Yahoo fundamental fields **fail open** (labeled Unverified on cards).

| Layer | Rule (summary) |
|--------|----------------|
| **Regime** | XIU vs 200 SMA (3-day confirmation) → bull longs or bear inverses. Extreme Greed (score ≥ 75) blocks new longs, not inverses. VIX scales `PORTFOLIO_RISK_CAD` (&lt;15 → 1.25×, &gt;25 → 0.5×). |
| **Technical (long)** | Above 150/200 SMA, 50 &gt; 200; close within 2.5% **above** SMA50; 63d ROC beats XIU; bullish close with CLV ≥ 0.60; rising SMA50 slope, RVOL ≥ 1.0, support intact (≤1×ATR wick), ADX ≥ 25. |
| **Technical (inverse)** | Bear mirror on the underlying; size on BetaPro vehicle (`CFOD.TO` / `NRGD.TO` / `CNDI.TO`). |
| **Watch** | Bull structure, not in pullback, within 3% of SMA200; no size. |
| **Fundamentals** | Earnings blackout −2 / +7 days; interest coverage ≥ 2×; positive FCF/OCF; operating-margin quality — missing fields assumed safe with notes. |
| **Sentiment** | 7-day negative headline velocity veto. |
| **Liquidity** | 20-day median dollar volume ≥ $5M CAD; last close ≥ $5. |
| **Slippage** | Ex-ante round-trip friction vs 1.5R target; reject if edge is wiped out. |
| **Risk / book** | Fixed CAD risk, stop = 1.5×ATR; ceil(⅓) scale-out at +1.5R, runner trails 20 EMA; max one open alert per sector; cooldown on `(ticker, alert_type)`. |

### Alert types

| Card | When |
|------|------|
| Position setup | Bull path fully armed → book `PENDING_OPEN` on the equity |
| Inverse setup | Bear path → book `PENDING_OPEN` on the sector inverse ETF |
| Watchlist radar | Near-200 retest; diagnostic only, `shares=0` |
| Idle cash | No setups dispatched → remain in `CASH.TO` |

### Sizing example

With `PORTFOLIO_RISK_CAD=20`, entry `100`, ATR `2`:

- Risk unit \(R = 1.5 \times ATR = 3\)
- Shares = `floor(20 / 3) = 6` (minimum 3 so a ⅓ scale-out is possible)
- Stop `97`, T1 `104.50` (sell `ceil(6/3)=2`), runner 4 shares trail 20 EMA after de-risk
- Max limit ≈ entry + 0.15×ATR (gap-fill abort if next open exceeds it)

---

## Project layout

```text
mrktsc/
├── scanner.py            # Orchestrator
├── book.py               # Pending fills + open exits
├── dispatch.py           # Buy / inverse / watch tickets
├── gates.py              # Tech pre-screen + arm_setup
├── indicators.py         # SMA/EMA/ATR/ADX/RVOL + flags
├── exits.py              # Tranche / trail / earnings / regime exits
├── sizing.py             # Fixed-risk size, slippage, portfolio heat
├── fundamentals.py       # Debt / FCF / earnings (fail-open)
├── sentiment.py          # Fear & Greed + news velocity
├── regime.py             # VIX multiplier + XIU bull/bear
├── market_data.py        # yfinance batch/retries
├── universe.py           # Watchlist, sectors, inverse map
├── thresholds.py         # Strategy constants
├── db.py                 # SQLite cooldowns + active book
├── cards.py / dashboard.py / templates/
├── telegram_notify.py
├── config.py             # .env loader
├── test_run.py           # Optional live smoke (Telegram)
├── tests/                # Offline pytest suite
└── .github/workflows/    # ci.yml, daily_scan.yml
```

---

## Setup

Requires **Python 3.11+** (matches CI / `.python-version`). If your existing
`venv` was created on 3.9 or 3.10, recreate it on 3.11 when convenient — do not
reuse an old venv after switching interpreters.

```bash
git clone https://github.com/mrz1368/mrktsc.git
cd mrktsc

python3.11 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

pip install -r requirements.txt
# For lint/tests:
pip install -r requirements-dev.txt

cp .env.example .env
# Fill Telegram credentials and risk settings
```

Requires network access to Yahoo Finance, CNN Fear & Greed, and Telegram. CI uses **Python 3.11**.

### Environment

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | yes | BotFather token |
| `TELEGRAM_CHAT_ID` | yes | Destination chat / channel ID |
| `PORTFOLIO_RISK_CAD` | yes | Base CAD risked per trade (then VIX-scaled) |
| `COOLDOWN_DAYS` | no | Default `5` |
| `SIGNALS_DB` | no | Default `signals.db` (gitignored) |

Never commit `.env` or tokens.

---

## Local run and tests

```bash
source venv/bin/activate
python scanner.py          # full scan (needs .env + network)
python test_run.py         # sizing + DB cooldown + one Telegram card

pytest                     # offline unit tests under tests/
ruff check .               # lint only (see ruff.toml; format not enforced)
```

---

## GitHub Actions

### CI ([`ci.yml`](.github/workflows/ci.yml))

- On pull requests and pushes to `main`
- `ruff check .` then `pytest` (offline; does not run `scanner.py`)

### Daily scan ([`daily_scan.yml`](.github/workflows/daily_scan.yml))

- Mon–Fri **16:15 America/Toronto**, plus manual **Run workflow**
- Secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (`PORTFOLIO_RISK_CAD=20` in the workflow)
- Restores/saves `signals.db` via Actions cache with a **rolling** key `signals-db-<run_id>` and restore prefix `signals-db-` (write-once cache; weekday mutations need a new key each run)
- DB is **not** committed to `main`
- Uploads `dist/` and deploys to **GitHub Pages** (Settings → Pages → Source: GitHub Actions)

---

## Configuration

- **Secrets / risk dollars** → `.env` via [`config.py`](config.py)
- **Strategy numbers** (ADX, pullback %, heat, blackout windows, VIX bands, etc.) → [`thresholds.py`](thresholds.py)
- **Tickers / sectors / inverses** → [`universe.py`](universe.py)

---

## Safety notes

- **Fail-open fundamentals** — incomplete Yahoo `.TO` metadata does not hard-reject; cards show Unverified Fundamentals. Gate math stays pass unless an explicit bad reading is present.
- **EOD mark vs open** — signals and exit marks use the signal day’s close; fills confirm at the **next session open**. That mark is optimistic; live fills can differ. Pending opens abort if the open is through the stop or above `max_limit_price`.
- **Manual execution** — Telegram is the ticket; the SQLite book tracks cooldowns and alerted risk for the scanner only.
- **Inverse ETFs** — daily-levered products; path dependency and decay matter for multi-week holds.

---

## License

Personal research use. No warranty; trading involves risk of loss.
