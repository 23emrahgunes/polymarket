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
from src.trading import TradeExecutor
from src.parser import parse_polymarket_question
from src.analytics import log_bot_performance
import pandas as pd
from datetime import datetime, timezone

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Mute noisy logs
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("py_clob_client").setLevel(logging.WARNING)
logging.getLogger("ccxt").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

# Shared State
ACTIVE_MARKET_CONTEXT = {}
CLOB_TOKEN_TO_MARKET_ID = {}

# Exchange-Specific Symbol Mappings
EXCHANGE_MAPPINGS = {
    "binance": {
        "BTC": "BTC/USDT",
        "ETH": "ETH/USDT",
        "SOL": "SOL/USDT",
        "XRP": "XRP/USDT",
        "DOGE": "DOGE/USDT",
        "BNB": "BNB/USDT"
    },
    "coinbase": {
        "BTC": "BTC/USD",
        "ETH": "ETH/USD",
        "SOL": "SOL/USD",
        "XRP": "XRP/USD",
        "DOGE": "DOGE/USD",
        "BNB": "BNB/USD" # Placeholder if supported
    }
}

NEWS_SWING_THRESHOLD = 0.05
PRICE_HISTORY = {}

async def run_discovery_loop(explorer, scanner, brain, trader, whale_tracker, db, exchange_id):
    last_status_log = time.time()
    last_market_scan_log = time.time()
    last_re_rank = time.time()

    crypto_map = EXCHANGE_MAPPINGS.get(exchange_id, EXCHANGE_MAPPINGS["binance"])

    try:
        while True:
            start_time = time.time()
            logger.debug("Ghost Intelligence: Discovering markets...")

            active_markets = await explorer.fetch_active_markets(limit=200)

            global ACTIVE_MARKET_CONTEXT, CLOB_TOKEN_TO_MARKET_ID
            ACTIVE_MARKET_CONTEXT = {m.get("market_id"): m for m in active_markets}

            CLOB_TOKEN_TO_MARKET_ID = {}
            for m in active_markets:
                mid = m.get("market_id")
                tid = m.get("token_id")
                if tid: CLOB_TOKEN_TO_MARKET_ID[tid] = mid
                for tid in m.get("token_ids", []):
                    CLOB_TOKEN_TO_MARKET_ID[tid] = mid

            # Update monitored symbols with exchange-compatible ones
            monitored_symbols = set()
            for m in active_markets:
                if m.get("category") == "CRYPTO":
                    question = m.get("question", "").upper()
                    for base, symbol in crypto_map.items():
                        if base in question:
                            monitored_symbols.add(symbol)

            if monitored_symbols:
                await scanner.update_monitored_symbols(list(monitored_symbols))

            for market in active_markets:
                try:
                    category = market.get("category", "OTHER")
                    question = market.get("question", "")
                    market_id = market.get("market_id")
                    volume_24h = float(market.get("volume_24h", 0))
                    token_id = market.get("token_id")

                    if not token_id: continue
                    current_poly_price = await scanner.get_token_price(token_id)
                    if not current_poly_price: continue

                    if category == "CRYPTO":
                        for base, symbol in crypto_map.items():
                            if base in question.upper():
                                current_exchange_price = scanner.current_prices.get(symbol)

                                # Robust Price Fallback Check
                                if not current_exchange_price:
                                    logger.debug(f"Skipping {symbol}: Price unavailable from {exchange_id}")
                                    continue

                                df = await scanner.get_historical_data(symbol)
                                volatility = calculate_annualized_volatility(df['close'])
                                strike_price, expiry_dt = parse_polymarket_question(question)
                                if not strike_price or not expiry_dt: continue

                                now = datetime.now(timezone.utc)
                                time_to_expiry_years = (expiry_dt - now).total_seconds() / (24 * 365 * 3600)
                                if time_to_expiry_years <= 0: continue

                                implied_prob = calculate_black_scholes_prob(current_exchange_price, strike_price, time_to_expiry_years, volatility)
                                edge = calculate_edge(current_poly_price, implied_prob)

                                debug_mode = os.getenv("DEBUG_SIGNAL_MODE", "false").lower() == "true"
                                if edge > 0.05 or debug_mode:
                                    rsi = calculate_rsi(df['close']).iloc[-1]
                                    confidence = await brain.get_confidence(edge, rsi, volume_24h, 0.0)
                                    logger.info(f"[!!! SIGNAL !!!] [CRYPTO] [{question[:30]}] | Price: ${current_poly_price:.2f} | Edge: {edge:.2%} | Confidence: {confidence:.2f}")
                                    if confidence > 0.7 or debug_mode:
                                        if debug_mode: logger.info("[DEBUG_SIGNAL_MODE] Bypassing confidence filter.")
                                        await trader.execute_trade(market_id, "YES", 50.0, current_poly_price, edge, confidence)
                                    else:
                                        logger.info(f"[REJECT] [CRYPTO] [{question[:30]}]: Low confidence ({confidence:.2f} < 0.7)")
                                else:
                                    logger.info(f"[REJECT] [CRYPTO] [{question[:30]}]: Edge too low ({edge:.2%} < 5%)")
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
                await log_bot_performance(db)
                last_status_log = time.time()
            if time.time() - last_re_rank > 86400:
                await whale_tracker.re_rank_whales(limit=20)
                last_re_rank = time.time()

            if time.time() - last_market_scan_log > 60:
                top_3 = sorted(active_markets, key=lambda x: x.get("volume_24h", 0), reverse=True)[:3]
                market_names = [f"{m.get('question')[:20]}... (${m.get('volume_24h', 0)/1000:.1f}k)" for m in top_3]
                logger.info(f"[MARKET-SCAN] Active: {len(active_markets)} | Top 3: {', '.join(market_names)}")
                last_market_scan_log = time.time()

            await asyncio.sleep(random.uniform(5, 10))
    except Exception as e:
        logger.error(f"Critical error in discovery loop: {e}", exc_info=True)

