import logging
import os


logger = logging.getLogger(__name__)

DUMMY_PRIVATE_KEY = "0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


def resolve_polygon_private_key() -> str:
    candidate = os.getenv("POLYGON_PRIVATE_KEY", "").strip()
    if candidate.startswith("0x") and len(candidate) == 66:
        return candidate

    if candidate:
        logger.info("Invalid POLYGON_PRIVATE_KEY detected. Falling back to deterministic dummy key for PAPER mode.")
    return DUMMY_PRIVATE_KEY
