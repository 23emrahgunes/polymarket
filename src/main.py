import asyncio
import logging
from src.scanner import MarketScanner
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

# Multi-Pair Support Architecture
WATCH_LIST = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT", "BNB/USDT"]

async def run_bot():
    logger.info("Starting Ghost Trader v1.0 - Multi-Crypto Monitoring Mode...")

    # Initialize components
    scanner = MarketScanner()
    db = Database("data/ghost_trader.db")
    await db.connect()

    brain = Brain(model="claude-3-5-sonnet")
    trader = PaperTrader(db)

    # Start WebSocket ticker task for all symbols
    ticker_task = asyncio.create_task(scanner.watch_tickers(WATCH_LIST))

    try:
        while True:
            # Iterative Scanning: Scan through each pair in WATCH_LIST
            for symbol in WATCH_LIST:
                try:
                    # Ticker-specific logging
                    logger.info(f"[{symbol}] Scanning...")

                    # 1. Fetch historical data for volatility and RSI
                    # Ensure parameters match scanner.py: (symbol, timeframe, limit)
                    df = await scanner.get_historical_data(symbol, timeframe='1m', limit=1440)

                    volatility = calculate_annualized_volatility(df['close'], sampling_period_minutes=1)
                    rsi = calculate_rsi(df['close']).iloc[-1]
                    volume_24h = df['volume'].sum()
                    price_delta_5m = (df['close'].iloc[-1] - df['close'].iloc[-5]) / df['close'].iloc[-5]

                    # Wait for WS to get the current price for this symbol
                    current_price = scanner.current_prices.get(symbol)
                    if current_price is None:
                        logger.warning(f"[{symbol}] Waiting for real-time price from WebSocket...")
                        continue

                    # 2. Polymarket Data - Search for related markets
                    base_currency = symbol.split('/')[0]
                    markets = await scanner.get_polymarket_markets_for_symbol(base_currency)

                    # For each market, calculate edge
                    for market in markets:
                        # Before parsing, check type safety
                        if not isinstance(market, dict):
                            continue

                        question = market.get("question")
                        if not question: continue

                        # Extract strike and expiry
                        strike_price, expiry_dt = parse_polymarket_question(question)
                        if not strike_price or not expiry_dt: continue

                        # Check expiry timeframe (< 24h)
                        now = datetime.now(timezone.utc)
                        time_to_expiry_seconds = (expiry_dt - now).total_seconds()
                        if time_to_expiry_seconds <= 0 or time_to_expiry_seconds > 86400:
                            continue # Ignore if already expired or too far out

                        # Get actual token price
                        tokens = market.get("tokens", [])
                        if not tokens or not isinstance(tokens, list): continue

                        token_id = tokens[0].get("token_id")
                        if not token_id: continue

                        polymarket_yes_price = await scanner.get_token_price(token_id)
                        if not polymarket_yes_price: continue

                        # 3. Logic - Black-Scholes for Implied Probability
                        T = time_to_expiry_seconds / (24 * 365 * 3600) # T in years
                        implied_prob = calculate_black_scholes_prob(current_price, strike_price, T, volatility)

                        edge = calculate_edge(polymarket_yes_price, implied_prob)

                        logger.info(f"[{symbol}] Market: {question}")
                        logger.info(f"[{symbol}] Binance Price: ${current_price:.2f} | Strike: ${strike_price:.2f}")
                        logger.info(f"[{symbol}] Implied Prob: {implied_prob:.2%} | Polymarket YES: ${polymarket_yes_price:.2f} | Edge: {edge:.2%}")

                        # 4. Signal and Brain Check
                        if edge > 0.05:
                            confidence = await brain.get_confidence(edge, rsi, volume_24h, price_delta_5m)
                            logger.info(f"[{symbol}] Edge detected! Claude Confidence: {confidence:.2f}")

                            if confidence > 0.7:
                                # Execute Paper Trade
                                trade_size = 50.0 # Fixed Fractional $50
                                success, msg = await trader.execute_trade(market.get("market_id"), "YES", trade_size, polymarket_yes_price, edge, confidence)
                                logger.info(f"[{symbol}] {msg}")

                except Exception as e:
                    # Global Try-Except per Pair: Ensure one pair failure doesn't stop the bot
                    logger.error(f"[{symbol}] Critical error in scanning cycle: {e}", exc_info=True)
                    continue

            # 5. Check for resolutions (Once per global loop)
            await trader.check_resolutions(scanner)

            # Wait before next scan (15 seconds to be efficient but responsive)
            await asyncio.sleep(15)

    except asyncio.CancelledError:
        logger.info("Bot task cancelled.")
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
    except Exception as e:
        logger.error(f"Critical error in main global loop: {e}", exc_info=True)
    finally:
        ticker_task.cancel()
        await scanner.close()
        await db.close()

if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass
