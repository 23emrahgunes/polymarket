from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .config import BinanceTechnicalSettings
from .repository import BinanceTechnicalRepository


def _parse_inputs(raw: str | None) -> dict[str, Any]:
    if raw in (None, ""):
        return {}
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _is_acceptance_decision(row: Any) -> bool:
    inputs = _parse_inputs(row["inputs_json"] if "inputs_json" in row.keys() else None)
    return inputs.get("acceptance_fixture") is True or str(inputs.get("sample_kind") or "") == "acceptance_fixture"


def _is_acceptance_position(row: Any) -> bool:
    return (
        str(row["sample_kind"] or "") == "acceptance_fixture"
        or str(row["source_signal"] or "") == "binance_acceptance_fixture"
    )


def _technical_acceptance_summary(decisions: list[Any], positions: list[Any]) -> dict[str, Any]:
    def _match(*, venue: str, action: str, direction: str | None = None, reason: str | None = None) -> Any | None:
        for row in decisions:
            inputs = _parse_inputs(row["inputs_json"])
            if str(row["venue"] or "") != venue:
                continue
            if str(row["action"] or "").lower() != action:
                continue
            if direction and str(inputs.get("signal_direction") or "").upper() != direction:
                continue
            if reason and reason not in str(row["reason"] or ""):
                continue
            return row
        return None

    futures_long = _match(venue="binance_futures", action="execute", direction="LONG")
    futures_short = _match(venue="binance_futures", action="execute", direction="SHORT")
    spot_long = _match(venue="binance_spot", action="execute", direction="LONG")
    spot_short_reject = _match(
        venue="binance_spot",
        action="reject",
        direction="SHORT",
        reason="spot_short_not_supported",
    )
    latest_ts = ""
    for row in decisions:
        candidate = str(row["occurred_at"] or "")
        if candidate > latest_ts:
            latest_ts = candidate
    return {
        "last_acceptance_at": latest_ts,
        "all_checks_passed": bool(futures_long and futures_short and spot_long and spot_short_reject),
        "futures_long_execute": bool(futures_long),
        "futures_long_execute_id": int(futures_long["id"] or 0) if futures_long else 0,
        "futures_short_execute": bool(futures_short),
        "futures_short_execute_id": int(futures_short["id"] or 0) if futures_short else 0,
        "spot_long_execute": bool(spot_long),
        "spot_long_execute_id": int(spot_long["id"] or 0) if spot_long else 0,
        "spot_short_reject": bool(spot_short_reject),
        "spot_short_reject_id": int(spot_short_reject["id"] or 0) if spot_short_reject else 0,
        "acceptance_decision_rows": len(decisions),
        "acceptance_open_positions": len(positions),
    }


def _technical_runtime_acceptance_summary(decisions: list[Any], positions: list[Any]) -> dict[str, Any]:
    def _match(*, venue: str, action: str, direction: str | None = None, reason: str | None = None) -> Any | None:
        for row in decisions:
            inputs = _parse_inputs(row["inputs_json"] if "inputs_json" in row.keys() else None)
            if str(row["venue"] or "") != venue:
                continue
            if str(row["action"] or "").lower() != action:
                continue
            if direction and str(inputs.get("signal_direction") or "").upper() != direction:
                continue
            if reason and reason not in str(row["reason"] or ""):
                continue
            return row
        return None

    futures_long = _match(venue="binance_futures", action="execute", direction="LONG")
    futures_short = _match(venue="binance_futures", action="execute", direction="SHORT")
    spot_long = _match(venue="binance_spot", action="execute", direction="LONG")
    spot_short_reject = _match(
        venue="binance_spot",
        action="reject",
        direction="SHORT",
        reason="spot_short_not_supported",
    )
    latest_ts = ""
    for row in decisions:
        candidate = str(row["occurred_at"] or "")
        if candidate and candidate > latest_ts:
            latest_ts = candidate

    all_checks_passed = bool(futures_long and futures_short and spot_long and spot_short_reject)
    if not decisions:
        reason = "no_runtime_decisions"
    elif all_checks_passed:
        reason = "runtime_all_paths_observed"
    else:
        reason = "runtime_paths_incomplete"

    return {
        "last_runtime_at": latest_ts,
        "all_checks_passed": all_checks_passed,
        "runtime_futures_long_execute": bool(futures_long),
        "runtime_futures_long_execute_id": int(futures_long["id"] or 0) if futures_long else 0,
        "runtime_futures_short_execute": bool(futures_short),
        "runtime_futures_short_execute_id": int(futures_short["id"] or 0) if futures_short else 0,
        "runtime_spot_long_execute": bool(spot_long),
        "runtime_spot_long_execute_id": int(spot_long["id"] or 0) if spot_long else 0,
        "runtime_spot_short_reject": bool(spot_short_reject),
        "runtime_spot_short_reject_id": int(spot_short_reject["id"] or 0) if spot_short_reject else 0,
        "runtime_decision_rows": len(decisions),
        "runtime_open_positions": len(positions),
        "reason": reason,
    }


