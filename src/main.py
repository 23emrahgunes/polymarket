import asyncio
import logging

from src.market_config import EXCHANGE_MAPPINGS
from src.runtime import GhostBotRuntime, RuntimeSettings


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("py_clob_client").setLevel(logging.WARNING)
logging.getLogger("ccxt").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)


async def main():
    settings = RuntimeSettings.from_env()
    logger.info("Starting Ghost Intelligence v4.0 - all-category PAPER runtime")
    logger.info("Mode: PAPER | exchange=%s | debug_signal_mode=%s | db_path=%s", settings.exchange_id.upper(), settings.debug_signal_mode, settings.db_path)
    logger.info("Exchange symbol map: %s", EXCHANGE_MAPPINGS.get(settings.exchange_id, EXCHANGE_MAPPINGS["coinbase"]))
    logger.info("Venue configs: %s", {venue: config for venue, config in settings.venue_configs.items()})

    runtime = GhostBotRuntime(settings)
    await runtime.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
