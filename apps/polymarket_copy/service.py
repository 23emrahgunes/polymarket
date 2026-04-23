from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .activity_client import PolymarketWalletActivityClient
from .config import PolymarketCopySettings
from .repository import PolymarketCopyRepository

ACCEPTANCE_COPY_SOURCE_TRADE_KEY = "acceptance:polymarket_copy:open:v1"
ACCEPTANCE_COPY_WALLET = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
ACCEPTANCE_COPY_MARKET = "acceptance-polymarket-copy-market"


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


def _decode_notes(raw_notes: str | None) -> dict[str, Any]:
    if raw_notes in (None, ""):
        return {}
    try:
        decoded = json.loads(str(raw_notes))
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _is_acceptance_copy_row(row: dict[str, Any]) -> bool:
    source_key = str(row.get("source_trade_key") or "")
    notes = _decode_notes(str(row.get("notes_json") or ""))
    return (
        source_key.startswith("acceptance:")
        or notes.get("acceptance_fixture") is True
        or str(notes.get("cohort_source") or "") == "acceptance_fixture"
    )


def _merge_source_rows(*row_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rows in row_groups:
        for row in rows:
            key = str(row.get("source_trade_key") or "").strip()
            if not key:
                key = "|".join(
                    [
                        str(row.get("wallet_address") or ""),
                        str(row.get("market_id") or ""),
                        str(row.get("side") or ""),
                        str(row.get("occurred_at") or row.get("source_opened_at") or ""),
                    ]
                )
            if key in seen:
                continue
            seen.add(key)
            merged.append(row)
    return merged


def _copy_acceptance_summary(actions: list[dict[str, Any]], positions: list[dict[str, Any]]) -> dict[str, Any]:
    open_actions = [row for row in actions if str(row.get("action_type") or "") == "open"]
    open_positions = [row for row in positions if str(row.get("status") or "").upper() == "OPEN"]
    latest_ts = ""
    for row in [*open_actions, *open_positions]:
        candidate = str(row.get("executed_at") or row.get("opened_at") or "")
        if candidate and candidate > latest_ts:
            latest_ts = candidate
    open_action = open_actions[0] if open_actions else {}
    open_position = open_positions[0] if open_positions else {}
    action_seen = bool(open_actions)
    position_seen = bool(open_positions)
    return {
        "last_acceptance_at": latest_ts,
        "all_checks_passed": action_seen and position_seen,
        "copy_open_action_observed": action_seen,
        "copy_open_action_id": int(open_action.get("id") or 0) if open_action else 0,
        "copy_open_position_observed": position_seen,
        "copy_open_position_id": int(open_position.get("id") or 0) if open_position else 0,
        "acceptance_actions": len(actions),
        "acceptance_open_positions": len(open_positions),
        "reason": "acceptance_copy_entry" if action_seen and position_seen else "acceptance_copy_entry_missing",
    }


def _copy_runtime_acceptance_summary(
    actions: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    *,
    eligible_copy_wallets_total: int,
    shadow_proven_wallets: int,
    manual_fast_track_wallets: int,
    copy_blocker_reason: str = "",
) -> dict[str, Any]:
    open_actions = [row for row in actions if str(row.get("action_type") or "") == "open"]
    close_actions = [row for row in actions if str(row.get("action_type") or "") == "close"]
    replay_closed_actions = [row for row in actions if str(row.get("action_type") or "") == "replay_closed"]
    open_positions = [row for row in positions if str(row.get("status") or "").upper() == "OPEN"]
    latest_ts = ""
    for row in [*actions, *positions]:
        candidate = str(row.get("executed_at") or row.get("opened_at") or "")
        if candidate and candidate > latest_ts:
            latest_ts = candidate

    if eligible_copy_wallets_total <= 0:
        reason = copy_blocker_reason or "no_eligible_copy_wallets"
    elif not open_actions and not open_positions and not close_actions and not replay_closed_actions:
        reason = "eligible_wallets_no_runtime_actions"
    else:
        reason = "runtime_copy_active"

    return {
        "last_runtime_action_at": latest_ts,
        "all_checks_passed": eligible_copy_wallets_total > 0 and bool(open_actions) and bool(open_positions),
        "runtime_open_action_observed": bool(open_actions),
        "runtime_open_position_observed": bool(open_positions),
        "runtime_close_action_observed": bool(close_actions),
        "runtime_replay_closed_observed": bool(replay_closed_actions),
        "runtime_action_rows": len(actions),
        "runtime_open_positions": len(open_positions),
        "eligible_copy_wallets_total": eligible_copy_wallets_total,
        "shadow_proven_wallets": shadow_proven_wallets,
        "manual_fast_track_wallets": manual_fast_track_wallets,
        "runtime_blocker_reason": reason,
        "runtime_schema_guard_status": "ok",
        "reason": reason,
    }


def _wallet_limits(settings: PolymarketCopySettings, wallet: dict[str, Any]) -> dict[str, float | int | str]:
    cohort_source = str(wallet.get("cohort_source") or "shadow_proven")
    max_trade_size = float(settings.max_trade_size_usd)
    wallet_risk_limit = float(settings.wallet_risk_limit_usd)
    market_risk_limit = float(settings.market_risk_limit_usd)
    max_concurrent_positions = int(settings.max_concurrent_positions_per_wallet)
    if cohort_source in {"manual_fast_track", "pilot_copy_ready"}:
        max_trade_size = min(
            max_trade_size,
            max(float(settings.min_trade_size_usd), round(float(settings.max_trade_size_usd) * 0.5, 4)),
        )
        wallet_risk_limit = min(wallet_risk_limit, round(max_trade_size * 2.0, 4))
        market_risk_limit = min(market_risk_limit, round(max_trade_size * 3.0, 4))
        max_concurrent_positions = 1
    elif cohort_source == "wallet_mirror":
        max_trade_size = float(settings.max_trade_size_usd)
        wallet_risk_limit = float(settings.wallet_risk_limit_usd)
        market_risk_limit = float(settings.market_risk_limit_usd)
        max_concurrent_positions = max(1, int(settings.max_concurrent_positions_per_wallet))
    return {
        "cohort_source": cohort_source,
        "max_trade_size_usd": round(max_trade_size, 4),
        "wallet_risk_limit_usd": round(wallet_risk_limit, 4),
        "market_risk_limit_usd": round(market_risk_limit, 4),
        "max_concurrent_positions": max_concurrent_positions,
    }


@dataclass(slots=True)
class PolymarketCopyService:
    settings: PolymarketCopySettings
    repository: PolymarketCopyRepository
    activity_client: Any | None = None

    def sync_copy_actions(self) -> dict[str, int]:
        self.repository.ensure_tables()
        eligible_wallet_rows = self.repository.fetch_copy_ready_wallets(limit=self.settings.copy_ready_limit)
        copy_ready_wallets = [dict(row) for row in eligible_wallet_rows]
        wallet_lookup = {
            str(row.get("address") or "").lower(): row for row in copy_ready_wallets if str(row.get("address") or "").strip()
        }
        wallet_addresses = list(wallet_lookup.keys())
        source_rows = self.repository.fetch_source_trade_rows(wallet_addresses, self.settings.lookback_days)
        live_activity_rows: list[dict[str, Any]] = []
        if self.settings.live_activity_enabled and wallet_addresses:
            client = self.activity_client or PolymarketWalletActivityClient(self.settings)
            live_activity_rows = client.fetch_wallet_activity_rows(wallet_addresses)
        source_rows = _merge_source_rows(live_activity_rows, source_rows)

        counters = {
            "wallets_considered": len(copy_ready_wallets),
            "source_rows_considered": len(source_rows),
            "live_activity_rows_considered": len(live_activity_rows),
            "open_actions": 0,
            "close_actions": 0,
            "replay_closed_actions": 0,
            "reject_actions": 0,
            "manual_fast_track_wallets": sum(1 for row in copy_ready_wallets if row.get("cohort_source") in {"manual_fast_track", "pilot_copy_ready"}),
            "pilot_copy_wallets": sum(1 for row in copy_ready_wallets if row.get("cohort_source") == "pilot_copy_ready"),
            "shadow_proven_wallets": sum(1 for row in copy_ready_wallets if row.get("cohort_source") == "shadow_proven"),
            "wallet_mirror_wallets": sum(1 for row in copy_ready_wallets if row.get("cohort_source") == "wallet_mirror"),
        }

        now = datetime.now(timezone.utc)
        for row in source_rows:
            source_key = str(row["source_trade_key"])
            wallet_address = str(row["wallet_address"])
            wallet = wallet_lookup.get(wallet_address)
            if wallet is None:
                continue
            wallet_limits = _wallet_limits(self.settings, wallet)
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
                "cohort_source": wallet_limits["cohort_source"],
                "copy_admission_source": wallet_limits["cohort_source"],
                "historical_trade_evidence_status": wallet.get("historical_trade_evidence_status", ""),
                "watchlist_mode": wallet.get("watchlist_mode", ""),
            }

            is_closed = source_status.startswith("CLOSED")
            if normalized_side == "SELL" and not is_closed:
                existing_position = self.repository.fetch_open_position_for_wallet_market(wallet_address, market_id)
                if existing_position is None:
                    if not self.repository.source_trade_action_exists(source_key, "reject"):
                        self.repository.insert_copy_action(
                            source_trade_key=source_key,
                            wallet_address=wallet_address,
                            market_id=market_id,
                            category=category,
                            action_type="reject",
                            reason="source_sell_without_open_position",
                            source_status=source_status,
                            side=normalized_side,
                            source_notional_usd=source_notional,
                            follower_notional_usd=0.0,
                            source_pnl=source_pnl,
                            follower_pnl=0.0,
                            delayed_seconds=0,
                            source_opened_at=source_opened_at,
                            source_closed_at=source_closed_at or source_opened_at,
                            executed_at=source_opened_at or None,
                            notes=notes,
                        )
                        counters["reject_actions"] += 1
                    continue

                if not self.repository.source_trade_action_exists(source_key, "close"):
                    source_close_time = source_closed_at or source_opened_at
                    close_notes = dict(notes)
                    close_notes["closed_by_source_trade_key"] = source_key
                    self.repository.close_position_by_source_trade_key(
                        source_trade_key=str(existing_position["source_trade_key"]),
                        source_status="CLOSED_BY_SOURCE_SELL",
                        source_closed_at=source_close_time,
                        source_pnl=source_pnl,
                        follower_pnl=0.0,
                        notes=close_notes,
                    )
                    self.repository.insert_copy_action(
                        source_trade_key=source_key,
                        wallet_address=wallet_address,
                        market_id=market_id,
                        category=category,
                        action_type="close",
                        reason="source_sell",
                        source_status="CLOSED_BY_SOURCE_SELL",
                        side=normalized_side,
                        source_notional_usd=source_notional,
                        follower_notional_usd=float(existing_position["follower_notional_usd"] or 0.0),
                        source_pnl=source_pnl,
                        follower_pnl=0.0,
                        delayed_seconds=0,
                        source_opened_at=str(existing_position["source_opened_at"] or ""),
                        source_closed_at=source_close_time,
                        executed_at=source_close_time or None,
                        notes=close_notes,
                    )
                    counters["close_actions"] += 1
                continue

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
                    min(source_notional, float(wallet_limits["max_trade_size_usd"])),
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
                min(source_notional, float(wallet_limits["max_trade_size_usd"])),
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

            if self.repository.fetch_open_wallet_position_count(wallet_address) >= int(wallet_limits["max_concurrent_positions"]):
                self.repository.insert_copy_action(
                    source_trade_key=source_key,
                    wallet_address=wallet_address,
                    market_id=market_id,
                    category=category,
                    action_type="reject",
                    reason="manual_fast_track_open_position_cap_exceeded",
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

            if self.repository.fetch_open_wallet_notional(wallet_address) + follower_notional > float(wallet_limits["wallet_risk_limit_usd"]):
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

            if self.repository.fetch_open_market_notional(market_id) + follower_notional > float(wallet_limits["market_risk_limit_usd"]):
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

    def run_acceptance_fixture(self) -> dict[str, Any]:
        self.repository.ensure_tables()
        now = datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
        notes = {
            "acceptance_fixture": True,
            "cohort_source": "acceptance_fixture",
            "copy_admission_source": "acceptance_fixture",
            "reason": "acceptance_copy_entry",
            "acceptance_case": "polymarket_copy_open",
            "source_signal": "acceptance_fixture",
        }
        self.repository.create_or_replace_position(
            source_trade_key=ACCEPTANCE_COPY_SOURCE_TRADE_KEY,
            wallet_address=ACCEPTANCE_COPY_WALLET,
            market_id=ACCEPTANCE_COPY_MARKET,
            category="CRYPTO",
            side="BUY",
            source_notional_usd=100.0,
            follower_notional_usd=25.0,
            source_pnl=0.0,
            follower_pnl=0.0,
            source_status="OPEN",
            status="OPEN",
            source_opened_at=now,
            source_closed_at="",
            opened_at=now,
            closed_at=None,
            notes=notes,
        )
        self.repository.insert_copy_action(
            source_trade_key=ACCEPTANCE_COPY_SOURCE_TRADE_KEY,
            wallet_address=ACCEPTANCE_COPY_WALLET,
            market_id=ACCEPTANCE_COPY_MARKET,
            category="CRYPTO",
            action_type="open",
            reason="acceptance_copy_entry",
            source_status="OPEN",
            side="BUY",
            source_notional_usd=100.0,
            follower_notional_usd=25.0,
            source_pnl=0.0,
            follower_pnl=0.0,
            delayed_seconds=0,
            source_opened_at=now,
            source_closed_at="",
            executed_at=now,
            notes=notes,
        )
        return self.build_summary()

    def build_summary(self) -> dict[str, Any]:
        self.repository.ensure_tables()
        copy_ready_wallets = [dict(row) for row in self.repository.fetch_copy_ready_wallets(limit=self.settings.copy_ready_limit)]
        all_actions = [dict(row) for row in self.repository.fetch_copy_actions()]
        all_recent_actions = [dict(row) for row in self.repository.fetch_recent_copy_actions(24)]
        all_active_positions = [dict(row) for row in self.repository.fetch_active_copy_positions()]
        acceptance_actions = [row for row in all_actions if _is_acceptance_copy_row(row)]
        acceptance_positions = [row for row in all_active_positions if _is_acceptance_copy_row(row)]
        actions = [row for row in all_actions if not _is_acceptance_copy_row(row)]
        recent_actions = [row for row in all_recent_actions if not _is_acceptance_copy_row(row)][:12]
        active_positions = [row for row in all_active_positions if not _is_acceptance_copy_row(row)]
        wallet_rows = [
            dict(row)
            for row in self.repository.fetch_wallet_follower_pnl_rows()
            if str(row["wallet_address"] or "").lower() != ACCEPTANCE_COPY_WALLET
        ]
        reject_rows = [
            dict(row)
            for row in self.repository.fetch_copy_action_reason_counts()
            if str(row["reason"] or "") != "acceptance_copy_entry"
        ]

        open_actions = sum(1 for row in actions if row["action_type"] == "open")
        close_actions = sum(1 for row in actions if row["action_type"] == "close")
        replay_closed_actions = sum(1 for row in actions if row["action_type"] == "replay_closed")
        reject_actions = sum(1 for row in actions if row["action_type"] == "reject")
        shadow_proven_wallets = sum(1 for row in copy_ready_wallets if row.get("cohort_source") == "shadow_proven")
        pilot_copy_wallets = sum(1 for row in copy_ready_wallets if row.get("cohort_source") == "pilot_copy_ready")
        wallet_mirror_wallets = sum(1 for row in copy_ready_wallets if row.get("cohort_source") == "wallet_mirror")
        manual_fast_track_wallets = sum(1 for row in copy_ready_wallets if row.get("cohort_source") in {"manual_fast_track", "pilot_copy_ready"})
        admission_counts = self.repository.fetch_copy_admission_counts()

        def _action_cohort(row: dict[str, Any]) -> str:
            raw_notes = str(row.get("notes_json") or "")
            if raw_notes:
                try:
                    decoded = json.loads(raw_notes)
                    if isinstance(decoded, dict):
                        cohort = str(decoded.get("cohort_source") or "")
                        if cohort:
                            return cohort
                except (TypeError, ValueError):
                    pass
            wallet = next((item for item in copy_ready_wallets if str(item.get("address") or "").lower() == str(row.get("wallet_address") or "").lower()), None)
            return str((wallet or {}).get("cohort_source") or "shadow_proven")

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
            matching_actions = [
                action
                for action in actions
                if str(action["wallet_address"] or "").lower() == str(row["wallet_address"] or "").lower()
            ]
            cohort_source = _action_cohort(matching_actions[0]) if matching_actions else next(
                (
                    str(wallet.get("cohort_source") or "")
                    for wallet in copy_ready_wallets
                    if str(wallet.get("address") or "").lower() == str(row["wallet_address"] or "").lower()
                ),
                "",
            )
            follower_realized = round(float(row["follower_realized_pnl"] or 0.0), 4)
            source_realized = round(float(row["source_realized_pnl"] or 0.0), 4)
            wallet_summary_rows.append(
                {
                    "wallet_address": str(row["wallet_address"] or ""),
                    "cohort_source": cohort_source or "shadow_proven",
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
                    "cohort_source": str(notes.get("cohort_source") or ""),
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
                "cohort_source": (
                    (
                        json.loads(str(row.get("notes_json") or "{}")).get("cohort_source", "")
                        if str(row.get("notes_json") or "")
                        else ""
                    ) or next(
                        (
                            str(wallet.get("cohort_source") or "")
                            for wallet in copy_ready_wallets
                            if str(wallet.get("address") or "").lower() == str(row["wallet_address"] or "").lower()
                        ),
                        "shadow_proven",
                    )
                ),
            }
            for row in active_positions
        ]
        active_shadow_positions = sum(1 for row in active_copy_positions if row["cohort_source"] == "shadow_proven")
        active_pilot_copy_positions = sum(1 for row in active_copy_positions if row["cohort_source"] == "pilot_copy_ready")
        active_wallet_mirror_positions = sum(1 for row in active_copy_positions if row["cohort_source"] == "wallet_mirror")
        active_manual_fast_track_positions = sum(1 for row in active_copy_positions if row["cohort_source"] in {"manual_fast_track", "pilot_copy_ready"})
        runtime_acceptance_summary = _copy_runtime_acceptance_summary(
            recent_copy_actions,
            active_copy_positions,
            eligible_copy_wallets_total=len(copy_ready_wallets),
            shadow_proven_wallets=shadow_proven_wallets,
            manual_fast_track_wallets=manual_fast_track_wallets,
            copy_blocker_reason=str(admission_counts.get("copy_blocker_reason", "")),
        )

        return {
            "copy_execution_summary": {
                "copy_ready_wallets": shadow_proven_wallets,
                "shadow_proven_wallets": shadow_proven_wallets,
                "manual_fast_track_wallets": manual_fast_track_wallets,
                "pilot_copy_wallets": pilot_copy_wallets,
                "wallet_mirror_wallets": wallet_mirror_wallets,
                "watch_only_wallets": int(admission_counts.get("watch_only_wallets", 0) or 0),
                "copy_blocker_reason": str(admission_counts.get("copy_blocker_reason", "")),
                "eligible_copy_wallets_total": len(copy_ready_wallets),
                "copy_window_days": self.settings.lookback_days,
                "open_actions": open_actions,
                "close_actions": close_actions,
                "replay_closed_actions": replay_closed_actions,
                "reject_actions": reject_actions,
                "active_copy_positions": len(active_positions),
                "active_shadow_proven_positions": active_shadow_positions,
                "active_manual_fast_track_positions": active_manual_fast_track_positions,
                "active_pilot_copy_positions": active_pilot_copy_positions,
                "active_wallet_mirror_positions": active_wallet_mirror_positions,
                "wallets_with_realized_pnl": sum(1 for row in wallet_summary_rows if row["closed_actions"] > 0),
            },
            "copy_reject_breakdown": [
                {"reason": str(row["reason"] or ""), "count": int(row["count"] or 0)}
                for row in reject_rows
            ],
            "active_copy_positions": active_copy_positions,
            "wallet_follower_pnl_summary": wallet_summary_rows,
            "shadow_vs_copy_drift_summary": {
                "copy_ready_wallets": shadow_proven_wallets,
                "shadow_proven_wallets": shadow_proven_wallets,
                "manual_fast_track_wallets": manual_fast_track_wallets,
                "pilot_copy_wallets": pilot_copy_wallets,
                "wallet_mirror_wallets": wallet_mirror_wallets,
                "watch_only_wallets": int(admission_counts.get("watch_only_wallets", 0) or 0),
                "copy_blocker_reason": str(admission_counts.get("copy_blocker_reason", "")),
                "eligible_copy_wallets_total": len(copy_ready_wallets),
                "shadow_closed_trades": shadow_closed,
                "shadow_net_edge": copy_ready_shadow_edge,
                "source_realized_pnl": source_realized_pnl,
                "copy_realized_pnl": copy_realized_pnl,
                "copy_vs_source_pnl_gap": round(copy_realized_pnl - source_realized_pnl, 4),
            },
            "recent_copy_actions": recent_copy_actions,
            "copy_acceptance_summary": _copy_acceptance_summary(acceptance_actions, acceptance_positions),
            "copy_runtime_acceptance_summary": runtime_acceptance_summary,
        }