@dataclass(slots=True)
class BinanceTechnicalService:
    settings: BinanceTechnicalSettings
    repository: BinanceTechnicalRepository

    def build_summary(self) -> dict[str, Any]:
        trades = self.repository.fetch_fresh_trade_rows(self.settings.fresh_window_days)
        all_decisions = self.repository.fetch_technical_decision_rows(self.settings.fresh_window_days)
        all_open_positions = self.repository.fetch_open_positions()
        acceptance_decisions = [row for row in all_decisions if _is_acceptance_decision(row)]
        acceptance_positions = [row for row in all_open_positions if _is_acceptance_position(row)]
        decisions = [row for row in all_decisions if not _is_acceptance_decision(row)]
        open_positions = [row for row in all_open_positions if not _is_acceptance_position(row)]
        venues = ("binance_futures", "binance_spot")

        closed_trades = [row for row in trades if str(row["status"] or "").upper().startswith("CLOSED")]
        total_pnl = round(sum(float(row["pnl"] or 0.0) for row in closed_trades), 4)
        wins = sum(1 for row in closed_trades if float(row["pnl"] or 0.0) > 0)
        losses = sum(1 for row in closed_trades if float(row["pnl"] or 0.0) < 0)
        legacy_positions = [
            row
            for row in open_positions
            if str(row["strategy_profile"] or "") != "binance_technical_sampling"
            or str(row["sample_kind"] or "") != "live_paper"
        ]
        fresh_positions = [row for row in open_positions if row not in legacy_positions]

        reject_breakdown: dict[str, int] = {}
        avg_scores: list[float] = []
        avg_thresholds: list[float] = []
        execute_count_by_venue = {venue: 0 for venue in venues}
        for row in decisions:
            action = str(row["action"] or "").lower()
            venue = str(row["venue"] or "")
            if action == "execute" and venue in execute_count_by_venue:
                execute_count_by_venue[venue] += 1
            if action == "reject":
                for part in [piece.strip() for piece in str(row["reason"] or "").split(",") if piece.strip()]:
                    reject_breakdown[part] = reject_breakdown.get(part, 0) + 1
            score = row["decision_score"]
            threshold = row["threshold"]
            if score is not None:
                avg_scores.append(float(score))
            if threshold is not None:
                avg_thresholds.append(float(threshold))

        sorted_rejects = [
            {"reason": reason, "count": count}
            for reason, count in sorted(reject_breakdown.items(), key=lambda item: (-item[1], item[0]))
        ]

        venue_summaries: dict[str, dict[str, Any]] = {}
        for venue in venues:
            venue_trades = [row for row in trades if str(row["venue"] or "") == venue]
            venue_closed = [row for row in closed_trades if str(row["venue"] or "") == venue]
            venue_wins = sum(1 for row in venue_closed if float(row["pnl"] or 0.0) > 0)
            venue_summaries[venue] = {
                "fresh_trade_count": len(venue_trades),
                "fresh_closed_trades": len(venue_closed),
                "net_pnl": round(sum(float(row["pnl"] or 0.0) for row in venue_closed), 4),
                "gross_wins": round(sum(max(float(row["pnl"] or 0.0), 0.0) for row in venue_closed), 4),
                "gross_losses": round(sum(min(float(row["pnl"] or 0.0), 0.0) for row in venue_closed), 4),
                "win_rate": round((venue_wins / len(venue_closed)) * 100.0, 1) if venue_closed else None,
                "execute_count": execute_count_by_venue.get(venue, 0),
            }

        oldest_open_minutes = 0.0
        if open_positions:
            timestamps = []
            for row in open_positions:
                opened_at = str(row["opened_at"] or "").strip()
                if not opened_at:
                    continue
                try:
                    timestamps.append(datetime.strptime(opened_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc))
                except ValueError:
                    continue
            if timestamps:
                oldest_open_minutes = round((datetime.now(timezone.utc) - min(timestamps)).total_seconds() / 60.0, 2)

        return {
            "fresh_technical_summary": {
                "fresh_window_days": self.settings.fresh_window_days,
                "symbols": self.settings.symbols,
                "fresh_trade_count": len(trades),
                "fresh_closed_trades": len(closed_trades),
                "fresh_open_trades": len(trades) - len(closed_trades),
                "wins": wins,
                "losses": losses,
                "fresh_execute_count": sum(execute_count_by_venue.values()),
                "enabled_venues": self.settings.enabled_venues,
                "execute_count_by_venue": execute_count_by_venue,
            },
            "fresh_pnl_summary_7d": {
                "fresh_window_days": self.settings.fresh_window_days,
                "fresh_trade_count": len(trades),
                "fresh_closed_trades": len(closed_trades),
                "net_pnl": total_pnl,
                "gross_wins": round(sum(max(float(row["pnl"] or 0.0), 0.0) for row in closed_trades), 4),
                "gross_losses": round(sum(min(float(row["pnl"] or 0.0), 0.0) for row in closed_trades), 4),
                "win_rate": round((wins / len(closed_trades)) * 100.0, 1) if closed_trades else None,
                "venues": venue_summaries,
            },
            "technical_score_summary": {
                "decision_rows": len(decisions),
                "avg_score": round(sum(avg_scores) / len(avg_scores), 4) if avg_scores else 0.0,
                "avg_threshold": round(sum(avg_thresholds) / len(avg_thresholds), 4) if avg_thresholds else 0.0,
                "scored_rows": len(avg_scores),
            },
            "technical_reject_breakdown": sorted_rejects,
            "position_pressure_summary": {
                "open_positions": len(open_positions),
                "fresh_open_positions": len(fresh_positions),
                "legacy_open_positions": len(legacy_positions),
                "open_notional_usd": round(sum(float(row["notional_usd"] or 0.0) for row in open_positions), 4),
                "open_unrealized_pnl": round(sum(float(row["unrealized_pnl"] or 0.0) for row in open_positions), 4),
                "oldest_open_position_minutes": oldest_open_minutes,
            },
            "legacy_position_summary": {
                "legacy_open_positions": len(legacy_positions),
                "strict_fresh_positions": len(fresh_positions),
                "legacy_symbols": sorted({str(row["symbol_or_market_id"] or "") for row in legacy_positions if str(row["symbol_or_market_id"] or "").strip()}),
            },
            "technical_acceptance_summary": _technical_acceptance_summary(acceptance_decisions, acceptance_positions),
            "technical_runtime_acceptance_summary": _technical_runtime_acceptance_summary(decisions, open_positions),
        }
