from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import PolymarketResearchSettings
from .repository import PolymarketResearchRepository


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


@dataclass(slots=True)
class PolymarketResearchService:
    settings: PolymarketResearchSettings
    repository: PolymarketResearchRepository

    def _decorate_candidate(self, row) -> dict[str, Any]:
        crypto_ratio = 0.0
        total_trade_rows = int(row["total_trade_rows"] or 0)
        crypto_trade_rows = int(row["crypto_trade_rows"] or 0)
        if total_trade_rows > 0:
            crypto_ratio = crypto_trade_rows / total_trade_rows

        trust_score = float(row["trust_score"] or 0.5)
        discovery_score = float(row["discovery_score"] or 0.0)
        active_days = int(row["active_days"] or 0)
        closed_trades = int(row["closed_trade_count"] or 0)
        realized_pnl = float(row["realized_pnl"] or 0.0)
        realized_component = _clamp(realized_pnl / 5000.0)
        active_component = _clamp(active_days / 14.0)
        closed_component = _clamp(closed_trades / 25.0)
        consistency_score = round(
            (trust_score * 0.30)
            + (discovery_score * 0.25)
            + (closed_component * 0.20)
            + (active_component * 0.15)
            + (realized_component * 0.10),
            4,
        )

        specialization = str(row["specialization_hint"] or row["last_event_category"] or "UNKNOWN").upper()
        if crypto_ratio >= 0.6 and specialization == "UNKNOWN":
            specialization = "CRYPTO"

        return {
            "address": str(row["address"]),
            "source_type": str(row["source_type"]),
            "discovery_score": round(discovery_score, 4),
            "trust_score": round(trust_score, 4),
            "active_days": active_days,
            "closed_trade_count": closed_trades,
            "realized_pnl": round(realized_pnl, 4),
            "crypto_participation_ratio": round(crypto_ratio, 4),
            "specialization": specialization,
            "event_count_24h": int(row["event_count_24h"] or 0),
            "last_event_amount": round(float(row["last_event_amount"] or 0.0), 4),
            "consistency_score": consistency_score,
        }

    def build_summary(self) -> dict[str, Any]:
        self.repository.ensure_tables()
        candidates = [self._decorate_candidate(row) for row in self.repository.fetch_candidate_wallets(self.settings.discovery_pool_size)]
        candidates.sort(key=lambda item: (item["consistency_score"], item["trust_score"], item["realized_pnl"]), reverse=True)

        shadow_wallets = candidates[: self.settings.shadow_pool_size]
        copy_ready_limit = self.settings.copy_ready_size

        shadow_actions = []
        shadow_by_wallet: dict[str, dict[str, Any]] = {}
        for row in self.repository.fetch_shadow_action_rows(self.settings.shadow_window_days):
            wallet_address = str(row["wallet_address"])
            stats = shadow_by_wallet.setdefault(
                wallet_address,
                {
                    "closed_shadow_trades": 0,
                    "shadow_pnl": 0.0,
                    "shadow_edge": 0.0,
                    "worst_drawdown_pct": 0.0,
                },
            )
            if str(row["status"]).upper() != "OPEN":
                stats["closed_shadow_trades"] += 1
            stats["shadow_pnl"] += float(row["shadow_pnl"] or 0.0)
            stats["shadow_edge"] += float(row["shadow_edge"] or 0.0)
            stats["worst_drawdown_pct"] = min(stats["worst_drawdown_pct"], float(row["drawdown_pct"] or 0.0))

        shadow_wallet_summary = []
        for candidate in shadow_wallets:
            shadow_stats = shadow_by_wallet.get(candidate["address"], {})
            shadow_wallet_summary.append(
                {
                    **candidate,
                    "closed_shadow_trades": int(shadow_stats.get("closed_shadow_trades", 0)),
                    "shadow_pnl": round(float(shadow_stats.get("shadow_pnl", 0.0)), 4),
                    "shadow_edge": round(float(shadow_stats.get("shadow_edge", 0.0)), 4),
                    "worst_drawdown_pct": round(float(shadow_stats.get("worst_drawdown_pct", 0.0)), 4),
                }
            )

        copy_ready = [
            row
            for row in shadow_wallet_summary
            if row["closed_shadow_trades"] >= 3 and row["shadow_edge"] > 0 and row["worst_drawdown_pct"] >= -15.0
        ][:copy_ready_limit]

        recent_shadow_actions = []
        for row in self.repository.fetch_recent_shadow_actions(12):
            recent_shadow_actions.append(
                {
                    "wallet_address": str(row["wallet_address"]),
                    "market_id": str(row["market_id"]),
                    "category": str(row["category"] or "UNKNOWN"),
                    "source_type": str(row["source_type"] or "unknown"),
                    "action_type": str(row["action_type"] or "shadow_trade"),
                    "shadow_pnl": round(float(row["shadow_pnl"] or 0.0), 4),
                    "shadow_edge": round(float(row["shadow_edge"] or 0.0), 4),
                    "drawdown_pct": round(float(row["drawdown_pct"] or 0.0), 4),
                    "opened_at": str(row["opened_at"] or ""),
                    "closed_at": str(row["closed_at"] or ""),
                    "status": str(row["status"] or "OPEN"),
                }
            )

        positive_shadow_wallets = sum(1 for row in shadow_wallet_summary if row["shadow_edge"] > 0)
        closed_shadow_trades = sum(int(row["closed_shadow_trades"]) for row in shadow_wallet_summary)
        net_shadow_edge = round(sum(float(row["shadow_edge"]) for row in shadow_wallet_summary), 4)
        net_shadow_pnl = round(sum(float(row["shadow_pnl"]) for row in shadow_wallet_summary), 4)
        worst_drawdown = round(min([float(row["worst_drawdown_pct"]) for row in shadow_wallet_summary] or [0.0]), 4)

        return {
            "discovery_wallet_summary": {
                "tracked_wallets": len(candidates),
                "discovery_pool_target": self.settings.discovery_pool_size,
                "shadow_pool_target": self.settings.shadow_pool_size,
                "copy_ready_target": self.settings.copy_ready_size,
                "crypto_specialists": sum(1 for row in candidates if row["specialization"] == "CRYPTO"),
                "promoted_to_shadow": min(len(candidates), self.settings.shadow_pool_size),
            },
            "shadow_wallet_summary": {
                "shadow_wallets": len(shadow_wallet_summary),
                "wallets_with_shadow_actions": sum(1 for row in shadow_wallet_summary if row["closed_shadow_trades"] > 0),
                "closed_shadow_trades": closed_shadow_trades,
                "positive_shadow_wallets": positive_shadow_wallets,
            },
            "copy_ready_wallet_summary": {
                "copy_ready_wallets": len(copy_ready),
                "copy_ready_target": self.settings.copy_ready_size,
                "minimum_shadow_trades": 3,
                "positive_shadow_edge_wallets": sum(1 for row in shadow_wallet_summary if row["shadow_edge"] > 0),
            },
            "shadow_edge_summary": {
                "evaluation_window_days": self.settings.shadow_window_days,
                "net_shadow_edge": net_shadow_edge,
                "net_shadow_pnl": net_shadow_pnl,
                "worst_drawdown_pct": worst_drawdown,
                "shadow_ready": net_shadow_edge > 0 and len(copy_ready) > 0,
            },
            "recent_shadow_actions": recent_shadow_actions,
            "wallet_consistency_table": shadow_wallet_summary[:12],
            "shadow_wallet_table": shadow_wallet_summary,
            "copy_ready_wallets": copy_ready,
        }
