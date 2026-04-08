# Ghost Trader v1.0

Ghost Trader is a default-PAPER multi-venue bot that monitors all active Polymarket categories, evaluates deterministic trade signals, writes venue-aware state to SQLite, and keeps the full runtime decision path observable at `INFO`.

## Runtime Model

- Default mode is `PAPER`. Real-money trading is not enabled by default.
- Venue model:
  - `polymarket`: active now, PAPER by default
  - `binance_futures`: futures-led crypto venue, PAPER by default, disabled by default
  - `binance_spot`: long-only crypto PAPER venue, disabled by default
- All Polymarket categories are monitored:
  - `CRYPTO`: tradable through Polymarket plus optional Binance Futures venue
  - `SPORTS`: tradable through whale/orderflow signals
  - `POLITICS`: tradable through whale/orderflow signals
  - `OTHER`: tradable through whale/orderflow signals
- Discovery does not silently idle on non-crypto markets. It logs `route_whale_orderflow_only` at `INFO`.
- Runtime scoring is deterministic, explainable, reproducible, category-aware, and logged at `INFO`.
- Normal runtime never fabricates whale/activity events. Synthetic verification inputs exist only when `DEBUG_SIGNAL_MODE=true`.
- Crypto routing is independent by venue: the same signal may open a Polymarket position, a Binance Futures paper position, both, or neither depending on venue-specific thresholds and guards.
- Whale source mode is now `hybrid_cache`:
  - activity-discovered wallets persisted in SQLite are preferred
  - live leaderboard results are merged when available
  - `WHALE_LIST` acts as a manual seed source
  - built-in static seeds are last resort bootstrap only
  - leaderboard outages no longer force the runtime down to a 3-wallet-only mode

## Deterministic Scoring

Every discovery or orderflow decision is scored against a category profile with explicit:

- minimum 24h liquidity
- minimum whale notional
- minimum cluster wallet count
- maximum orderbook spread
- maximum price drift
- minimum decision score
- fixed PAPER trade size

Decision logs look like:

```text
[DECISION] venue=polymarket source=activity category=SPORTS market=... score=0.73 threshold=0.72 trade_size=40.00 inputs={...}
[REJECT] venue=polymarket source=discovery category=CRYPTO market=... reasons=missing_exchange_price score=0.00 threshold=0.68 inputs={...}
[DECISION] venue=binance_futures source=binance_futures_price_structure category=CRYPTO market=BTC/USDT:USDT score=0.79 threshold=0.70 trade_size=100.00 inputs={...}
```

Successful PAPER inserts log:

```text
[PAPER-TRADE-RUNTIME] venue=polymarket inserted trade id=... market=... side=YES balance_before=1000.00 balance_after=960.00 source=activity category=SPORTS
[PAPER-TRADE-RUNTIME] venue=binance_futures inserted trade id=... market=BTC/USDT:USDT side=LONG balance_before=1000.00 balance_after=950.00 source=binance_futures_price_structure ...
```

## Futures-Led Crypto

`CRYPTO` markets now use a shared futures-led signal engine before venue-specific gating.

Shared crypto signal inputs:

- Polymarket mid price and spread
- spot reference price
- Binance Futures last/mark price
- Binance Futures volume, open interest, funding, and microstructure
- optional whale/activity orderflow hint

Then venue-specific gating applies:

- `polymarket`: existing binary-market liquidity, spread, expiry, and edge checks
- `binance_futures`: isolated margin only, fixed `2x`, hard SL/TP, max position, max daily loss, slippage and exposure limits
- `binance_spot`: long-only spot PAPER execution with hard SL/TP, max position, max daily loss, and slippage limits

If both venues qualify, both can open independently.

## VPS One-Command Bootstrap

`git clone` by itself does not execute code, so the unattended VPS flow is:

```bash
git clone <repo-url>
cd polymarket
chmod +x scripts/bootstrap_vps.sh
./scripts/bootstrap_vps.sh
```

Run the bootstrap as the same non-root user that cloned the repo. The script uses `sudo` only for package installation and `systemd` registration.

What the bootstrap does:

- installs Linux packages needed for the bot
- creates `.venv`
- installs Python requirements
- creates `.env` from `.env.example` if missing
- runs `pytest -q`
- runs deterministic runtime verification
- installs and starts a `systemd` service called `ghost-trader`

After that, the service restarts automatically on reboot.

## One-File VPS Refresh

After the first bootstrap, daily VPS operations can run through a single script:

```bash
chmod +x scripts/vps_refresh_and_evaluate.sh
./scripts/vps_refresh_and_evaluate.sh
```

Default behavior is full verification:

