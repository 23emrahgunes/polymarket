# Ghost Trader v1.0 👻🤖

## Project Overview

**Ghost Trader v1.0** is an automated Python bot designed to identify and exploit price discrepancies between prediction markets on **Polymarket** and the real-time prices on **Binance Futures**.

Using the **Black-Scholes model** for probability assessment and **Historical Volatility** from Binance ticker updates, the bot identifies when a Polymarket share is "mispriced" relative to its implied mathematical probability (the "Edge"). A secondary analysis is performed by **Claude 3.5 Sonnet** to determine a final confidence score before executing a trade in a high-fidelity **Paper Trading** environment.

---

## Architecture

The bot is designed to be modular and asynchronous:

- **`src/scanner.py`**: A high-frequency market scanner using `ccxt.pro` for Binance WebSockets and `py-clob-client` for Polymarket market discovery.
- **`src/logic.py`**: The "Math Engine" that calculates:
  - **Historical Volatility (HV)** from 24h of 1m ticker data.
  - **Implied Probability** using the Black-Scholes risk-neutral model.
  - **RSI (14)** for technical signal confirmation.
- **`src/brain.py`**: The AI module that integrates with Claude 3.5 Sonnet to generate a confidence score (currently mocked for v1.0).
- **`src/trading.py`**: A robust Paper Trading module with a **$1,000 virtual balance**, double-spending prevention via `asyncio.Lock`, and automatic resolution tracking.
- **`src/database.py`**: A persistence layer using **SQLite (`aiosqlite`)** to store trade history and wallet balance.
- **`src/main.py`**: The central orchestrator that ties all components together in an `asyncio` event loop.

---

## Installation

1. **Clone the repository:**
   ```bash
   git clone <repository_url>
   cd ghost-trader
   ```

2. **Create a virtual environment:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

---

## Configuration

The bot uses environment variables for configuration. Create a `.env` file based on the example:

```bash
cp .env.example .env
```

**Key Parameters:**
- `VIRTUAL_BALANCE`: Starting amount for the paper trading wallet ($1000).
- `TRADE_SIZE_FIXED`: Amount spent per trade ($50).
- `EDGE_THRESHOLD`: Minimum price gap to trigger a trade (5% = 0.05).
- `CONFIDENCE_THRESHOLD`: Minimum AI confidence required to execute (0.7).

---

## How to Run

### Start the Bot
The bot runs as a long-lived process that continuously scans the markets.
```bash
python src/main.py
```

### Run Automated Tests
We use `pytest` for unit testing of all core modules.
```bash
pytest tests/
```

---

## Docker Support

You can run the Ghost Trader in a containerized environment to ensure stability and persistence.

1. **Build the image:**
   ```bash
   docker build -t ghost-trader .
   ```

2. **Run the container:**
   ```bash
   docker run -d --name ghost-bot \
     -v $(pwd)/data:/app/data \
     --env-file .env \
     ghost-trader
   ```
*The `-v` flag ensures that the `ghost_trader.db` is persisted on your host machine even if the container is removed.*

---

## Database & Logs

The bot maintains a structured trade history in **`data/ghost_trader.db`**. You can view your trade results and wallet balance using any SQLite viewer or the command line:

```bash
# View open trades
sqlite3 data/ghost_trader.db "SELECT * FROM trades WHERE status = 'OPEN';"

# Check virtual wallet balance
sqlite3 data/ghost_trader.db "SELECT balance FROM wallet WHERE id = 1;"
```

---

*Ghost Trader v1.0 - Trading in the shadows.* 🌙
