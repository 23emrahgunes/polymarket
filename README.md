# Ghost Trader v1.0

Ghost Trader is a default-PAPER Polymarket bot that monitors all active categories, evaluates deterministic trade signals, writes trades to SQLite, and keeps the full runtime decision path observable at `INFO`.

## Runtime Model

- Default mode is `PAPER`. Real-money trading is not enabled by default.
- All Polymarket categories are monitored:
  - `CRYPTO`: tradable through discovery pricing plus whale/orderflow signals
  - `SPORTS`: tradable through whale/orderflow signals
  - `POLITICS`: tradable through whale/orderflow signals
  - `OTHER`: tradable through whale/orderflow signals
- Discovery does not silently idle on non-crypto markets. It logs `route_whale_orderflow_only` at `INFO`.
- Runtime scoring is deterministic, explainable, reproducible, category-aware, and logged at `INFO`.
- Normal runtime never fabricates whale/activity events. Synthetic verification inputs exist only when `DEBUG_SIGNAL_MODE=true`.

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
[DECISION] source=activity category=SPORTS market=... score=0.73 threshold=0.72 trade_size=40.00 inputs={...}
[REJECT] source=discovery category=CRYPTO market=... reasons=missing_exchange_price score=0.00 threshold=0.68 inputs={...}
```

Successful PAPER inserts log:

```text
[PAPER-TRADE-RUNTIME] inserted trade id=... market=... side=YES balance_before=1000.00 balance_after=960.00 source=activity category=SPORTS
```

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
- `RUNTIME_VERIFY_ONCE=false`
- `GHOST_TRADER_DB_PATH=data/ghost_trader.db`

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

## DEBUG_SIGNAL_MODE

`DEBUG_SIGNAL_MODE=true` is an opt-in verification mode.

When enabled:

- runtime uses deterministic synthetic verification inputs
- one active `SPORTS` market is injected into the real runtime loop
- one matching whale/orderflow event is injected into the real runtime loop
- the decision engine evaluates the event normally
- a PAPER trade is inserted into SQLite
- wallet balance decreases

When disabled:

- no synthetic whale/activity events are created
- discovery and orderflow only use real upstream data

## SQLite Verification

Default SQLite path:

```bash
data/ghost_trader.db
```

Verification SQLite path used by the harness:

```bash
data/runtime_verification.db
```

Example queries:

```bash
sqlite3 data/runtime_verification.db "SELECT balance FROM wallet WHERE id = 1;"
sqlite3 data/runtime_verification.db "SELECT id, market_id, side, size, price, confidence, whale_address, timestamp FROM trades ORDER BY id ASC;"
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
- Service management on VPS:

```bash
sudo systemctl status ghost-trader
sudo journalctl -u ghost-trader -f
./scripts/check_runtime.sh ghost-trader
```