- `git fetch` + `git pull --ff-only`
- dependency sync
- `pytest -q`
- sports runtime proof
- crypto dual-venue proof
- crypto triple-venue proof
- `ghost-trader` service restart
- performance analysis
- backtest insufficiency / replay report
- SWOT / verdict report
- runtime DB state
- systemd status and last 50 service logs

Useful variants:

```bash
./scripts/vps_refresh_and_evaluate.sh --quick
./scripts/vps_refresh_and_evaluate.sh --branch ghost-trader-v1-0-impl-1884939158518981932
./scripts/vps_refresh_and_evaluate.sh --service ghost-trader
```

Behavior notes:

- the script fails fast if `.venv`, `.env`, or the `ghost-trader` service is missing
- local git changes stop the run; nothing is auto-stashed or reset
- one operation log is written to `logs/vps_refresh_latest.log`
- the final terminal summary reads:
  - current commit
  - branch
  - test / proof / analysis status
  - service status
  - `live_paper_closed`
  - `synthetic_total`
  - `core.expectancy`
  - SWOT `final_verdict`

Role split:

- `scripts/bootstrap_vps.sh`: first installation only
- `scripts/vps_refresh_and_evaluate.sh`: day-to-day refresh and validation
- `scripts/check_runtime.sh`: lightweight healthcheck

## Installation

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Configuration

Copy `.env.example` to `.env` and adjust values as needed if you are not using the bootstrap script.

Important variables:

- `EXCHANGE_ID=coinbase`
- `VIRTUAL_BALANCE=1000.0`
- `DEBUG_SIGNAL_MODE=false`
- `DEBUG_SIGNAL_PROFILE=sports`
- `RUNTIME_VERIFY_ONCE=false`
- `GHOST_TRADER_DB_PATH=data/ghost_trader.db`
- `WHALE_TARGET_COUNT=50`
- `WHALE_DISCOVERY_MIN_EVENT_USD=2500`
- `WHALE_DISCOVERY_MIN_EVENTS=2`
- `WHALE_DISCOVERY_SINGLE_EVENT_USD=10000`
- `WHALE_CONNECT_TIMEOUT_SEC=3`
- `WHALE_READ_TIMEOUT_SEC=6`
- `WHALE_INSPECTION_CONCURRENCY=8`
- `WHALE_LIST=` optional comma-separated manual seed wallets
- `BINANCE_FUTURES_ENABLED=false`
- `BINANCE_FUTURES_MODE=paper`
- `BINANCE_FUTURES_LEVERAGE=2`
- `BINANCE_FUTURES_MARGIN_MODE=isolated`
- `BINANCE_FUTURES_STOP_LOSS_PCT=0.03`
- `BINANCE_FUTURES_TAKE_PROFIT_PCT=0.06`
- `BINANCE_SPOT_ENABLED=false`
- `BINANCE_SPOT_MODE=paper`
- `BINANCE_SPOT_MAX_ORDER_USD=100`
- `BINANCE_SPOT_MAX_POSITION_USD=200`
- `BINANCE_SPOT_MAX_DAILY_LOSS_USD=100`
- `BINANCE_SPOT_MAX_OPEN_POSITIONS=3`
- `BINANCE_SPOT_FEE_BPS=10`
- `BINANCE_SPOT_SLIPPAGE_LIMIT_BPS=20`
- `BINANCE_SPOT_SIGNAL_THRESHOLD=0.72`
- `BINANCE_SPOT_STOP_LOSS_PCT=0.03`
- `BINANCE_SPOT_TAKE_PROFIT_PCT=0.06`
- verify-only helpers:
  - `VERIFY_REQUIRED_VENUES=polymarket`
  - `VERIFY_REQUIRED_CATEGORY=SPORTS`

## Performance Evaluation

The repo now includes a conservative strategy-evaluation layer. Its job is not to promise profit, but to measure what is actually knowable and say `insufficient evidence` when the data is weak.

Core rules:

- `live_paper` runtime trades are the primary evidence source
- `synthetic_verify` trades from debug/proof harnesses are excluded from core performance by default
- `replay` / backtest results are reported separately and never mixed into live paper alpha claims
- if there is not enough trustworthy data, the final verdict will be `IMPROVE FIRST`

Main commands:

```bash
python scripts/analyze_performance.py
python scripts/run_backtest.py
python scripts/swot_report.py
```

Outputs are written under:

```bash
reports/performance/
```

Main files:

- `summary.json`
- `summary.csv`
- `rejections.csv`
- `summary.md`
- `backtest.json`
- `swot_report.md`
- `swot_report.json`

What `analyze_performance.py` measures:

