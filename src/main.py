import os
import sys
import asyncio
import logging
import time
import random

# Zero-Failure Technical Architecture: absolute path handling
ABS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ABS_ROOT)

from src.scanner import MarketScanner
from src.explorer import MarketExplorer
from src.whale_tracker import WhaleTracker
from src.copy_trader import CopyTrader
from src.database import Database
from src.logic import calculate_black_scholes_prob, calculate_edge, calculate_annualized_volatility, calculate_rsi
from src.brain import Brain
from src.trading import PaperTrader
from src.parser import parse_polymarket_question
import pandas as pd
from datetime import datetime, timezone

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Category-specific Symbols
CRYPTO_MAPPING = {
    "BTC": "BTC/USDT",
    "ETH": "ETH/USDT",
    "SOL": "SOL/USDT",
    "XRP": "XRP/USDT",
    "DOGE": "DOGE/USDT",
    "BNB": "BNB/USDT"
}

# Breaking News detection: price swing > 5% in 15 mins
NEWS_SWING_THRESHOLD = 0.05
PRICE_HISTORY = {} # {market_id: [(timestamp, price)]}

async def run_discovery_loop(explorer, scanner, brain, trader, whale_tracker):
    """
    Ghost Intelligence v2.0 logic: discover, filter, and monitor markets.
    Includes periodic status logging every 5 minutes.
    """
    last_status_log = time.time()
    try:
        while True:
            start_time = time.time()
            logger.info("Ghost Intelligence: Discovering markets...")
            active_markets = await explorer.fetch_active_markets()

            # Extract symbols for crypto monitoring
            crypto_symbols = set()
            for market in active_markets:
                if market.get("category") == "CRYPTO":
                    question = market.get("question", "").upper()
                    for base, pair in CRYPTO_MAPPING.items():
                        if base in question:
                            crypto_symbols.add(pair)

            # Start/Update WebSocket ticker task
            await scanner.update_monitored_symbols(list(crypto_symbols))

            # 2. Main Processing Loop for discovered markets
            for market in active_markets:
                try:
                    category = market.get("category", "OTHER")
                    question = market.get("question", "")
                    market_id = market.get("market_id")
                    volume_24h = float(market.get("volume_24h", 0))

                    # Fetch YES/NO token IDs
                    tokens = market.get("tokens", [])
                    if not tokens: continue
                    token_id = tokens[0].get("token_id") # YES token

                    current_poly_price = await scanner.get_token_price(token_id)
                    if not current_poly_price: continue

                    # [CRYPTO] Parity Arbitrage vs Binance
                    if category == "CRYPTO":
                        for base, symbol in CRYPTO_MAPPING.items():
                            if base in question.upper():
                                current_binance_price = scanner.current_prices.get(symbol)
                                if not current_binance_price: continue

                                # Edge Calculation using Black-Scholes
                                df = await scanner.get_historical_data(symbol)
                                volatility = calculate_annualized_volatility(df['close'])
                                strike_price, expiry_dt = parse_polymarket_question(question)
                                if not strike_price or not expiry_dt: continue

                                now = datetime.now(timezone.utc)
                                time_to_expiry_years = (expiry_dt - now).total_seconds() / (24 * 365 * 3600)
                                if time_to_expiry_years <= 0: continue

                                implied_prob = calculate_black_scholes_prob(current_binance_price, strike_price, time_to_expiry_years, volatility)
                                edge = calculate_edge(current_poly_price, implied_prob)

                                status = "ACTIVE" if abs(edge) > 0.05 else "IDLE"
                                logger.info(f"[CRYPTO] [{question[:30]}] | Price: ${current_poly_price:.2f} | 24h Vol: ${volume_24h:.0f} | SIGNAL: {status}")

                                if edge > 0.05:
                                    rsi = calculate_rsi(df['close']).iloc[-1]
                                    confidence = await brain.get_confidence(edge, rsi, volume_24h, 0.0)
                                    if confidence > 0.7:
                                        await trader.execute_trade(market_id, "YES", 50.0, current_poly_price, edge, confidence)

                    # [POLITICS/FINANCE/OTHER] Volatility-based "Breaking News" detection
                    else:
                        if market_id not in PRICE_HISTORY:
                            PRICE_HISTORY[market_id] = []
                        PRICE_HISTORY[market_id].append((time.time(), current_poly_price))
                        PRICE_HISTORY[market_id] = [(t, p) for t, p in PRICE_HISTORY[market_id] if time.time() - t < 900]

                        if len(PRICE_HISTORY[market_id]) > 2:
                            price_swing = (PRICE_HISTORY[market_id][-1][1] - PRICE_HISTORY[market_id][0][1]) / PRICE_HISTORY[market_id][0][1]
                            status = "ACTIVE" if abs(price_swing) > NEWS_SWING_THRESHOLD else "IDLE"
                            logger.info(f"[{category}] [{question[:30]}] | Price: ${current_poly_price:.2f} | 24h Vol: ${volume_24h:.0f} | SIGNAL: {status}")

                            if status == "ACTIVE":
                                logger.info(f"Ghost Intelligence Alert: Breaking News detected in {category}! Price swing of {price_swing:.2%}")

                except Exception as e:
                    logger.debug(f"Error processing market: {e}") # Use debug to keep logs clean
                    continue

            await trader.check_resolutions(scanner)

            # Periodic Status Logging: Every 5 minutes
            scan_time = time.time() - start_time
            if time.time() - last_status_log > 300:
                logger.info(f"[STATUS] Monitoring {len(active_markets)} Active Markets | Tracked {len(whale_tracker.top_whales)} Elite Wallets | Last Scan Time: {scan_time:.2f}s")
                last_status_log = time.time()

            # Faster discovery interval: 5-10 seconds with jitter
            jitter = random.uniform(5, 10)
            await asyncio.sleep(jitter)
    except asyncio.CancelledError:
        logger.info("Discovery loop task cancelled.")
    except Exception as e:
        logger.error(f"Critical error in discovery loop: {e}", exc_info=True)

async def run_whale_tracker_loop(whale_tracker, copy_trader):
    """
    Ghost Intelligence v3.0 logic: monitor top whales and copy trades.
    """
    try:
        logger.info("Ghost Intelligence v3.0: Whale Tracker active.")
        async for whale_action in whale_tracker.monitor_whale_activity():
            if not isinstance(whale_action, dict):
                logger.warning("WhaleTracker: Invalid action data.")
                continue
            await copy_trader.evaluate_signal(whale_action)

    except asyncio.CancelledError:
        logger.info("Whale tracker loop task cancelled.")
    except Exception as e:
        logger.error(f"Critical error in whale tracker loop: {e}", exc_info=True)

async def main():
    logger.info("Starting Ghost Intelligence v3.0 - The Final Deployment...")

    # Initialize components
    scanner = MarketScanner()
    db = Database("data/ghost_trader.db")
    await db.connect()

    explorer = MarketExplorer(scanner.polymarket)
    brain = Brain(model="claude-3-5-sonnet")
    trader = PaperTrader(db)

    whale_tracker = WhaleTracker(scanner.polymarket)
    copy_trader = CopyTrader(trader, scanner)

    # Concurrency
    tasks = [
        run_discovery_loop(explorer, scanner, brain, trader, whale_tracker),
        run_whale_tracker_loop(whale_tracker, copy_trader)
    ]

    try:
        await asyncio.gather(*tasks)
    except Exception as e:
        logger.error(f"Critical failure in bot core: {e}", exc_info=True)
    finally:
        await scanner.close()
        await db.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
