from __future__ import annotations

from pathlib import Path
from typing import Optional


SIGNAL_FAMILY_DISCOVERY = {
    "discovery",
    "blended_crypto",
    "binance_futures_price_structure",
    "binance_spot_price_structure",
}
SIGNAL_FAMILY_WHALE = {"whale_tracker"}
SIGNAL_FAMILY_ACTIVITY = {"activity", "cluster_detected"}

SAMPLE_KIND_LIVE_PAPER = "live_paper"
SAMPLE_KIND_SYNTHETIC_VERIFY = "synthetic_verify"
SAMPLE_KIND_REPLAY = "replay"
STRATEGY_PROFILE_BASELINE = "baseline"
STRATEGY_PROFILE_SAMPLING_RELAXED = "sampling_relaxed"


def normalize_signal_family(raw_source_signal: Optional[str]) -> str:
    normalized = (raw_source_signal or "").strip().lower()
    if normalized in SIGNAL_FAMILY_DISCOVERY:
        return "discovery"
    if normalized in SIGNAL_FAMILY_WHALE:
        return "whale"
    if normalized in SIGNAL_FAMILY_ACTIVITY:
        return "activity_orderflow"
    return "unknown"


def infer_sample_kind(
    debug_signal_mode: bool = False,
    explicit_sample_kind: Optional[str] = None,
    debug_profile: Optional[str] = None,
) -> str:
    if explicit_sample_kind:
        return explicit_sample_kind
    if debug_signal_mode or debug_profile:
        return SAMPLE_KIND_SYNTHETIC_VERIFY
    return SAMPLE_KIND_LIVE_PAPER


def is_synthetic_sample(sample_kind: Optional[str]) -> bool:
    return (sample_kind or SAMPLE_KIND_LIVE_PAPER) != SAMPLE_KIND_LIVE_PAPER


def infer_sample_kind_from_db_path(db_path: str) -> str:
    stem = Path(db_path).stem.lower()
    if any(fragment in stem for fragment in ("runtime_verification", "crypto_dual", "crypto_triple", "verify")):
        return SAMPLE_KIND_SYNTHETIC_VERIFY
    if "replay" in stem or "backtest" in stem:
        return SAMPLE_KIND_REPLAY
    return SAMPLE_KIND_LIVE_PAPER


def infer_debug_profile_from_db_path(db_path: str) -> Optional[str]:
    stem = Path(db_path).stem.lower()
    if "runtime_verification" in stem:
        return "sports"
    if "crypto_dual" in stem:
        return "crypto_dual"
    if "crypto_triple" in stem:
        return "crypto_triple_long"
    return None


def slippage_proxy_bps_from_spread(spread_pct: Optional[float]) -> Optional[float]:
    if spread_pct is None:
        return None
    return round(float(spread_pct) * 10_000 / 2.0, 4)


def normalize_strategy_profile(raw_strategy_profile: Optional[str]) -> str:
    normalized = (raw_strategy_profile or STRATEGY_PROFILE_BASELINE).strip().lower()
    if normalized == STRATEGY_PROFILE_SAMPLING_RELAXED:
        return STRATEGY_PROFILE_SAMPLING_RELAXED
    return STRATEGY_PROFILE_BASELINE