async def run_activity_hunter_loop(hunter, copy_trader):
    try:
        logger.info("Ghost Intelligence v4.0: Activity Hunter active.")
        async for event in hunter.monitor_stream():
            if not isinstance(event, dict): continue
            token_id = event.get("market_id")
            market_id = CLOB_TOKEN_TO_MARKET_ID.get(token_id)
            if market_id and market_id in ACTIVE_MARKET_CONTEXT:
                market_data = ACTIVE_MARKET_CONTEXT[market_id]
                amount = event.get("amount", 0)
                logger.info(f"[DETECTED] Trade of ${amount:,.2f} on {market_data.get('question')[:40]}...")
                event["market_id"] = market_id
                event["token_id"] = market_data.get("token_id")
                event["category"] = market_data.get("category")
                await copy_trader.evaluate_activity_event(event)
            else:
                logger.debug(f"Skipped Trade | Reason: Market Not Monitored | TokenID: {token_id}")
                if event.get("type") == "WHALE_EVENT":
                    await copy_trader.evaluate_activity_event(event)
    except Exception as e:
        logger.error(f"Critical error in activity hunter loop: {e}", exc_info=True)

async def run_whale_tracker_loop(whale_tracker, copy_trader):
    try:
        logger.info("Ghost Intelligence v3.0: Whale Tracker active.")
        async for whale_action in whale_tracker.monitor_whale_activity():
            if not isinstance(whale_action, dict): continue
            mid = whale_action.get("market_id")
            if mid in ACTIVE_MARKET_CONTEXT:
                whale_action["token_id"] = ACTIVE_MARKET_CONTEXT[mid].get("token_id")
            await copy_trader.evaluate_signal(whale_action)
    except Exception as e:
        logger.error(f"Critical error in whale tracker loop: {e}", exc_info=True)

async def main():
    logger.info("Starting Ghost Intelligence v4.0 - Synchronized Core Deployment...")

    # Select exchange based on environment or availability
    exchange_id = 'coinbase' # Defaulting to coinbase due to regional restrictions in dev environment
    logger.info(f"Initializing scanner with {exchange_id.upper()}...")

    # Startup Log: show active exchange and its symbols
    active_symbols = list(EXCHANGE_MAPPINGS.get(exchange_id, {}).values())
    logger.info(f"Monitored Exchange: {exchange_id.upper()} | Primary Symbols: {active_symbols}")

    scanner = MarketScanner(exchange_id=exchange_id)
    db = Database("data/ghost_trader.db")
    await db.connect()
    explorer = MarketExplorer(scanner.polymarket)
    brain = Brain(model="claude-3-5-sonnet")
    trader = TradeExecutor(db)

    whale_tracker = WhaleTracker(scanner.polymarket, db=db)
    activity_hunter = ActivityHunter()
    copy_trader = CopyTrader(trader, scanner)

    tasks = [
        asyncio.create_task(run_discovery_loop(explorer, scanner, brain, trader, whale_tracker, db, exchange_id)),
        asyncio.create_task(run_whale_tracker_loop(whale_tracker, copy_trader)),
        asyncio.create_task(run_activity_hunter_loop(activity_hunter, copy_trader))
    ]
    try:
        await asyncio.gather(*tasks)
    except Exception as e:
        logger.error(f"Critical failure in bot core: {e}", exc_info=True)
    finally:
        for t in tasks: t.cancel()
        await scanner.close()
        await db.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
