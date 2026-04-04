import asyncio
import logging
import signal
from src.scanner import MarketScanner
from src.database import Database
from src.logic import calculate_black_scholes_prob, calculate_edge, calculate_annualized_volatility, calculate_rsi
from src.brain import Brain
from src.trading import PaperTrader
import pandas as pd
import numpy as np

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def run_bot():
    logger.info("Starting Ghost Trader v1.0...")

    # Initialize components
    scanner = MarketScanner(binance_symbol="BTC/USDT")
    db = Database("data/ghost_trader.db")
    await db.connect()

    brain = Brain(model="claude-3-5-sonnet")
    trader = PaperTrader(db)

    # Start WebSocket ticker task
    ticker_task = asyncio.create_task(scanner.watch_ticker())

    try:
        while True:
            # 1. Fetch historical data for volatility and RSI
            logger.info("Updating historical data...")
            df = await scanner.get_historical_data("BTC/USDT", timeframe='1m', limit=1440)

            volatility = calculate_annualized_volatility(df['close'], sampling_period_minutes=1)
            rsi = calculate_rsi(df['close']).iloc[-1]
            volume_24h = df['volume'].sum()
            price_delta_5m = (df['close'].iloc[-1] - df['close'].iloc[-5]) / df['close'].iloc[-5]

            # Wait for WS to get the current price
            while scanner.current_price is None:
                await asyncio.sleep(1)

            current_price = scanner.current_price

            # 2. Polymarket Data - Search for BTC-related markets
            markets = await scanner.get_polymarket_btc_markets()

            # For each market, calculate edge
            for market in markets:
                # Basic market data
                # For v1.0, we'll extract the strike price and expiry from the question string
                # Question: "Will Bitcoin be above $70,000 at March 31, 2024?"
                # (Actual parsing requires a more robust regex; for v1.0, we'll demonstrate on the first found market)

                # Mocking a market strike price and expiry based on the question
                # In production, we'd use market['tags'] or more robust parsing
                strike_price = current_price + 500
                expiry_time_hours = 4

                # Get actual token price
                token_id = market.get("tokens", [{}])[0].get("token_id")
                if not token_id: continue

                polymarket_yes_price = await scanner.get_token_price(token_id)
                if not polymarket_yes_price: continue

                # 3. Logic - Black-Scholes for Implied Probability
                T = expiry_time_hours / (24 * 365) # T in years
                implied_prob = calculate_black_scholes_prob(current_price, strike_price, T, volatility)

                edge = calculate_edge(polymarket_yes_price, implied_prob)

                logger.info(f"Binance Price: ${current_price:.2f} | Polymarket Yes Price: ${polymarket_yes_price:.2f}")
                logger.info(f"Implied Prob: {implied_prob:.2%} | Edge: {edge:.2%}")

                # 4. Signal and Brain Check
                if edge > 0.05:
                    confidence = await brain.get_confidence(edge, rsi, volume_24h, price_delta_5m)
                    logger.info(f"Edge detected! Claude Confidence: {confidence:.2f}")

                    if confidence > 0.7:
                        # Execute Paper Trade
                        trade_size = 50.0 # Fixed Fractional $50
                        success, msg = await trader.execute_trade(market.get("market_id"), "YES", trade_size, polymarket_yes_price, edge, confidence)
                        logger.info(msg)

                # For demo purposes, we only check the first market
                break

            # 5. Check for resolutions
            await trader.check_resolutions(scanner)

            # Wait before next scan
            await asyncio.sleep(10)

    except asyncio.CancelledError:
        logger.info("Bot task cancelled.")
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
    except Exception as e:
        logger.error(f"Critical error in main loop: {e}", exc_info=True)
    finally:
        ticker_task.cancel()
        await scanner.close()
        await db.close()

if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass
