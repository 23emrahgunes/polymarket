# Ghost Trader v1.0

Ghost Trader is a default-PAPER multi-venue bot that monitors all active Polymarket categories, evaluates deterministic trade signals, writes venue-aware state to SQLite, and keeps the full runtime decision path observable at `INFO`.

## Runtime Model

- Default mode is `PAPER`. Real-money trading is not enabled by default.
- Venue model:
  - `polymarket`: active now, PAPER by default
  - `binance_futures`: futures-led crypto venue, PAPER by default, disabled by default
  - `binance_spot`: scaffolded for phase 2, disabled by default
- All Polymarket categories are monitored:
  - `CRYPTO`: tradable through Polymarket plus optional Binance Futures venue
  - `SPORTS`: tradable through whale/orderflow signals
  - `POLITICS`: tradable through whale/orderflow signals
  - `OTHER`: tradable through whale/orderflow signals
- Discovery does not silently idle on non-crypto markets. It logs `route_whale_orderflow_only` at `INFO`.
- Runtime scoring is deterministic, explainable, reproducible, category-aware, and logged at `INFO`.
- Normal runtime never fabricates whale/activity events. Synthetic verification inputs exist only when `DEBUG_SIGNAL_MODE=true`.
- Crypto routing is independent by venue: the same signal may open a Polymarket position, a Binance Futures paper position, both, or neither depending on venue-specific thresholds and guards.

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
- `BINANCE_FUTURES_ENABLED=false`
- `BINANCE_FUTURES_MODE=paper`
- `BINANCE_FUTURES_LEVERAGE=2`
- `BINANCE_FUTURES_MARGIN_MODE=isolated`
- `BINANCE_FUTURES_STOP_LOSS_PCT=0.03`
- `BINANCE_FUTURES_TAKE_PROFIT_PCT=0.06`
- verify-only helpers:
  - `VERIFY_REQUIRED_VENUES=polymarket`
  - `VERIFY_REQUIRED_CATEGORY=SPORTS`

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

## DEBUG_SIGNAL_MODE

`DEBUG_SIGNAL_MODE=true` is an opt-in verification mode.

When enabled:

- runtime uses deterministic synthetic verification inputs
- `DEBUG_SIGNAL_PROFILE=sports` injects one active `SPORTS` market and one matching orderflow event
- `DEBUG_SIGNAL_PROFILE=crypto_dual` injects one active `CRYPTO` market plus one crypto orderflow hint used by the shared futures-led signal engine
- the decision engine evaluates the event normally
- a PAPER trade is inserted into SQLite
- wallet balance decreases

When disabled:

- no synthetic whale/activity events are created
- discovery and orderflow only use real upstream data

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

Example queries:

```bash
sqlite3 data/runtime_verification.db "SELECT balance FROM wallet WHERE id = 1;"
sqlite3 data/runtime_verification.db "SELECT id, market_id, side, size, price, confidence, whale_address, timestamp FROM trades ORDER BY id ASC;"
sqlite3 data/ghost_trader.db "SELECT venue, execution_mode, cash_balance, equity, available_balance FROM venue_accounts ORDER BY venue;"
sqlite3 data/ghost_trader.db "SELECT venue, symbol_or_market_id, side, status, unrealized_pnl, realized_pnl FROM venue_positions ORDER BY id DESC;"
sqlite3 data/runtime_crypto_dual_venue.db "SELECT venue, execution_mode, cash_balance, equity, available_balance FROM venue_accounts ORDER BY venue;"
sqlite3 data/runtime_crypto_dual_venue.db "SELECT venue, instrument_type, market_id, side, size, price, confidence, source_signal FROM trades ORDER BY id ASC;"
sqlite3 data/runtime_crypto_dual_venue.db "SELECT venue, symbol_or_market_id, side, status, notional_usd FROM venue_positions ORDER BY id ASC;"
sqlite3 data/runtime_crypto_dual_venue.db "SELECT venue, symbol_or_market_id, order_type, side, stop_price, status FROM venue_orders ORDER BY id ASC;"
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
- The dual-venue crypto proof harness enables `BINANCE_FUTURES_ENABLED=true` only inside the verification command, not in the normal daemon defaults.
- Service management on VPS:

```bash
sudo systemctl status ghost-trader
sudo journalctl -u ghost-trader -f
./scripts/check_runtime.sh ghost-trader
```