- total / closed / open trades
- win rate
- average win / average loss
- expectancy
- profit factor
- total / realized / unrealized pnl
- venue / category / signal-family comparisons
- max drawdown
- longest losing streak
- average hold time
- open exposure by venue / category
- average entry spread
- average slippage proxy
- rejection counts by reason
- confidence bucket performance
- whale trust bucket performance

What `run_backtest.py` does today:

- supports deterministic replay only when you provide a structured replay dataset
- defaults to an explicit `insufficient_evidence` report when the repo does not contain enough trustworthy historical data
- does **not** fabricate Polymarket non-crypto replay when historical orderbook / whale archive data is missing

What `swot_report.py` decides:

- `GO`
- `NO-GO`
- `IMPROVE FIRST`

The default expected result on a fresh repo is usually `IMPROVE FIRST`, because proof-harness trades are synthetic and do not count as live alpha evidence.

## Run

Start the bot:

```bash
python -m src.main
```

Run tests:

```bash
pytest -q
```

Run deterministic runtime verification:

```bash
python scripts/verify_runtime_trade.py
```

Run deterministic dual-venue crypto verification:

```bash
python scripts/verify_crypto_dual_venue.py
```

Run deterministic triple-venue crypto verification:

```bash
python scripts/verify_crypto_triple_venue.py
```

## DEBUG_SIGNAL_MODE

`DEBUG_SIGNAL_MODE=true` is an opt-in verification mode.

When enabled:

- runtime uses deterministic synthetic verification inputs
- `DEBUG_SIGNAL_PROFILE=sports` injects one active `SPORTS` market and one matching orderflow event
- `DEBUG_SIGNAL_PROFILE=crypto_dual` injects one active `CRYPTO` market plus one crypto orderflow hint used by the shared futures-led signal engine
- `DEBUG_SIGNAL_PROFILE=crypto_triple_long` injects one active `CRYPTO` market that produces a deterministic `LONG` shared signal for Polymarket, Futures, and Spot
- the decision engine evaluates the event normally
- a PAPER trade is inserted into SQLite
- wallet balance decreases

When disabled:

- no synthetic whale/activity events are created
- discovery and orderflow only use real upstream data

## Whale Source Hardening

Whale discovery is no longer "Gamma leaderboard or 3 static wallets". The runtime now keeps a persisted whale cache in SQLite.

How the source works:

- global activity is used to discover strong wallets across all categories
- discovery candidates are persisted in `whale_wallets`
- wallet ranking is deterministic and uses:
  - recent event notional
  - event frequency
  - DB trust score
  - recency of successful inspection
  - timeout/failure penalties
- if Gamma leaderboard is slow or unavailable, the runtime falls back to the learned cache before touching manual/static seeds

Operationally this means:

- `tracked_whales=3` is no longer the expected steady-state fallback
- `STATUS` logs now include:
  - `leaderboard_wallets`
  - `activity_discovered_wallets`
  - `persisted_wallets`
  - `wallet_timeouts_last_cycle`
  - `source_mode`
- clear `INFO` logs explain degraded whale source states such as:
  - `leaderboard_unavailable`
  - `wallet_activity_timeout`
  - `whale_source_unavailable`
  - `seed_only_mode`

## Dual-Venue Crypto Verification

The dual-venue harness keeps normal defaults intact:

- `polymarket` stays PAPER-first
- `binance_futures` stays disabled in normal runtime
- the harness enables `binance_futures` only for its own isolated verification run

Command:

```bash
python scripts/verify_crypto_dual_venue.py
```

What it proves in one run:

- one deterministic `CRYPTO` market is discovered by the real runtime
- the shared futures-led crypto score is produced
- `polymarket` and `binance_futures` both log their own `[DECISION]`
- both venues insert independent PAPER trades into SQLite
- both venue balances decrease
- `binance_futures` creates `STOP_LOSS` and `TAKE_PROFIT` protection orders

## Binance Spot PAPER

`binance_spot` is now a PAPER crypto venue with long-only semantics.

Behavior:

- `LONG` signal opens a spot PAPER long
- `SHORT` signal closes an existing spot long via `signal_exit`
- if no spot long exists, `SHORT` logs `spot_short_not_supported`
- spot does not simulate synthetic shorts or margin
- spot creates virtual `STOP_LOSS` and `TAKE_PROFIT` orders in SQLite

## Triple-Venue Crypto Verification

The triple-venue harness keeps normal defaults intact while enabling `binance_futures` and `binance_spot` only inside an isolated debug run.

Command:

```bash
python scripts/verify_crypto_triple_venue.py
```

What it proves in one run:

