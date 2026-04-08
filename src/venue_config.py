from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Literal


VenueId = Literal["polymarket", "binance_futures", "binance_spot"]
ExecutionMode = Literal["paper", "live"]
InstrumentType = Literal["prediction", "futures", "spot"]


def _env_flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return float(default)
    try:
        return float(raw)
    except ValueError:
        return float(default)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return int(default)
    try:
        return int(raw)
    except ValueError:
        return int(default)


@dataclass(frozen=True)
class VenueConfig:
    venue_id: VenueId
    enabled: bool
    mode: ExecutionMode
    instrument_type: InstrumentType
    max_order_usd: float
    max_position_usd: float
    max_daily_loss_usd: float
    max_open_positions: int
    fee_bps: float
    slippage_limit_bps: float
    signal_threshold: float
    leverage: int = 1
    margin_mode: str = "isolated"
    stop_loss_pct: float = 0.03
    take_profit_pct: float = 0.06


def build_default_venue_configs() -> Dict[VenueId, VenueConfig]:
    return {
        "polymarket": VenueConfig(
            venue_id="polymarket",
            enabled=_env_flag("POLYMARKET_ENABLED", True),
            mode="paper",
            instrument_type="prediction",
            max_order_usd=_env_float("POLYMARKET_MAX_ORDER_USD", 50.0),
            max_position_usd=_env_float("POLYMARKET_MAX_POSITION_USD", 100.0),
            max_daily_loss_usd=_env_float("POLYMARKET_MAX_DAILY_LOSS_USD", 150.0),
            max_open_positions=_env_int("POLYMARKET_MAX_OPEN_POSITIONS", 6),
            fee_bps=_env_float("POLYMARKET_FEE_BPS", 0.0),
            slippage_limit_bps=_env_float("POLYMARKET_SLIPPAGE_LIMIT_BPS", 400.0),
            signal_threshold=_env_float("POLYMARKET_SIGNAL_THRESHOLD", 0.68),
            leverage=1,
            margin_mode="isolated",
            stop_loss_pct=0.0,
            take_profit_pct=0.0,
        ),
        "binance_futures": VenueConfig(
            venue_id="binance_futures",
            enabled=_env_flag("BINANCE_FUTURES_ENABLED", False),
            mode=os.getenv("BINANCE_FUTURES_MODE", "paper").strip().lower() or "paper",
            instrument_type="futures",
            max_order_usd=_env_float("BINANCE_FUTURES_MAX_ORDER_USD", 100.0),
            max_position_usd=_env_float("BINANCE_FUTURES_MAX_POSITION_USD", 250.0),
            max_daily_loss_usd=_env_float("BINANCE_FUTURES_MAX_DAILY_LOSS_USD", 150.0),
            max_open_positions=_env_int("BINANCE_FUTURES_MAX_OPEN_POSITIONS", 3),
            fee_bps=_env_float("BINANCE_FUTURES_FEE_BPS", 4.0),
            slippage_limit_bps=_env_float("BINANCE_FUTURES_SLIPPAGE_LIMIT_BPS", 35.0),
            signal_threshold=_env_float("BINANCE_FUTURES_SIGNAL_THRESHOLD", 0.70),
            leverage=_env_int("BINANCE_FUTURES_LEVERAGE", 2),
            margin_mode=os.getenv("BINANCE_FUTURES_MARGIN_MODE", "isolated").strip().lower() or "isolated",
            stop_loss_pct=_env_float("BINANCE_FUTURES_STOP_LOSS_PCT", 0.03),
            take_profit_pct=_env_float("BINANCE_FUTURES_TAKE_PROFIT_PCT", 0.06),
        ),
        "binance_spot": VenueConfig(
            venue_id="binance_spot",
            enabled=_env_flag("BINANCE_SPOT_ENABLED", False),
            mode=os.getenv("BINANCE_SPOT_MODE", "paper").strip().lower() or "paper",
            instrument_type="spot",
            max_order_usd=_env_float("BINANCE_SPOT_MAX_ORDER_USD", 100.0),
            max_position_usd=_env_float("BINANCE_SPOT_MAX_POSITION_USD", 200.0),
            max_daily_loss_usd=_env_float("BINANCE_SPOT_MAX_DAILY_LOSS_USD", 100.0),
            max_open_positions=_env_int("BINANCE_SPOT_MAX_OPEN_POSITIONS", 3),
            fee_bps=_env_float("BINANCE_SPOT_FEE_BPS", 10.0),
            slippage_limit_bps=_env_float("BINANCE_SPOT_SLIPPAGE_LIMIT_BPS", 20.0),
            signal_threshold=_env_float("BINANCE_SPOT_SIGNAL_THRESHOLD", 0.72),
            leverage=1,
            margin_mode="isolated",
            stop_loss_pct=_env_float("BINANCE_SPOT_STOP_LOSS_PCT", 0.03),
            take_profit_pct=_env_float("BINANCE_SPOT_TAKE_PROFIT_PCT", 0.06),
        ),
    }
