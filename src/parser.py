import re
from datetime import datetime, timezone

def parse_polymarket_question(question):
    """
    Parses a Polymarket question to extract the strike price and expiry date.
    Example: "Will Bitcoin be above $70,000 at March 31, 2024?"
    Returns: (strike_price, expiry_datetime_utc)
    """
    # Regex for strike price (e.g., $70,000 or 70000 or 150.50)
    strike_match = re.search(r'\$\s*([\d,]+(?:\.\d+)?)', question)
    if not strike_match:
        strike_match = re.search(r'above\s*([\d,]+(?:\.\d+)?)', question, re.IGNORECASE)

    if not strike_match:
        strike_match = re.search(r'([\d,]+(?:\.\d+)?)', question)

    if not strike_match:
        return None, None

    strike_str = strike_match.group(1).replace(',', '')
    try:
        strike_price = float(strike_str)
    except ValueError:
        return None, None

    # Expiry parsing
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
              "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]

    month_pattern = '|'.join(months)
    expiry_match = re.search(rf'({month_pattern})\s+(\d{{1,2}}),\s+(\d{{4}})', question, re.IGNORECASE)

    if not expiry_match:
        return strike_price, None

    month_str = expiry_match.group(1)
    day = int(expiry_match.group(2))
    year = int(expiry_match.group(3))

    month_map = {m.lower()[:3]: i+1 for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}
    month = month_map.get(month_str.lower()[:3])

    if not month:
        return strike_price, None

    try:
        # Use timezone-aware UTC datetime
        expiry_dt = datetime(year, month, day, 23, 59, 59, tzinfo=timezone.utc)
        return strike_price, expiry_dt
    except ValueError:
        return strike_price, None