- one deterministic `CRYPTO` market is discovered by the real runtime
- the shared futures-led crypto score is produced as a `LONG`
- `polymarket`, `binance_futures`, and `binance_spot` each log their own `[DECISION]`
- all three venues insert independent PAPER trades into SQLite
- all three venue balances decrease
- futures creates `STOP_LOSS` and `TAKE_PROFIT`
- spot creates virtual `STOP_LOSS` and `TAKE_PROFIT`
- futures and spot both leave one open PAPER position in SQLite

Expected proof sections:

- `DB_PATH`
- `POLYMARKET_WALLET_BEFORE/AFTER`
- `BINANCE_FUTURES_WALLET_BEFORE/AFTER`
- `TRADES`
- `POSITIONS`
- `ORDERS`
- `LOG_EXCERPT`

## SQLite Verification

Default SQLite path:

```bash
data/ghost_trader.db
```

Verification SQLite path used by the harness:

```bash
data/runtime_verification.db
```

Dual-venue crypto verification SQLite path:

```bash
data/runtime_crypto_dual_venue.db
```

Triple-venue crypto verification SQLite path:

```bash
data/runtime_crypto_triple_venue.db
```

Example queries:

```bash
sqlite3 data/runtime_verification.db "SELECT balance FROM wallet WHERE id = 1;"
sqlite3 data/runtime_verification.db "SELECT id, market_id, side, size, price, confidence, whale_address, timestamp FROM trades ORDER BY id ASC;"
sqlite3 data/ghost_trader.db "SELECT venue, execution_mode, cash_balance, equity, available_balance FROM venue_accounts ORDER BY venue;"
sqlite3 data/ghost_trader.db "SELECT venue, symbol_or_market_id, side, status, unrealized_pnl, realized_pnl FROM venue_positions ORDER BY id DESC;"
sqlite3 data/ghost_trader.db "SELECT source_type, COUNT(*) FROM whale_wallets WHERE enabled = 1 GROUP BY source_type ORDER BY source_type;"
sqlite3 data/ghost_trader.db "SELECT address, source_type, discovery_score, last_event_amount, event_count_24h, failure_streak FROM whale_wallets WHERE enabled = 1 ORDER BY discovery_score DESC LIMIT 10;"
sqlite3 data/runtime_crypto_dual_venue.db "SELECT venue, execution_mode, cash_balance, equity, available_balance FROM venue_accounts ORDER BY venue;"
sqlite3 data/runtime_crypto_dual_venue.db "SELECT venue, instrument_type, market_id, side, size, price, confidence, source_signal FROM trades ORDER BY id ASC;"
sqlite3 data/runtime_crypto_dual_venue.db "SELECT venue, symbol_or_market_id, side, status, notional_usd FROM venue_positions ORDER BY id ASC;"
sqlite3 data/runtime_crypto_dual_venue.db "SELECT venue, symbol_or_market_id, order_type, side, stop_price, status FROM venue_orders ORDER BY id ASC;"
sqlite3 data/runtime_crypto_triple_venue.db "SELECT venue, execution_mode, cash_balance, equity, available_balance FROM venue_accounts ORDER BY venue;"
sqlite3 data/runtime_crypto_triple_venue.db "SELECT venue, instrument_type, market_id, side, size, price, confidence, source_signal FROM trades ORDER BY id ASC;"
sqlite3 data/runtime_crypto_triple_venue.db "SELECT venue, symbol_or_market_id, side, status, notional_usd FROM venue_positions ORDER BY id ASC;"
sqlite3 data/runtime_crypto_triple_venue.db "SELECT venue, symbol_or_market_id, order_type, side, stop_price, status FROM venue_orders ORDER BY id ASC;"
```

## Docker

Build the production image:

```bash
docker build -t ghost-trader .
```

## Notes

- PAPER remains the default mode.
- Runtime rejection reasons are logged at `INFO`, not hidden behind `DEBUG`.
- Weak/noisy markets are filtered with category-specific liquidity, spread, drift, and scoring thresholds before a PAPER trade is opened.
- Venue balances, positions, and PnL are tracked separately.
- Binance Futures live execution is not active by default; v1 execution is PAPER-first.
- Binance Spot is PAPER-only and long-only in v1; `SHORT` is interpreted as exit-only.
- The dual-venue crypto proof harness enables `BINANCE_FUTURES_ENABLED=true` only inside the verification command, not in the normal daemon defaults.
- The triple-venue crypto proof harness enables both `BINANCE_FUTURES_ENABLED=true` and `BINANCE_SPOT_ENABLED=true` only inside its verification command.
- Service management on VPS:

```bash
sudo systemctl status ghost-trader
sudo journalctl -u ghost-trader -f
./scripts/check_runtime.sh ghost-trader
```
