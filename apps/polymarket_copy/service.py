from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .config import PolymarketCopySettings
from .repository import PolymarketCopyRepository


def _parse_utc(value: str | None) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _follower_pnl(source_pnl: float, source_notional_usd: float, follower_notional_usd: float) -> float:
    if source_notional_usd <= 0 or follower_notional_usd <= 0:
        return 0.0
    ratio = follower_notional_usd / source_notional_usd
    return round(source_pnl * ratio, 4)


def _normalize_side(side: str) -> str:
    normalized = side.strip().upper()
    if normalized in {"YES", "BUY", "LONG", "UP"}:
        return "BUY"
    if normalized in {"NO", "SELL", "SHORT", "DOWN"}:
        return "SELL"
    return normalized or "BUY"


@dataclass(slots=True)
class PolymarketCopyService:
    settings: PolymarketCopySettings
    repository: PolymarketCopyRepository

    def sync_copy_actions(self) -> dict[str, int]:
        self.repository.ensure_tables()
        copy_ready_wallets = self.repository.fetch_copy_ready_wallets(limit=self.settings.copy_ready_limit)
        wallet_addresses = [str(row["address"] or "").lower() for row in copy_ready_wallets]
        source_rows = self.repository.fetch_source_trade_rows(wallet_addresses, self.settings.lookback_days)

        counters = {
            "wallets_considered": len(copy_ready_wallets),
            "source_rows_considered": len(source_rows),
            "open_actions": 0,
            "close_actions": 0,
            "replay_closed_actions": 0,
            "reject_actions": 0,
        }

        now = datetime.now(timezone.utc)
        for row in source_rows:
            source_key = str(row["source_trade_key"])
            wallet_address = str(row["wallet_address"])
            market_id = str(row["market_id"] or "")
            if market_id == "":
                continue
            category = str(row["category"] or "UNKNOWN")
            source_status = str(row["status"] or "UNKNOWN").upper()
            source_notional = round(abs(float(row["source_notional_usd"] or 0.0)), 4)
            source_pnl = round(float(row["source_pnl"] or 0.0), 4)
            source_opened_at = str(row["source_opened_at"] or row["occurred_at"] or "")
            source_closed_at = str(row["source_closed_at"] or row["occurred_at"] or "")
            normalized_side = _normalize_side(str(row["side"] or "BUY"))
            notes = {
                "category": category,
                "source_signal": row.get("source_signal", ""),
                "source_status": source_status,
            }

            is_closed = source_status.startswith("CLOSED")
            open_position = self.repository.fetch_open_position_by_source_trade_key(source_key)

            if is_closed and open_position is not None:
                if not self.repository.source_trade_action_exists(source_key, "close"):
                    follower_notional = float(open_position["follower_notional_usd"] or 0.0)
                    follower_pnl = _follower_pnl(source_pnl, source_notional, follower_notional)
                    self.repository.close_position_by_source_trade_key(
                        source_trade_key=source_key,
                        source_status=source_status,
                        source_closed_at=source_closed_at,
                        source_pnl=source_pnl,
                        follower_pnl=follower_pnl,
                        notes=notes,
                    )
                    self.repository.insert_copy_action(
                        source_trade_key=source_key,
                        wallet_address=wallet_address,
                        market_id=market_id,
                        category=category,
                        action_type="close",
                        reason="source_closed",
                        source_status=source_status,
                        side=normalized_side,
                        source_notional_usd=source_notional,
                        follower_notional_usd=follower_notional,
                        source_pnl=source_pnl,
                        follower_pnl=follower_pnl,
                        delayed_seconds=0,
                        source_opened_at=source_opened_at,
                        source_closed_at=source_closed_at,
                        executed_at=source_closed_at or None,
                        notes=notes,
                    )
                    counters["close_actions"] += 1
                continue

            if is_closed and open_position is None:
                if self.repository.source_trade_action_exists(source_key, "replay_closed"):
                    continue
                follower_notional = round(
                    min(source_notional, self.settings.max_trade_size_usd),
                    4,
                )
                if follower_notional < self.settings.min_trade_size_usd:
                    self.repository.insert_copy_action(
                        source_trade_key=source_key,
                        wallet_address=wallet_address,
                        market_id=market_id,
                        category=category,
                        action_type="reject",
                        reason="min_trade_size_not_met",
                        source_status=source_status,
                        side=normalized_side,
                        source_notional_usd=source_notional,
                        follower_notional_usd=follower_notional,
                        source_pnl=source_pnl,
                        follower_pnl=0.0,
                        delayed_seconds=0,
                        source_opened_at=source_opened_at,
                        source_closed_at=source_closed_at,
                        executed_at=source_closed_at or None,
                        notes=notes,
                    )
                    counters["reject_actions"] += 1
                    continue
                follower_pnl = _follower_pnl(source_pnl, source_notional, follower_notional)
                self.repository.create_or_replace_position(
                    source_trade_key=source_key,
                    wallet_address=wallet_address,
                    market_id=market_id,
                    category=category,
                    side=normalized_side,
                    source_notional_usd=source_notional,
                    follower_notional_usd=follower_notional,
                    source_pnl=source_pnl,
                    follower_pnl=follower_pnl,
                    source_status=source_status,
                    status="CLOSED",
                    source_opened_at=source_opened_at,
                    source_closed_at=source_closed_at,
                    opened_at=source_opened_at or source_closed_at or None,
                    closed_at=source_closed_at or None,
                    notes=notes,
                )
                self.repository.insert_copy_action(
                    source_trade_key=source_key,
                    wallet_address=wallet_address,
                    market_id=market_id,
                    category=category,
                    action_type="replay_closed",
                    reason="shadow_replay_seed",
                    source_status=source_status,
                    side=normalized_side,
                    source_notional_usd=source_notional,
                    follower_notional_usd=follower_notional,
                    source_pnl=source_pnl,
                    follower_pnl=follower_pnl,
                    delayed_seconds=0,
                    source_opened_at=source_opened_at,
                    source_closed_at=source_closed_at,
                    executed_at=source_closed_at or None,
                    notes=notes,
                )
                counters["replay_closed_actions"] += 1
                continue

            if open_position is not None or self.repository.source_trade_action_exists(source_key, "open"):
                continue

            source_open_time = _parse_utc(source_opened_at) or _parse_utc(row.get("occurred_at", ""))
            delayed_seconds = 0
            if source_open_time is not None:
                delayed_seconds = int((now - source_open_time).total_seconds())
                if delayed_seconds < self.settings.follower_delay_seconds:
                    continue

            follower_notional = round(
                min(source_notional, self.settings.max_trade_size_usd),
                4,
            )
            if follower_notional < self.settings.min_trade_size_usd:
                self.repository.insert_copy_action(
                    source_trade_key=source_key,
                    wallet_address=wallet_address,
                    market_id=market_id,
                    category=category,
                    action_type="reject",
                    reason="min_trade_size_not_met",
                    source_status=source_status,
                    side=normalized_side,
                    source_notional_usd=source_notional,
                    follower_notional_usd=follower_notional,
                    source_pnl=source_pnl,
                    follower_pnl=0.0,
                    delayed_seconds=delayed_seconds,
                    source_opened_at=source_opened_at,
                    source_closed_at=source_closed_at,
                    notes=notes,
                )
                counters["reject_actions"] += 1
                continue

            if self.repository.fetch_open_position_for_wallet_market(wallet_address, market_id) is not None:
                self.repository.insert_copy_action(
                    source_trade_key=source_key,
                    wallet_address=wallet_address,
                    market_id=market_id,
                    category=category,
                    action_type="reject",
                    reason="duplicate_market_exposure",
                    source_status=source_status,
                    side=normalized_side,
                    source_notional_usd=source_notional,
                    follower_notional_usd=follower_notional,
                    source_pnl=source_pnl,
                    follower_pnl=0.0,
                    delayed_seconds=delayed_seconds,
                    source_opened_at=source_opened_at,
                    source_closed_at=source_closed_at,
                    notes=notes,
                )
                counters["reject_actions"] += 1
                continue

            if self.repository.fetch_open_wallet_notional(wallet_address) + follower_notional > self.settings.wallet_risk_limit_usd:
                self.repository.insert_copy_action(
                    source_trade_key=source_key,
                    wallet_address=wallet_address,
                    market_id=market_id,
                    category=category,
                    action_type="reject",
                    reason="wallet_risk_limit_exceeded",
                    source_status=source_status,
                    side=normalized_side,
                    source_notional_usd=source_notional,
                    follower_notional_usd=follower_notional,
                    source_pnl=source_pnl,
                    follower_pnl=0.0,
                    delayed_seconds=delayed_seconds,
                    source_opened_at=source_opened_at,
                    source_closed_at=source_closed_at,
                    notes=notes,
                )
                counters["reject_actions"] += 1
                continue

            if self.repository.fetch_open_market_notional(market_id) + follower_notional > self.settings.market_risk_limit_usd:
                self.repository.insert_copy_action(
                    source_trade_key=source_key,
                    wallet_address=wallet_address,
                    market_id=market_id,
                    category=category,
                    action_type="reject",
                    reason="market_risk_limit_exceeded",
                    source_status=source_status,
                    side=normalized_side,
                    source_notional_usd=source_notional,
                    follower_notional_usd=follower_notional,
                    source_pnl=source_pnl,
                    follower_pnl=0.0,
                    delayed_seconds=delayed_seconds,
                    source_opened_at=source_opened_at,
                    source_closed_at=source_closed_at,
                    notes=notes,
                )
                counters["reject_actions"] += 1
                continue

            self.repository.create_or_replace_position(
                source_trade_key=source_key,
                wallet_address=wallet_address,
                market_id=market_id,
                category=category,
                side=normalized_side,
                source_notional_usd=source_notional,
                follower_notional_usd=follower_notional,
                source_pnl=0.0,
                follower_pnl=0.0,
                source_status=source_status,
                status="OPEN",
                source_opened_at=source_opened_at,
                source_closed_at="",
                opened_at=_parse_utc(source_opened_at).strftime("%Y-%m-%d %H:%M:%S") if _parse_utc(source_opened_at) else None,
                closed_at=None,
                notes=notes,
            )
            self.repository.insert_copy_action(
                source_trade_key=source_key,
                wallet_address=wallet_address,
                market_id=market_id,
                category=category,
                action_type="open",
                reason="copy_entry",
                source_status=source_status,
                side=normalized_side,
                source_notional_usd=source_notional,
                follower_notional_usd=follower_notional,
                source_pnl=0.0,
                follower_pnl=0.0,
                delayed_seconds=delayed_seconds,
                source_opened_at=source_opened_at,
                source_closed_at=source_closed_at,
                notes=notes,
            )
            counters["open_actions"] += 1

        return counters

    def build_summary(self) -> dict[str, Any]:
        self.repository.ensure_tables()
        copy_ready_wallets = [dict(row) for row in self.repository.fetch_copy_ready_wallets(limit=self.settings.copy_ready_limit)]
        actions = [dict(row) for row in self.repository.fetch_copy_actions()]
        recent_actions = [dict(row) for row in self.repository.fetch_recent_copy_actions(12)]
        active_positions = [dict(row) for row in self.repository.fetch_active_copy_positions()]
        wallet_rows = [dict(row) for row in self.repository.fetch_wallet_follower_pnl_rows()]
        reject_rows = [dict(row) for row in self.repository.fetch_copy_action_reason_counts()]

        open_actions = sum(1 for row in actions if row["action_type"] == "open")
        close_actions = sum(1 for row in actions if row["action_type"] == "close")
        replay_closed_actions = sum(1 for row in actions if row["action_type"] == "replay_closed")
        reject_actions = sum(1 for row in actions if row["action_type"] == "reject")

        copy_realized_pnl = round(
            sum(float(row["follower_pnl"] or 0.0) for row in actions if row["action_type"] in {"close", "replay_closed"}),
            4,
        )
        source_realized_pnl = round(
            sum(float(row["source_pnl"] or 0.0) for row in actions if row["action_type"] in {"close", "replay_closed"}),
            4,
        )
        copy_ready_shadow_edge = round(
            sum(float(row.get("shadow_edge") or 0.0) for row in copy_ready_wallets),
            4,
        )
        shadow_closed = sum(int(row.get("closed_shadow_trades") or 0) for row in copy_ready_wallets)

        wallet_summary_rows = []
        for row in wallet_rows:
            follower_realized = round(float(row["follower_realized_pnl"] or 0.0), 4)
            source_realized = round(float(row["source_realized_pnl"] or 0.0), 4)
            wallet_summary_rows.append(
                {
                    "wallet_address": str(row["wallet_address"] or ""),
                    "closed_actions": int(row["closed_actions"] or 0),
                    "follower_realized_pnl": follower_realized,
                    "source_realized_pnl": source_realized,
                    "pnl_drift": round(follower_realized - source_realized, 4),
                    "opened_notional_usd": round(float(row["opened_notional_usd"] or 0.0), 4),
                    "last_action_at": str(row["last_action_at"] or ""),
                }
            )

        recent_copy_actions = []
        for row in recent_actions:
            notes = {}
            raw_notes = str(row.get("notes_json") or "")
            if raw_notes:
                try:
                    notes = json.loads(raw_notes)
                except (TypeError, ValueError):
                    notes = {}
            recent_copy_actions.append(
                {
                    "executed_at": str(row["executed_at"] or ""),
                    "wallet_address": str(row["wallet_address"] or ""),
                    "market_id": str(row["market_id"] or ""),
                    "category": str(row["category"] or "UNKNOWN"),
                    "action_type": str(row["action_type"] or ""),
                    "reason": str(row["reason"] or ""),
                    "side": str(row["side"] or ""),
                    "source_status": str(row["source_status"] or ""),
                    "source_notional_usd": round(float(row["source_notional_usd"] or 0.0), 4),
                    "follower_notional_usd": round(float(row["follower_notional_usd"] or 0.0), 4),
                    "source_pnl": round(float(row["source_pnl"] or 0.0), 4),
                    "follower_pnl": round(float(row["follower_pnl"] or 0.0), 4),
                    "delayed_seconds": int(row["delayed_seconds"] or 0),
                    "source_opened_at": str(row["source_opened_at"] or ""),
                    "source_closed_at": str(row["source_closed_at"] or ""),
                    "notes": notes,
                }
            )

        active_copy_positions = [
            {
                "wallet_address": str(row["wallet_address"] or ""),
                "market_id": str(row["market_id"] or ""),
                "category": str(row["category"] or "UNKNOWN"),
                "side": str(row["side"] or ""),
                "source_notional_usd": round(float(row["source_notional_usd"] or 0.0), 4),
                "follower_notional_usd": round(float(row["follower_notional_usd"] or 0.0), 4),
                "opened_at": str(row["opened_at"] or ""),
                "source_opened_at": str(row["source_opened_at"] or ""),
                "status": str(row["status"] or ""),
            }
            for row in active_positions
        ]

        return {
            "copy_execution_summary": {
                "copy_ready_wallets": len(copy_ready_wallets),
                "copy_window_days": self.settings.lookback_days,
                "open_actions": open_actions,
                "close_actions": close_actions,
                "replay_closed_actions": replay_closed_actions,
                "reject_actions": reject_actions,
                "active_copy_positions": len(active_positions),
                "wallets_with_realized_pnl": sum(1 for row in wallet_summary_rows if row["closed_actions"] > 0),
            },
            "copy_reject_breakdown": [
                {"reason": str(row["reason"] or ""), "count": int(row["count"] or 0)}
                for row in reject_rows
            ],
            "active_copy_positions": active_copy_positions,
            "wallet_follower_pnl_summary": wallet_summary_rows,
            "shadow_vs_copy_drift_summary": {
                "copy_ready_wallets": len(copy_ready_wallets),
                "shadow_closed_trades": shadow_closed,
                "shadow_net_edge": copy_ready_shadow_edge,
                "source_realized_pnl": source_realized_pnl,
                "copy_realized_pnl": copy_realized_pnl,
                "copy_vs_source_pnl_gap": round(copy_realized_pnl - source_realized_pnl, 4),
            },
            "recent_copy_actions": recent_copy_actions,
        }
