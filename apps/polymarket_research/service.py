from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .config import PolymarketResearchSettings
from .repository import PolymarketResearchRepository


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def _parse_timestamp(raw: str | None) -> datetime | None:
    if raw in (None, ""):
        return None
    text = str(raw).strip()
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


@dataclass(slots=True)
class PolymarketResearchService:
    settings: PolymarketResearchSettings
    repository: PolymarketResearchRepository

    def _build_trade_context(self, rows: list[Any]) -> dict[str, dict[str, float]]:
        grouped: dict[str, list[Any]] = {}
        for row in rows:
            address = str(row["address"] or "").lower().strip()
            if address == "":
                continue
            grouped.setdefault(address, []).append(row)

        context: dict[str, dict[str, float]] = {}
        for address, wallet_rows in grouped.items():
            ordered_rows = sorted(wallet_rows, key=lambda item: str(item["occurred_at"] or ""))
            cumulative = 0.0
            peak = 0.0
            trough = 0.0
            wins = 0
            day_totals: dict[str, float] = {}
            for row in ordered_rows:
                pnl = float(row["pnl"] or 0.0)
                cumulative += pnl
                peak = max(peak, cumulative)
                trough = min(trough, cumulative - peak)
                if pnl > 0:
                    wins += 1
                occurred_at = str(row["occurred_at"] or "")
                trade_day = occurred_at[:10] if len(occurred_at) >= 10 else ""
                if trade_day:
                    day_totals[trade_day] = day_totals.get(trade_day, 0.0) + pnl

            closed_count = len(ordered_rows)
            positive_trade_ratio = wins / closed_count if closed_count else 0.0
            positive_day_ratio = 0.0
            if day_totals:
                positive_day_ratio = sum(1 for pnl in day_totals.values() if pnl > 0) / len(day_totals)

            capital_reference = max(abs(peak), abs(cumulative), 100.0)
            drawdown_estimate_pct = round(abs(trough) / capital_reference * 100.0, 4) if capital_reference > 0 else 0.0
            profit_consistency_score = round(
                _clamp((positive_trade_ratio * 0.60) + (positive_day_ratio * 0.40)),
                4,
            )

            context[address] = {
                "drawdown_estimate_pct": drawdown_estimate_pct,
                "profit_consistency_score": profit_consistency_score,
            }

        return context

    def _compute_recency_score(self, last_seen_at: str | None) -> float:
        last_seen = _parse_timestamp(last_seen_at)
        if last_seen is None:
            return 0.0
        age_days = (datetime.now(timezone.utc) - last_seen).total_seconds() / 86400.0
        if age_days <= 1:
            return 1.0
        if age_days <= 3:
            return 0.85
        if age_days <= 7:
            return 0.65
        if age_days <= 14:
            return 0.45
        if age_days <= 30:
            return 0.25
        return 0.1

    def _compute_frequency_score(self, *, active_days: int, event_count_24h: int) -> float:
        active_component = _clamp(active_days / 14.0)
        event_component = _clamp(event_count_24h / 10.0)
        return round(_clamp((active_component * 0.70) + (event_component * 0.30)), 4)

    def _decorate_candidate(self, row, trade_context: dict[str, dict[str, float]]) -> dict[str, Any]:
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
        wallet_address = str(row["address"]).lower()
        trade_metrics = trade_context.get(wallet_address, {})
        profit_consistency_score = float(trade_metrics.get("profit_consistency_score", 0.0))
        drawdown_estimate_pct = float(trade_metrics.get("drawdown_estimate_pct", 0.0))
        recency_score = self._compute_recency_score(row["last_seen_at"])
        frequency_score = self._compute_frequency_score(
            active_days=active_days,
            event_count_24h=int(row["event_count_24h"] or 0),
        )
        consistency_score = round(
            (trust_score * 0.22)
            + (discovery_score * 0.18)
            + (closed_component * 0.16)
            + (active_component * 0.10)
            + (realized_component * 0.10)
            + (_clamp(crypto_ratio) * 0.08)
            + (profit_consistency_score * 0.10)
            + (recency_score * 0.04)
            + (frequency_score * 0.02),
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
            "last_seen_at": str(row["last_seen_at"] or ""),
            "recency_score": round(recency_score, 4),
            "frequency_score": round(frequency_score, 4),
            "profit_consistency_score": round(profit_consistency_score, 4),
            "drawdown_estimate_pct": round(drawdown_estimate_pct, 4),
            "consistency_score": consistency_score,
        }

    def build_summary(self) -> dict[str, Any]:
        self.repository.ensure_tables()
        raw_candidates = self.repository.fetch_candidate_wallets(self.settings.discovery_pool_size)
        trade_rows = self.repository.fetch_wallet_trade_rows([str(row["address"] or "") for row in raw_candidates])
        trade_context = self._build_trade_context(trade_rows)
        candidates = [self._decorate_candidate(row, trade_context) for row in raw_candidates]
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

        copy_ready_rank_by_address = {row["address"]: index + 1 for index, row in enumerate(copy_ready)}
        shadow_rank_by_address = {row["address"]: index + 1 for index, row in enumerate(shadow_wallet_summary)}

        persisted_rows = []
        for index, candidate in enumerate(candidates, start=1):
            shadow_stats = shadow_by_wallet.get(
                candidate["address"],
                {
                    "closed_shadow_trades": 0,
                    "shadow_pnl": 0.0,
                    "shadow_edge": 0.0,
                    "worst_drawdown_pct": 0.0,
                },
            )
            is_shadow_wallet = candidate["address"] in shadow_rank_by_address
            is_copy_ready = candidate["address"] in copy_ready_rank_by_address
            cohort = "copy_ready" if is_copy_ready else ("shadow" if is_shadow_wallet else "discovery")
            persisted_rows.append(
                {
                    "address": candidate["address"],
                    "source_type": candidate["source_type"],
                    "cohort": cohort,
                    "discovery_rank": index,
                    "shadow_rank": shadow_rank_by_address.get(candidate["address"], 0),
                    "copy_ready_rank": copy_ready_rank_by_address.get(candidate["address"], 0),
                    "discovery_score": candidate["discovery_score"],
                    "trust_score": candidate["trust_score"],
                    "consistency_score": candidate["consistency_score"],
                    "profit_consistency_score": candidate["profit_consistency_score"],
                    "recency_score": candidate["recency_score"],
                    "frequency_score": candidate["frequency_score"],
                    "drawdown_estimate_pct": candidate["drawdown_estimate_pct"],
                    "active_days": candidate["active_days"],
                    "closed_trade_count": candidate["closed_trade_count"],
                    "realized_pnl": candidate["realized_pnl"],
                    "crypto_participation_ratio": candidate["crypto_participation_ratio"],
                    "specialization": candidate["specialization"],
                    "event_count_24h": candidate["event_count_24h"],
                    "last_event_amount": candidate["last_event_amount"],
                    "last_seen_at": candidate["last_seen_at"],
                    "closed_shadow_trades": int(shadow_stats.get("closed_shadow_trades", 0)),
                    "shadow_pnl": round(float(shadow_stats.get("shadow_pnl", 0.0)), 4),
                    "shadow_edge": round(float(shadow_stats.get("shadow_edge", 0.0)), 4),
                    "worst_drawdown_pct": round(float(shadow_stats.get("worst_drawdown_pct", 0.0)), 4),
                    "shadow_eligible": 1 if is_shadow_wallet else 0,
                    "copy_ready_eligible": 1 if is_copy_ready else 0,
                }
            )
        self.repository.replace_wallet_snapshots(persisted_rows)

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
                "persisted_wallet_snapshots": len(persisted_rows),
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
