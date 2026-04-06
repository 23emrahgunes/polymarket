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
from src.scrapers.activity import ActivityHunter
from src.copy_trader import CopyTrader
from src.database import Database
from src.logic import calculate_black_scholes_prob, calculate_edge, calculate_annualized_volatility, calculate_rsi
from src.brain import Brain
from src.trading import PaperTrader
from src.parser import parse_polymarket_question
from src.analytics import log_bot_performance
import pandas as pd
from datetime import datetime, timezone

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Mute noisy HTTP and SDK logs in production
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("py_clob_client").setLevel(logging.WARNING)
logging.getLogger("ccxt").setLevel(logging.WARNING)

# Shared state for discovery
ACTIVE_MARKET_CONTEXT = {} # {market_id: market_data}
TOKEN_TO_MARKET_MAP = {}   # {token_id: market_id}

# Mapping logic remains standard
CRYPTO_MAPPING = {
    "BTC": "BTC/USDT",
    "ETH": "ETH/USDT",
    "SOL": "SOL/USDT",
    "XRP": "XRP/USDT",
    "DOGE": "DOGE/USDT",
    "BNB": "BNB/USDT"
}

NEWS_SWING_THRESHOLD = 0.05
PRICE_HISTORY = {}

async def run_discovery_loop(explorer, scanner, brain, trader, whale_tracker, db):
    last_status_log = time.time()
    last_re_rank = time.time()
    try:
        while True:
            start_time = time.time()
            logger.debug("Ghost Intelligence: Discovering markets...")

            # Fetch top 200 high-volume active markets
            active_markets = await explorer.fetch_active_markets(limit=200)

            # Update Global Market Context for Activity Sync
            global ACTIVE_MARKET_CONTEXT, TOKEN_TO_MARKET_MAP
            ACTIVE_MARKET_CONTEXT = {m.get("market_id"): m for m in active_markets}

            # Map every token_id to its parent market_id for reliable activity matching
            TOKEN_TO_MARKET_MAP = {}
            for m in active_markets:
                for tid in m.get("token_ids", []):
                    TOKEN_TO_MARKET_MAP[tid] = m.get("market_id")

            # 1. Update monitored symbols for crypto
            crypto_symbols = set()
            for market in active_markets:
                if market.get("category") == "CRYPTO":
                    question = market.get("question", "").upper()
                    for base, pair in CRYPTO_MAPPING.items():
                        if base in question:
                            crypto_symbols.add(pair)
            await scanner.update_monitored_symbols(list(crypto_symbols))

            # 2. Main Processing Loop
            for market in active_markets:
                try:
                    category = market.get("category", "OTHER")
                    question = market.get("question", "")
                    market_id = market.get("market_id")
                    volume_24h = float(market.get("volume_24h", 0))
                    tokens = market.get("tokens", [])
                    if not tokens: continue
                    token_id = tokens[0].get("token_id")

                    current_poly_price = await scanner.get_token_price(token_id)
                    if not current_poly_price: continue

                    if category == "CRYPTO":
                        for base, symbol in CRYPTO_MAPPING.items():
                            if base in question.upper():
                                current_binance_price = scanner.current_prices.get(symbol)
                                if not current_binance_price: continue

                                df = await scanner.get_historical_data(symbol)
                                volatility = calculate_annualized_volatility(df['close'])
                                strike_price, expiry_dt = parse_polymarket_question(question)
                                if not strike_price or not expiry_dt: continue

                                now = datetime.now(timezone.utc)
                                time_to_expiry_years = (expiry_dt - now).total_seconds() / (24 * 365 * 3600)
                                if time_to_expiry_years <= 0: continue

                                implied_prob = calculate_black_scholes_prob(current_binance_price, strike_price, time_to_expiry_years, volatility)
                                edge = calculate_edge(current_poly_price, implied_prob)

                                if edge > 0.05:
                                    rsi = calculate_rsi(df['close']).iloc[-1]
                                    confidence = await brain.get_confidence(edge, rsi, volume_24h, 0.0)
                                    logger.info(f"[!!! SIGNAL !!!] [CRYPTO] [{question[:30]}] | Price: ${current_poly_price:.2f} | Edge: {edge:.2%} | Confidence: {confidence:.2f}")
                                    if confidence > 0.7:
                                        await trader.execute_trade(market_id, "YES", 50.0, current_poly_price, edge, confidence)
                                else:
                                    logger.debug(f"[CRYPTO] [{question[:30]}] | Price: ${current_poly_price:.2f} | Edge: {edge:.2%} | SIGNAL: IDLE")
                    else:
                        if market_id not in PRICE_HISTORY:
                            PRICE_HISTORY[market_id] = []
                        PRICE_HISTORY[market_id].append((time.time(), current_poly_price))
                        PRICE_HISTORY[market_id] = [(t, p) for t, p in PRICE_HISTORY[market_id] if time.time() - t < 900]
                        if len(PRICE_HISTORY[market_id]) > 2:
                            price_swing = (PRICE_HISTORY[market_id][-1][1] - PRICE_HISTORY[market_id][0][1]) / PRICE_HISTORY[market_id][0][1]
                            if abs(price_swing) > NEWS_SWING_THRESHOLD:
                                logger.info(f"[!!! SIGNAL !!!] [{category}] [{question[:30]}] | Breaking News! Price Swing: {price_swing:.2%}")
                            else:
                                logger.debug(f"[{category}] [{question[:30]}] | Price: ${current_poly_price:.2f} | Swing: {price_swing:.2%} | SIGNAL: IDLE")
                except Exception as e:
                    logger.debug(f"Error processing market: {e}")
                    continue

            await trader.check_resolutions(scanner)

            if time.time() - last_status_log > 300:
                total, wins, win_rate, total_pnl = await db.get_bot_performance()
                scan_time = time.time() - start_time
                logger.info(f"[STATUS] Monitoring {len(active_markets)} Active Markets | Tracked {len(whale_tracker.top_whales)} Elite Wallets | Win-Rate: {win_rate:.1f}%")
                last_status_log = time.time()
            if time.time() - last_re_rank > 86400:
                await whale_tracker.re_rank_whales(limit=20)
                last_re_rank = time.time()

            # Discovery cycle: 5-10 seconds
            await asyncio.sleep(random.uniform(5, 10))
    except asyncio.CancelledError:
        logger.info("Discovery loop cancelled.")
    except Exception as e:
        logger.error(f"Discovery loop error: {e}", exc_info=True)

