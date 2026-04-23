from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Iterable

import requests

from .config import PolymarketCopySettings


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_time(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
    if text.isdigit():
        try:
            return datetime.fromtimestamp(int(text), tz=timezone.utc).replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
        except (OSError, OverflowError, ValueError):
            return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
    normalized = text.replace("T", " ").replace("Z", "")
    if "." in normalized:
        normalized = normalized.split(".", 1)[0]
    return normalized[:19]


def _normalize_side(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"BUY", "YES", "LONG", "UP"}:
        return "BUY"
    if text in {"SELL", "NO", "SHORT", "DOWN"}:
        return "SELL"
    return "BUY"


def _market_id(payload: dict[str, Any]) -> str:
    for key in (
        "conditionId",
        "condition_id",
        "marketId",
        "market_id",
        "market",
        "slug",
        "title",
        "question",
    ):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def _category(payload: dict[str, Any], market_id: str) -> str:
    raw = str(payload.get("category") or payload.get("eventCategory") or "").strip().upper()
    if raw:
        return raw
    haystack = " ".join(
        str(payload.get(key) or "")
        for key in ("title", "question", "market", "slug", "eventTitle", "eventSlug")
    ).lower()
    if any(token in haystack for token in ("bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto")):
        return "CRYPTO"
    if any(token in market_id.lower() for token in ("bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto")):
        return "CRYPTO"
    return "UNKNOWN"


def _source_key(wallet_address: str, payload: dict[str, Any], market_id: str, side: str, occurred_at: str, notional: float) -> str:
    for key in ("id", "tradeId", "trade_id", "transactionHash", "transaction_hash", "txHash", "hash", "orderHash"):
        value = str(payload.get(key) or "").strip()
        if value:
            return f"activity:{wallet_address}:{value}"
    raw = "|".join(
        [
            wallet_address,
            market_id,
            side,
            occurred_at,
            str(round(notional, 6)),
            str(payload.get("price") or ""),
        ]
    )
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]
    return f"activity:{wallet_address}:{digest}"


class PolymarketWalletActivityClient:
    def __init__(self, settings: PolymarketCopySettings):
        self.settings = settings

    def fetch_wallet_activity_rows(self, wallet_addresses: Iterable[str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for wallet_address in wallet_addresses:
            rows.extend(self._fetch_single_wallet(str(wallet_address).strip().lower()))
        return rows

    def _fetch_single_wallet(self, wallet_address: str) -> list[dict[str, Any]]:
        if not wallet_address:
            return []
        try:
            response = requests.get(
                f"{self.settings.data_api_base.rstrip('/')}/activity",
                params={"user": wallet_address, "limit": max(1, int(self.settings.live_activity_limit))},
                timeout=(
                    float(self.settings.activity_connect_timeout_sec),
                    float(self.settings.activity_read_timeout_sec),
                ),
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return []

        if isinstance(payload, dict):
            activities = payload.get("data") or payload.get("activities") or payload.get("results") or []
        else:
            activities = payload
        if not isinstance(activities, list):
            return []

        rows: list[dict[str, Any]] = []
        for item in activities:
            if not isinstance(item, dict):
                continue
            activity_type = str(item.get("type") or item.get("activityType") or item.get("eventType") or "TRADE").upper()
            if activity_type and "TRADE" not in activity_type:
                continue
            side = _normalize_side(item.get("side") or item.get("action") or item.get("outcome") or item.get("outcomeType"))
            market_id = _market_id(item)
            if not market_id:
                continue
            price = _safe_float(item.get("price"))
            raw_size = _safe_float(item.get("size") or item.get("amount") or item.get("shares"))
            notional = _safe_float(item.get("usdcSize") or item.get("usdc_size") or item.get("notional") or item.get("value"))
            if notional <= 0 and raw_size > 0 and price > 0:
                notional = raw_size * price
            if notional <= 0:
                notional = raw_size
            occurred_at = _normalize_time(
                item.get("timestamp") or item.get("createdAt") or item.get("created_at") or item.get("time")
            )
            rows.append(
                {
                    "source_trade_key": _source_key(wallet_address, item, market_id, side, occurred_at, notional),
                    "wallet_address": wallet_address,
                    "venue": "polymarket",
                    "market_id": market_id,
                    "status": "OPEN",
                    "source_pnl": 0.0,
                    "source_notional_usd": abs(round(float(notional or 0.0), 4)),
                    "category": _category(item, market_id),
                    "side": side,
                    "occurred_at": occurred_at,
                    "source_opened_at": occurred_at,
                    "source_closed_at": "",
                    "source_signal": "polymarket_wallet_activity",
                }
            )
        return rows
