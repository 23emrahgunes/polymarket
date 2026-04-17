from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .config import PolymarketResearchSettings
from .repository import PolymarketResearchRepository


DISCOVERY_BUCKET_TARGETS: dict[str, int] = {
    "leaderboard": 15,
    "activity_discovery": 15,
    "graph_discovery": 10,
    "manual_persisted": 10,
}
DISCOVERY_MIN_VIABLE_POOL = 30
SHADOW_CONSISTENCY_MIN = 0.45
SHADOW_CRYPTO_RATIO_MIN = 0.60
COPY_READY_MAX_DRAWDOWN = -15.0
MANUAL_PERSISTED_LABELS = {
    "manual_seed",
    "manual",
    "manual_confirmed",
    "persisted_wallets",
    "persisted_confirmed",
    "persisted_manual",
}


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


def _wallet_sort_key(item: dict[str, Any]) -> tuple[float, float, float, float]:
    return (
        float(item.get("consistency_score", 0.0)),
        float(item.get("trust_score", 0.0)),
        float(item.get("realized_pnl", 0.0)),
        float(item.get("recency_score", 0.0)),
    )


def _normalize_source_label(raw: str | None) -> str:
    text = str(raw or "").strip().lower()
    if text == "":
        return "unknown"
    if text == "leaderboard":
        return "leaderboard"
    if text == "activity_discovery":
        return "activity_discovery"
    if text == "graph_discovery":
        return "graph_discovery"
    if text in MANUAL_PERSISTED_LABELS:
        return "manual_persisted"
    if text == "static_seed":
        return "static_seed"
    return text


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
            positive_day_ratio = (
                sum(1 for pnl in day_totals.values() if pnl > 0) / len(day_totals)
                if day_totals
                else 0.0
            )

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

    def _build_provenance_map(self, candidate_rows: list[Any], provenance_rows: list[Any]) -> dict[str, dict[str, Any]]:
        provenance_map: dict[str, dict[str, Any]] = {}
        for row in provenance_rows:
            address = str(row["address"] or "").lower().strip()
            if address == "":
                continue
            payload = provenance_map.setdefault(address, {"raw_labels": [], "normalized_labels": set()})
            raw_label = str(row["source_type"] or "").strip()
            if raw_label == "":
                continue
            if raw_label not in payload["raw_labels"]:
                payload["raw_labels"].append(raw_label)
            payload["normalized_labels"].add(_normalize_source_label(raw_label))

        for row in candidate_rows:
            address = str(row["address"] or "").lower().strip()
            if address == "":
                continue
            payload = provenance_map.setdefault(address, {"raw_labels": [], "normalized_labels": set()})
            raw_label = str(row["source_type"] or "").strip()
            if raw_label and raw_label not in payload["raw_labels"]:
                payload["raw_labels"].append(raw_label)
            if raw_label:
                payload["normalized_labels"].add(_normalize_source_label(raw_label))

        for row in candidate_rows:
            address = str(row["address"] or "").lower().strip()
            raw_primary = str(row["source_type"] or "").strip()
            payload = provenance_map.setdefault(address, {"raw_labels": [], "normalized_labels": set()})
            normalized_labels = set(payload["normalized_labels"])
            primary_source = _normalize_source_label(raw_primary)
            if primary_source == "unknown" and normalized_labels:
                for preferred_source in ("leaderboard", "activity_discovery", "graph_discovery", "manual_persisted", "static_seed"):
                    if preferred_source in normalized_labels:
                        primary_source = preferred_source
                        break
                else:
                    primary_source = sorted(normalized_labels)[0]

            payload["raw_labels"] = sorted(payload["raw_labels"])
            payload["normalized_labels"] = normalized_labels
            payload["primary_source"] = primary_source
            payload["source_count"] = len(payload["raw_labels"])

        return provenance_map

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

    def _decorate_candidate(
        self,
        row: Any,
        trade_context: dict[str, dict[str, float]],
        provenance_map: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
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
        provenance = provenance_map.get(wallet_address, {})
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
            "source_type": str(row["source_type"] or "unknown"),
            "primary_source": str(provenance.get("primary_source") or _normalize_source_label(row["source_type"])),
            "source_labels": list(provenance.get("raw_labels", [])),
            "source_count": int(provenance.get("source_count", 0)),
            "_normalized_source_labels": set(provenance.get("normalized_labels", set())),
            "discovery_bucket": "unassigned",
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
            "closed_shadow_trades": 0,
            "shadow_pnl": 0.0,
            "shadow_edge": 0.0,
            "worst_drawdown_pct": 0.0,
            "shadow_gate_status": "blocked",
            "shadow_gate_reason": "low_consistency",
            "copy_ready_gate_status": "blocked",
            "copy_ready_gate_reason": "needs_shadow_history",
            "shadow_eligible": 0,
            "copy_ready_eligible": 0,
            "discovery_rank": 0,
            "shadow_rank": 0,
            "copy_ready_rank": 0,
            "cohort": "discovery",
        }

    def _build_discovery_pool(self, candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        discovery_pool_size = self.settings.discovery_pool_size
        selected_by_address: dict[str, dict[str, Any]] = {}
        actual_counts: Counter[str] = Counter()
        grouped_candidates: dict[str, list[dict[str, Any]]] = {
            bucket: [] for bucket in (*DISCOVERY_BUCKET_TARGETS.keys(), "static_seed")
        }

        for candidate in candidates:
            grouped_candidates.setdefault(candidate["primary_source"], []).append(candidate)

        for bucket, bucket_candidates in grouped_candidates.items():
            bucket_candidates.sort(key=_wallet_sort_key, reverse=True)

        for bucket, target in DISCOVERY_BUCKET_TARGETS.items():
            for candidate in grouped_candidates.get(bucket, []):
                if len(selected_by_address) >= discovery_pool_size:
                    break
                if candidate["address"] in selected_by_address:
                    continue
                if actual_counts[bucket] >= target:
                    break
                selected_by_address[candidate["address"]] = candidate
                candidate["discovery_bucket"] = bucket
                actual_counts[bucket] += 1

        for candidate in candidates:
            if len(selected_by_address) >= discovery_pool_size:
                break
            if candidate["address"] in selected_by_address:
                continue
            if candidate["primary_source"] == "static_seed":
                continue
            selected_by_address[candidate["address"]] = candidate
            candidate["discovery_bucket"] = candidate["primary_source"]
            actual_counts[candidate["discovery_bucket"]] += 1

        if len(selected_by_address) < min(DISCOVERY_MIN_VIABLE_POOL, discovery_pool_size):
            for candidate in grouped_candidates.get("static_seed", []):
                if len(selected_by_address) >= discovery_pool_size:
                    break
                if candidate["address"] in selected_by_address:
                    continue
                selected_by_address[candidate["address"]] = candidate
                candidate["discovery_bucket"] = "static_seed"
                actual_counts["static_seed"] += 1

        discovery_wallets = list(selected_by_address.values())
        discovery_wallets.sort(key=_wallet_sort_key, reverse=True)
        for index, candidate in enumerate(discovery_wallets, start=1):
            candidate["discovery_rank"] = index

        source_rows = [
            {
                "bucket": bucket,
                "target": target,
                "actual": int(actual_counts.get(bucket, 0)),
            }
            for bucket, target in DISCOVERY_BUCKET_TARGETS.items()
        ]
        if actual_counts.get("static_seed", 0) > 0:
            source_rows.append({"bucket": "static_seed", "target": 0, "actual": int(actual_counts["static_seed"])})

        return discovery_wallets, {
            "rows": source_rows,
            "selected_wallets": len(discovery_wallets),
            "min_viable_pool": min(DISCOVERY_MIN_VIABLE_POOL, discovery_pool_size),
            "static_seed_used": int(actual_counts.get("static_seed", 0)),
        }

    def _build_shadow_context(self) -> dict[str, dict[str, float]]:
        shadow_by_wallet: dict[str, dict[str, float]] = {}
        for row in self.repository.fetch_shadow_action_rows(self.settings.shadow_window_days):
            wallet_address = str(row["wallet_address"] or "").lower()
            if wallet_address == "":
                continue
            stats = shadow_by_wallet.setdefault(
                wallet_address,
                {
                    "closed_shadow_trades": 0,
                    "shadow_pnl": 0.0,
                    "shadow_edge": 0.0,
                    "worst_drawdown_pct": 0.0,
                },
            )
            if str(row["status"] or "").upper() != "OPEN":
                stats["closed_shadow_trades"] += 1
            stats["shadow_pnl"] += float(row["shadow_pnl"] or 0.0)
            stats["shadow_edge"] += float(row["shadow_edge"] or 0.0)
            stats["worst_drawdown_pct"] = min(stats["worst_drawdown_pct"], float(row["drawdown_pct"] or 0.0))
        return shadow_by_wallet

    def _shadow_gate_reason(self, wallet: dict[str, Any]) -> str:
        normalized_source_labels = wallet.get("_normalized_source_labels", set())
        if normalized_source_labels == {"static_seed"} or wallet.get("primary_source") == "static_seed":
            return "seed_only_excluded"
        if wallet.get("specialization") != "CRYPTO" and float(wallet.get("crypto_participation_ratio", 0.0)) < SHADOW_CRYPTO_RATIO_MIN:
            return "non_crypto_specialist"
        if int(wallet.get("active_days", 0)) < 3:
            return "low_activity"
        if int(wallet.get("closed_trade_count", 0)) < 3:
            return "low_closed_trades"
        if float(wallet.get("consistency_score", 0.0)) < SHADOW_CONSISTENCY_MIN:
            return "low_consistency"
        return "eligible"

    def _copy_ready_gate_reason(self, wallet: dict[str, Any]) -> str:
        if wallet.get("shadow_gate_status") != "promoted":
            return "not_shadow_wallet"
        if wallet.get("specialization") != "CRYPTO" and float(wallet.get("crypto_participation_ratio", 0.0)) < SHADOW_CRYPTO_RATIO_MIN:
            return "non_crypto_specialist"
        if int(wallet.get("closed_shadow_trades", 0)) < 3:
            return "needs_shadow_history"
        if float(wallet.get("shadow_edge", 0.0)) <= 0.0:
            return "negative_shadow_edge"
        if float(wallet.get("worst_drawdown_pct", 0.0)) < COPY_READY_MAX_DRAWDOWN:
            return "drawdown_too_deep"
        return "eligible"

    def build_summary(self) -> dict[str, Any]:
        self.repository.ensure_tables()
        raw_candidates = self.repository.fetch_candidate_wallets(limit=None)
        if not raw_candidates:
            self.repository.replace_wallet_snapshots([])
            return {
                "discovery_wallet_summary": {
                    "tracked_wallets": 0,
                    "discovery_pool_target": self.settings.discovery_pool_size,
                    "shadow_pool_target": self.settings.shadow_pool_size,
                    "copy_ready_target": self.settings.copy_ready_size,
                    "crypto_specialists": 0,
                    "promoted_to_shadow": 0,
                    "persisted_wallet_snapshots": 0,
                },
                "discovery_source_summary": {"rows": [], "selected_wallets": 0, "min_viable_pool": min(DISCOVERY_MIN_VIABLE_POOL, self.settings.discovery_pool_size), "static_seed_used": 0},
                "shadow_wallet_summary": {
                    "shadow_wallets": 0,
                    "wallets_with_shadow_actions": 0,
                    "closed_shadow_trades": 0,
                    "positive_shadow_wallets": 0,
                },
                "shadow_promotion_summary": {"eligible_wallets": 0, "promoted_wallets": 0, "blocked_wallets": 0, "blocker_counts": []},
                "copy_ready_wallet_summary": {
                    "copy_ready_wallets": 0,
                    "copy_ready_target": self.settings.copy_ready_size,
                    "minimum_shadow_trades": 3,
                    "positive_shadow_edge_wallets": 0,
                },
                "wallet_provenance_summary": {"multi_source_wallets": 0, "single_source_wallets": 0, "seed_only_wallets": 0},
                "shadow_edge_summary": {
                    "evaluation_window_days": self.settings.shadow_window_days,
                    "net_shadow_edge": 0.0,
                    "net_shadow_pnl": 0.0,
                    "worst_drawdown_pct": 0.0,
                    "shadow_ready": False,
                },
                "recent_shadow_actions": [],
                "wallet_consistency_table": [],
                "shadow_wallet_table": [],
                "copy_ready_wallets": [],
            }

        addresses = [str(row["address"] or "") for row in raw_candidates]
        trade_rows = self.repository.fetch_wallet_trade_rows(addresses)
        trade_context = self._build_trade_context(trade_rows)
        provenance_rows = self.repository.fetch_wallet_provenance(addresses)
        provenance_map = self._build_provenance_map(raw_candidates, provenance_rows)

        candidates = [
            self._decorate_candidate(row, trade_context, provenance_map)
            for row in raw_candidates
        ]
        candidates.sort(key=_wallet_sort_key, reverse=True)

        discovery_wallets, discovery_source_summary = self._build_discovery_pool(candidates)

        shadow_by_wallet = self._build_shadow_context()
        for candidate in discovery_wallets:
            shadow_stats = shadow_by_wallet.get(candidate["address"].lower(), {})
            candidate["closed_shadow_trades"] = int(shadow_stats.get("closed_shadow_trades", 0))
            candidate["shadow_pnl"] = round(float(shadow_stats.get("shadow_pnl", 0.0)), 4)
            candidate["shadow_edge"] = round(float(shadow_stats.get("shadow_edge", 0.0)), 4)
            candidate["worst_drawdown_pct"] = round(float(shadow_stats.get("worst_drawdown_pct", 0.0)), 4)

        shadow_eligible_wallets: list[dict[str, Any]] = []
        shadow_blockers: Counter[str] = Counter()
        for candidate in discovery_wallets:
            gate_reason = self._shadow_gate_reason(candidate)
            candidate["shadow_gate_reason"] = gate_reason
            candidate["shadow_eligible"] = 1 if gate_reason == "eligible" else 0
            if gate_reason == "eligible":
                shadow_eligible_wallets.append(candidate)
            else:
                shadow_blockers[gate_reason] += 1

        shadow_eligible_wallets.sort(key=_wallet_sort_key, reverse=True)
        shadow_promoted_addresses = {
            wallet["address"] for wallet in shadow_eligible_wallets[: self.settings.shadow_pool_size]
        }
        shadow_wallet_table: list[dict[str, Any]] = []
        for candidate in discovery_wallets:
            if candidate["shadow_gate_reason"] == "eligible":
                candidate["shadow_gate_status"] = "promoted" if candidate["address"] in shadow_promoted_addresses else "eligible"
            else:
                candidate["shadow_gate_status"] = "blocked"
            if candidate["shadow_gate_status"] == "promoted":
                shadow_wallet_table.append(candidate)

        shadow_wallet_table.sort(key=_wallet_sort_key, reverse=True)
        for index, wallet in enumerate(shadow_wallet_table, start=1):
            wallet["shadow_rank"] = index

        copy_ready_eligible_wallets: list[dict[str, Any]] = []
        for candidate in discovery_wallets:
            copy_ready_reason = self._copy_ready_gate_reason(candidate)
            candidate["copy_ready_gate_reason"] = copy_ready_reason
            candidate["copy_ready_eligible"] = 1 if copy_ready_reason == "eligible" else 0
            if copy_ready_reason == "eligible":
                copy_ready_eligible_wallets.append(candidate)

        copy_ready_eligible_wallets.sort(
            key=lambda item: (
                float(item.get("shadow_edge", 0.0)),
                float(item.get("consistency_score", 0.0)),
                float(item.get("trust_score", 0.0)),
            ),
            reverse=True,
        )
        copy_ready_addresses = {
            wallet["address"] for wallet in copy_ready_eligible_wallets[: self.settings.copy_ready_size]
        }
        copy_ready_wallets: list[dict[str, Any]] = []
        for candidate in discovery_wallets:
            if candidate["copy_ready_gate_reason"] == "eligible":
                candidate["copy_ready_gate_status"] = "promoted" if candidate["address"] in copy_ready_addresses else "eligible"
            else:
                candidate["copy_ready_gate_status"] = "blocked"
            if candidate["copy_ready_gate_status"] == "promoted":
                copy_ready_wallets.append(candidate)

        copy_ready_wallets.sort(
            key=lambda item: (
                float(item.get("shadow_edge", 0.0)),
                float(item.get("consistency_score", 0.0)),
                float(item.get("trust_score", 0.0)),
            ),
            reverse=True,
        )
        for index, wallet in enumerate(copy_ready_wallets, start=1):
            wallet["copy_ready_rank"] = index

        persisted_rows = []
        for candidate in discovery_wallets:
            candidate["cohort"] = (
                "copy_ready"
                if candidate["copy_ready_gate_status"] == "promoted"
                else ("shadow" if candidate["shadow_gate_status"] == "promoted" else "discovery")
            )
            persisted_rows.append(
                {
                    "address": candidate["address"],
                    "source_type": candidate["source_type"],
                    "primary_source": candidate["primary_source"],
                    "source_labels": json.dumps(candidate["source_labels"], ensure_ascii=True),
                    "source_count": candidate["source_count"],
                    "discovery_bucket": candidate["discovery_bucket"],
                    "cohort": candidate["cohort"],
                    "discovery_rank": candidate["discovery_rank"],
                    "shadow_rank": candidate["shadow_rank"],
                    "copy_ready_rank": candidate["copy_ready_rank"],
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
                    "closed_shadow_trades": candidate["closed_shadow_trades"],
                    "shadow_pnl": candidate["shadow_pnl"],
                    "shadow_edge": candidate["shadow_edge"],
                    "worst_drawdown_pct": candidate["worst_drawdown_pct"],
                    "shadow_gate_status": candidate["shadow_gate_status"],
                    "shadow_gate_reason": candidate["shadow_gate_reason"],
                    "copy_ready_gate_status": candidate["copy_ready_gate_status"],
                    "copy_ready_gate_reason": candidate["copy_ready_gate_reason"],
                    "shadow_eligible": candidate["shadow_eligible"],
                    "copy_ready_eligible": candidate["copy_ready_eligible"],
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

        provenance_summary = {
            "multi_source_wallets": sum(1 for row in discovery_wallets if int(row["source_count"]) > 1),
            "single_source_wallets": sum(1 for row in discovery_wallets if int(row["source_count"]) == 1),
            "seed_only_wallets": sum(1 for row in discovery_wallets if row["_normalized_source_labels"] == {"static_seed"}),
        }

        positive_shadow_wallets = sum(1 for row in shadow_wallet_table if row["shadow_edge"] > 0)
        closed_shadow_trades = sum(int(row["closed_shadow_trades"]) for row in shadow_wallet_table)
        net_shadow_edge = round(sum(float(row["shadow_edge"]) for row in shadow_wallet_table), 4)
        net_shadow_pnl = round(sum(float(row["shadow_pnl"]) for row in shadow_wallet_table), 4)
        worst_drawdown = round(min([float(row["worst_drawdown_pct"]) for row in shadow_wallet_table] or [0.0]), 4)

        shadow_promotion_summary = {
            "eligible_wallets": len(shadow_eligible_wallets),
            "promoted_wallets": len(shadow_wallet_table),
            "blocked_wallets": sum(1 for row in discovery_wallets if row["shadow_gate_status"] == "blocked"),
            "blocker_counts": [
                {"reason": reason, "count": count}
                for reason, count in sorted(shadow_blockers.items())
            ],
        }

        clean_discovery_rows = []
        for row in discovery_wallets[:12]:
            clean_discovery_rows.append(
                {key: value for key, value in row.items() if not key.startswith("_")}
            )

        clean_shadow_rows = [{key: value for key, value in row.items() if not key.startswith("_")} for row in shadow_wallet_table]
        clean_copy_ready_rows = [{key: value for key, value in row.items() if not key.startswith("_")} for row in copy_ready_wallets]

        return {
            "discovery_wallet_summary": {
                "tracked_wallets": len(discovery_wallets),
                "discovery_pool_target": self.settings.discovery_pool_size,
                "shadow_pool_target": self.settings.shadow_pool_size,
                "copy_ready_target": self.settings.copy_ready_size,
                "crypto_specialists": sum(1 for row in discovery_wallets if row["specialization"] == "CRYPTO"),
                "promoted_to_shadow": len(shadow_wallet_table),
                "persisted_wallet_snapshots": len(persisted_rows),
            },
            "discovery_source_summary": discovery_source_summary,
            "shadow_wallet_summary": {
                "shadow_wallets": len(shadow_wallet_table),
                "wallets_with_shadow_actions": sum(1 for row in shadow_wallet_table if row["closed_shadow_trades"] > 0),
                "closed_shadow_trades": closed_shadow_trades,
                "positive_shadow_wallets": positive_shadow_wallets,
            },
            "shadow_promotion_summary": shadow_promotion_summary,
            "copy_ready_wallet_summary": {
                "copy_ready_wallets": len(copy_ready_wallets),
                "copy_ready_target": self.settings.copy_ready_size,
                "minimum_shadow_trades": 3,
                "positive_shadow_edge_wallets": positive_shadow_wallets,
            },
            "wallet_provenance_summary": provenance_summary,
            "shadow_edge_summary": {
                "evaluation_window_days": self.settings.shadow_window_days,
                "net_shadow_edge": net_shadow_edge,
                "net_shadow_pnl": net_shadow_pnl,
                "worst_drawdown_pct": worst_drawdown,
                "shadow_ready": net_shadow_edge > 0 and len(copy_ready_wallets) > 0,
            },
            "recent_shadow_actions": recent_shadow_actions,
            "wallet_consistency_table": clean_discovery_rows,
            "shadow_wallet_table": clean_shadow_rows,
            "copy_ready_wallets": clean_copy_ready_rows,
        }