async def run_activity_hunter_loop(hunter, copy_trader):
    try:
        logger.info("Ghost Intelligence v4.0: Activity Hunter active.")
        async for event in hunter.monitor_stream():
            if not isinstance(event, dict): continue

            # Reliability Fix: Resolve token_id from activity to market_id
            token_id_from_activity = event.get("market_id") # Gamma API calls it market_id in activity
            market_id = TOKEN_TO_MARKET_MAP.get(token_id_from_activity, token_id_from_activity)

            # Sync ActivityHunter with Global Market Context
            if market_id in ACTIVE_MARKET_CONTEXT:
                market_data = ACTIVE_MARKET_CONTEXT[market_id]
                event["market_id"] = market_id
                event["token_id"] = market_data.get("token_id") # Critical Fix: Pass clobTokenId
                event["category"] = market_data.get("category")
                await copy_trader.evaluate_activity_event(event)
            else:
                # Forced Debug: Skip and Log
                logger.debug(f"Skipped Trade | Reason: Market Not Monitored | ID: {token_id_from_activity}")

                # Still evaluate if high volume event
                if event.get("type") == "WHALE_EVENT":
                    await copy_trader.evaluate_activity_event(event)
    except asyncio.CancelledError:
        logger.info("Activity Hunter loop cancelled.")
    except Exception as e:
        logger.error(f"Activity Hunter loop error: {e}")

async def run_whale_tracker_loop(whale_tracker, copy_trader):
    try:
        logger.info("Ghost Intelligence v3.0: Whale Tracker active.")
        async for whale_action in whale_tracker.monitor_whale_activity():
            if not isinstance(whale_action, dict): continue

            # Sync WhaleTracker with Global Market Context
            mid = whale_action.get("market_id")
            if mid in ACTIVE_MARKET_CONTEXT:
                whale_action["token_id"] = ACTIVE_MARKET_CONTEXT[mid].get("token_id")

            await copy_trader.evaluate_signal(whale_action)
    except asyncio.CancelledError:
        logger.info("Whale tracker loop cancelled.")
    except Exception as e:
        logger.error(f"Whale tracker loop error: {e}")

async def main():
    logger.info("Starting Ghost Intelligence v4.0 - The Activity Hunter...")
    scanner = MarketScanner()
    db = Database("data/ghost_trader.db")
    await db.connect()
    explorer = MarketExplorer(scanner.polymarket)
    brain = Brain(model="claude-3-5-sonnet")
    trader = PaperTrader(db)
    whale_tracker = WhaleTracker(scanner.polymarket, db=db)
    activity_hunter = ActivityHunter()
    copy_trader = CopyTrader(trader, scanner)

    tasks = [
        asyncio.create_task(run_discovery_loop(explorer, scanner, brain, trader, whale_tracker, db)),
        asyncio.create_task(run_whale_tracker_loop(whale_tracker, copy_trader)),
        asyncio.create_task(run_activity_hunter_loop(activity_hunter, copy_trader))
    ]
    try:
        await asyncio.gather(*tasks)
    except Exception as e:
        logger.error(f"Critical failure: {e}", exc_info=True)
    finally:
        for t in tasks: t.cancel()
        await scanner.close()
        await db.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
