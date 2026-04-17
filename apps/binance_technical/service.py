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


@dataclass(slots=True)
class BinanceTechnicalService:
    settings: BinanceTechnicalSettings
    repository: BinanceTechnicalRepository

    def build_summary(self) -> dict[str, Any]:
        trades = self.repository.fetch_fresh_trade_rows(self.settings.fresh_window_days)
        decisions = self.repository.fetch_technical_decision_rows(self.settings.fresh_window_days)
        open_positions = self.repository.fetch_open_positions()

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
        for row in decisions:
            action = str(row["action"] or "").lower()
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
                "fresh_execute_count": sum(1 for row in decisions if str(row["action"] or "").lower() == "execute"),
            },
            "fresh_pnl_summary_7d": {
                "fresh_window_days": self.settings.fresh_window_days,
                "net_pnl": total_pnl,
                "gross_wins": round(sum(max(float(row["pnl"] or 0.0), 0.0) for row in closed_trades), 4),
                "gross_losses": round(sum(min(float(row["pnl"] or 0.0), 0.0) for row in closed_trades), 4),
                "win_rate": round((wins / len(closed_trades)) * 100.0, 1) if closed_trades else None,
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
        }
