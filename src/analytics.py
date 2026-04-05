import logging

logger = logging.getLogger(__name__)

async def log_bot_performance(db):
    """
    Logs the current bot performance metrics.
    [BOT_PERFORMANCE] Total Trades: X | Wins: Y | Win-Rate: Z% | Total Profit: $W.
    """
    total, wins, win_rate, total_pnl = await db.get_bot_performance()
    logger.info(f"[BOT_PERFORMANCE] Total Trades: {total} | Wins: {wins} | Win-Rate: {win_rate:.2f}% | Total Profit: ${total_pnl:.2f}")
    return win_rate

async def log_whale_score(db, address):
    """
    Logs the performance score for a specific whale.
    [WHALE_SCORE] Wallet 0x... | Accuracy: Z% (Success: X/Total: Y).
    """
    stats = await db.get_whale_stats(address)
    if stats:
        total = stats['total_trades']
        wins = stats['wins']
        accuracy = (wins / total * 100) if total > 0 else 0.0
        logger.info(f"[WHALE_SCORE] Wallet {address[:10]}... | Accuracy: {accuracy:.2f}% (Success: {wins}/Total: {total})")
        return accuracy
    return 0.0
