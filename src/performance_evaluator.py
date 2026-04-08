from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

from src.evaluation_utils import (
    infer_sample_kind_from_db_path,
    is_synthetic_sample,
    normalize_signal_family,
)


DEFAULT_CONFIDENCE_BUCKETS = [
    (0.00, 0.60, "0.00-0.60"),
    (0.60, 0.70, "0.60-0.70"),
    (0.70, 0.80, "0.70-0.80"),
    (0.80, 0.90, "0.80-0.90"),
    (0.90, 1.01, "0.90-1.00"),
]

DEFAULT_WHALE_TRUST_BUCKETS = [
    (0.00, 0.40, "0.00-0.40"),
    (0.40, 0.60, "0.40-0.60"),
    (0.60, 0.80, "0.60-0.80"),
    (0.80, 1.01, "0.80-1.00"),
]


@dataclass(frozen=True)
class PerformanceDataset:
    trades: pd.DataFrame
    positions: pd.DataFrame
    audits: pd.DataFrame
    db_paths: List[str]


def load_performance_dataset(db_paths: Iterable[str]) -> PerformanceDataset:
    trade_frames: list[pd.DataFrame] = []
    position_frames: list[pd.DataFrame] = []
    audit_frames: list[pd.DataFrame] = []
    normalized_paths: list[str] = []

    for raw_path in db_paths:
        db_path = str(Path(raw_path))
        normalized_paths.append(db_path)
        if not Path(db_path).exists():
            continue

        connection = sqlite3.connect(db_path)
        try:
            trade_frames.append(_load_table(connection, "trades", db_path))
            position_frames.append(_load_table(connection, "venue_positions", db_path))
            audit_frames.append(_load_table(connection, "decision_audit", db_path))
        finally:
            connection.close()

    trades = _normalize_trades(pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame())
    positions = _normalize_positions(pd.concat(position_frames, ignore_index=True) if position_frames else pd.DataFrame())
    audits = _normalize_audits(pd.concat(audit_frames, ignore_index=True) if audit_frames else pd.DataFrame())
    return PerformanceDataset(trades=trades, positions=positions, audits=audits, db_paths=normalized_paths)


def analyze_dataset(
    dataset: PerformanceDataset,
    include_synthetic: bool = False,
) -> Dict:
    trades = dataset.trades.copy()
    positions = dataset.positions.copy()
    audits = dataset.audits.copy()

    synthetic_trades = trades[trades["is_synthetic"] == 1].copy()
    synthetic_positions = positions[positions["is_synthetic"] == 1].copy()
    synthetic_audits = audits[audits["is_synthetic"] == 1].copy()

    if not include_synthetic:
        trades = trades[trades["is_synthetic"] == 0].copy()
        positions = positions[positions["is_synthetic"] == 0].copy()
        audits = audits[audits["is_synthetic"] == 0].copy()

    summary = {
        "db_paths": dataset.db_paths,
        "sample_counts": _sample_counts(dataset.trades),
        "what_was_measurable": _what_was_measurable(trades, audits),
        "what_was_not_measurable": _what_was_not_measurable(trades),
    }
    summary["core"] = _core_metrics(trades, positions)
    summary["venue_comparison"] = _group_pnl_summary(trades, positions, "venue")
    summary["category_comparison"] = _group_pnl_summary(trades, positions, "category")
    summary["signal_source_comparison"] = _group_pnl_summary(trades, positions, "signal_family")
    summary["confidence_bucket_performance"] = _bucket_performance(trades, "confidence", DEFAULT_CONFIDENCE_BUCKETS)
    summary["whale_trust_bucket_performance"] = _bucket_performance(trades, "whale_trust_at_entry", DEFAULT_WHALE_TRUST_BUCKETS, include_unknown=True)
    summary["rejection_counts_by_reason"] = _rejection_counts(audits)
    summary["synthetic_appendix"] = {
        "trade_count": int(len(synthetic_trades)),
        "position_count": int(len(synthetic_positions)),
        "audit_count": int(len(synthetic_audits)),
        "core_metrics": _core_metrics(synthetic_trades, synthetic_positions),
    }
    summary["evidence"] = _evidence_summary(dataset.trades)
    return summary


def write_analysis_outputs(summary: Dict, output_dir: str) -> Dict[str, str]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    summary_json_path = output_path / "summary.json"
    summary_csv_path = output_path / "summary.csv"
    rejections_csv_path = output_path / "rejections.csv"
    summary_md_path = output_path / "summary.md"

    summary_json_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    pd.DataFrame([_flatten_for_csv(summary.get("core", {}))]).to_csv(summary_csv_path, index=False)
    pd.DataFrame(summary.get("rejection_counts_by_reason", [])).to_csv(rejections_csv_path, index=False)
    summary_md_path.write_text(_render_summary_markdown(summary), encoding="utf-8")

    return {
        "summary_json": str(summary_json_path),
        "summary_csv": str(summary_csv_path),
        "rejections_csv": str(rejections_csv_path),
        "summary_md": str(summary_md_path),
    }


def _load_table(connection: sqlite3.Connection, table_name: str, db_path: str) -> pd.DataFrame:
    try:
        frame = pd.read_sql_query(f"SELECT * FROM {table_name}", connection)
    except Exception:
        frame = pd.DataFrame()
    if frame.empty:
        return frame
    frame["db_path"] = db_path
    return frame


def _normalize_trades(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(
            columns=[
                "id",
                "venue",
                "category",
                "signal_family",
                "sample_kind",
                "is_synthetic",
                "status",
                "pnl",
                "confidence",
                "entry_spread_pct",
                "slippage_proxy_bps",
                "whale_trust_at_entry",
                "hold_seconds",
                "size",
                "timestamp",
                "db_path",
            ]
        )

    frame = frame.copy()
    _ensure_columns(
        frame,
        {
            "venue": "polymarket",
            "instrument_type": "prediction",
            "source_signal": "runtime",
            "category": None,
            "signal_family": None,
            "sample_kind": None,
            "is_synthetic": None,
            "debug_profile": None,
            "entry_spread_pct": None,
            "slippage_proxy_bps": None,
            "whale_trust_at_entry": None,
            "opened_at": None,
            "closed_at": None,
            "hold_seconds": None,
            "pnl": 0.0,
            "confidence": None,
        },
    )

    frame["category"] = frame.apply(_infer_trade_category, axis=1)
    frame["signal_family"] = frame.apply(
        lambda row: row["signal_family"] if _nonempty(row["signal_family"]) and row["signal_family"] != "unknown" else normalize_signal_family(row["source_signal"]),
        axis=1,
    )
    frame["sample_kind"] = frame.apply(
        lambda row: row["sample_kind"] if _nonempty(row["sample_kind"]) else infer_sample_kind_from_db_path(row["db_path"]),
        axis=1,
    )
    frame["is_synthetic"] = frame.apply(
        lambda row: int(row["is_synthetic"]) if pd.notna(row["is_synthetic"]) else int(is_synthetic_sample(row["sample_kind"])),
        axis=1,
    )
    frame["pnl"] = pd.to_numeric(frame["pnl"], errors="coerce").fillna(0.0)
    frame["confidence"] = pd.to_numeric(frame["confidence"], errors="coerce")
    frame["entry_spread_pct"] = pd.to_numeric(frame["entry_spread_pct"], errors="coerce")
    frame["slippage_proxy_bps"] = pd.to_numeric(frame["slippage_proxy_bps"], errors="coerce")
    frame["whale_trust_at_entry"] = pd.to_numeric(frame["whale_trust_at_entry"], errors="coerce")
    frame["hold_seconds"] = pd.to_numeric(frame["hold_seconds"], errors="coerce")
    frame["size"] = pd.to_numeric(frame["size"], errors="coerce").fillna(0.0)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
    frame["closed_at"] = pd.to_datetime(frame["closed_at"], errors="coerce")
    return frame


def _normalize_positions(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(
            columns=[
                "venue",
                "category",
                "signal_family",
                "sample_kind",
                "is_synthetic",
                "status",
                "notional_usd",
                "unrealized_pnl",
                "realized_pnl",
                "entry_spread_pct",
                "slippage_proxy_bps",
                "db_path",
            ]
        )

    frame = frame.copy()
    _ensure_columns(
        frame,
        {
            "venue": None,
            "instrument_type": None,
            "source_signal": None,
            "category": None,
            "signal_family": None,
            "sample_kind": None,
            "is_synthetic": None,
            "debug_profile": None,
            "entry_spread_pct": None,
            "slippage_proxy_bps": None,
            "whale_trust_at_entry": None,
            "unrealized_pnl": 0.0,
            "realized_pnl": 0.0,
            "status": "OPEN",
            "notional_usd": 0.0,
        },
    )
    frame["category"] = frame.apply(_infer_position_category, axis=1)
    frame["signal_family"] = frame.apply(
        lambda row: row["signal_family"] if _nonempty(row["signal_family"]) and row["signal_family"] != "unknown" else normalize_signal_family(row["source_signal"]),
        axis=1,
    )
    frame["sample_kind"] = frame.apply(
        lambda row: row["sample_kind"] if _nonempty(row["sample_kind"]) else infer_sample_kind_from_db_path(row["db_path"]),
        axis=1,
    )
    frame["is_synthetic"] = frame.apply(
        lambda row: int(row["is_synthetic"]) if pd.notna(row["is_synthetic"]) else int(is_synthetic_sample(row["sample_kind"])),
        axis=1,
    )
    for column in ("entry_spread_pct", "slippage_proxy_bps", "whale_trust_at_entry", "unrealized_pnl", "realized_pnl", "notional_usd"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def _normalize_audits(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(
            columns=[
                "venue",
                "category",
                "signal_family",
                "sample_kind",
                "is_synthetic",
                "action",
                "reason",
                "db_path",
            ]
        )

    frame = frame.copy()
    _ensure_columns(
        frame,
        {
            "venue": None,
            "category": None,
            "signal_family": None,
            "raw_source_signal": None,
            "sample_kind": None,
            "is_synthetic": None,
            "action": None,
            "reason": None,
        },
    )
    frame["signal_family"] = frame.apply(
        lambda row: row["signal_family"] if _nonempty(row["signal_family"]) and row["signal_family"] != "unknown" else normalize_signal_family(row["raw_source_signal"]),
        axis=1,
    )
    frame["sample_kind"] = frame.apply(
        lambda row: row["sample_kind"] if _nonempty(row["sample_kind"]) else infer_sample_kind_from_db_path(row["db_path"]),
        axis=1,
    )
    frame["is_synthetic"] = frame.apply(
        lambda row: int(row["is_synthetic"]) if pd.notna(row["is_synthetic"]) else int(is_synthetic_sample(row["sample_kind"])),
        axis=1,
    )
    return frame


def _core_metrics(trades: pd.DataFrame, positions: pd.DataFrame) -> Dict:
    total_trades = int(len(trades))
    closed_trades = trades[trades["status"] != "OPEN"].copy()
    open_trades = trades[trades["status"] == "OPEN"].copy()

    wins = closed_trades[closed_trades["pnl"] > 0]
    losses = closed_trades[closed_trades["pnl"] < 0]
    realized_pnl = float(closed_trades["pnl"].sum()) if not closed_trades.empty else 0.0
    unrealized_pnl = float(positions[positions["status"] == "OPEN"]["unrealized_pnl"].fillna(0).sum()) if not positions.empty else 0.0
    total_pnl = realized_pnl + unrealized_pnl

    average_win = float(wins["pnl"].mean()) if not wins.empty else None
    average_loss = float(losses["pnl"].mean()) if not losses.empty else None
    expectancy = float(closed_trades["pnl"].mean()) if not closed_trades.empty else None
    gross_profit = float(wins["pnl"].sum()) if not wins.empty else 0.0
    gross_loss = float(abs(losses["pnl"].sum())) if not losses.empty else 0.0
    profit_factor = round(gross_profit / gross_loss, 4) if gross_loss > 0 else (None if gross_profit == 0 else math.inf)

    confidence_series = trades["confidence"].dropna()
    whale_trust_series = trades["whale_trust_at_entry"].dropna()
    hold_seconds = closed_trades["hold_seconds"].dropna()

    return {
        "total_trades": total_trades,
        "closed_trades": int(len(closed_trades)),
        "open_trades": int(len(open_trades)),
        "win_rate": round((len(wins) / len(closed_trades)) * 100, 4) if len(closed_trades) else None,
        "average_win": _round_or_none(average_win),
        "average_loss": _round_or_none(average_loss),
        "expectancy": _round_or_none(expectancy),
        "profit_factor": _round_or_none(profit_factor if profit_factor is not math.inf else None),
        "profit_factor_is_infinite": bool(profit_factor is math.inf),
        "total_pnl": round(total_pnl, 4),
        "realized_pnl": round(realized_pnl, 4),
        "unrealized_pnl": round(unrealized_pnl, 4),
        "max_drawdown": round(_max_drawdown(closed_trades), 4),
        "longest_losing_streak": _longest_losing_streak(closed_trades),
        "average_hold_time_seconds": _round_or_none(float(hold_seconds.mean()) if not hold_seconds.empty else None),
        "average_hold_time_hours": _round_or_none(float(hold_seconds.mean() / 3600) if not hold_seconds.empty else None),
        "exposure_by_venue": _exposure_summary(trades, positions, "venue"),
        "exposure_by_category": _exposure_summary(trades, positions, "category"),
        "average_entry_spread": _round_or_none(float(trades["entry_spread_pct"].dropna().mean()) if not trades["entry_spread_pct"].dropna().empty else None),
        "average_slippage_proxy": _round_or_none(float(trades["slippage_proxy_bps"].dropna().mean()) if not trades["slippage_proxy_bps"].dropna().empty else None),
        "average_confidence": _round_or_none(float(confidence_series.mean()) if not confidence_series.empty else None),
        "average_whale_trust": _round_or_none(float(whale_trust_series.mean()) if not whale_trust_series.empty else None),
    }


def _group_pnl_summary(trades: pd.DataFrame, positions: pd.DataFrame, key: str) -> List[Dict]:
    if trades.empty and positions.empty:
        return []

    trade_summary = pd.DataFrame(columns=[key, "trade_count", "closed_trades", "realized_pnl"])
    if not trades.empty:
        grouped = trades.groupby(trades[key].fillna("UNKNOWN"), dropna=False)
        trade_summary = grouped.apply(
            lambda group: pd.Series(
                {
                    key: group[key].iloc[0] if key in group and pd.notna(group[key].iloc[0]) else "UNKNOWN",
                    "trade_count": len(group),
                    "closed_trades": int((group["status"] != "OPEN").sum()),
                    "realized_pnl": float(group[group["status"] != "OPEN"]["pnl"].sum()),
                    "average_entry_spread": float(group["entry_spread_pct"].dropna().mean()) if not group["entry_spread_pct"].dropna().empty else None,
                    "average_slippage_proxy": float(group["slippage_proxy_bps"].dropna().mean()) if not group["slippage_proxy_bps"].dropna().empty else None,
                }
            )
        ).reset_index(drop=True)

    position_summary = {}
    if not positions.empty:
        for group_key, group in positions.groupby(positions[key].fillna("UNKNOWN"), dropna=False):
            position_summary[group_key] = {
                "unrealized_pnl": float(group[group["status"] == "OPEN"]["unrealized_pnl"].fillna(0).sum()),
                "open_exposure": float(group[group["status"] == "OPEN"]["notional_usd"].fillna(0).sum()),
                "open_positions": int((group["status"] == "OPEN").sum()),
            }

    rows: list[dict] = []
    for _, row in trade_summary.iterrows():
        row_key = row.get(key) or "UNKNOWN"
        position_metrics = position_summary.get(row_key, {"unrealized_pnl": 0.0, "open_exposure": 0.0, "open_positions": 0})
        rows.append(
            {
                key: row_key,
                "trade_count": int(row["trade_count"]),
                "closed_trades": int(row["closed_trades"]),
                "realized_pnl": round(float(row["realized_pnl"]), 4),
                "unrealized_pnl": round(float(position_metrics["unrealized_pnl"]), 4),
                "total_pnl": round(float(row["realized_pnl"]) + float(position_metrics["unrealized_pnl"]), 4),
                "open_exposure": round(float(position_metrics["open_exposure"]), 4),
                "open_positions": int(position_metrics["open_positions"]),
                "average_entry_spread": _round_or_none(row["average_entry_spread"]),
                "average_slippage_proxy": _round_or_none(row["average_slippage_proxy"]),
            }
        )

    uncovered_position_keys = set(position_summary) - {row[key] for row in rows}
    for row_key in sorted(uncovered_position_keys):
        metrics = position_summary[row_key]
        rows.append(
            {
                key: row_key,
                "trade_count": 0,
                "closed_trades": 0,
                "realized_pnl": 0.0,
                "unrealized_pnl": round(float(metrics["unrealized_pnl"]), 4),
                "total_pnl": round(float(metrics["unrealized_pnl"]), 4),
                "open_exposure": round(float(metrics["open_exposure"]), 4),
                "open_positions": int(metrics["open_positions"]),
                "average_entry_spread": None,
                "average_slippage_proxy": None,
            }
        )

    return rows


def _bucket_performance(
    trades: pd.DataFrame,
    column: str,
    buckets: list[tuple[float, float, str]],
    include_unknown: bool = False,
) -> List[Dict]:
    closed_trades = trades[trades["status"] != "OPEN"].copy()
    if closed_trades.empty:
        rows = []
    else:
        rows = []
        for lower, upper, label in buckets:
            bucket = closed_trades[(closed_trades[column] >= lower) & (closed_trades[column] < upper)]
            rows.append(_bucket_summary(bucket, label))
        if include_unknown:
            unknown = closed_trades[closed_trades[column].isna()]
            rows.append(_bucket_summary(unknown, "unknown"))
    return [row for row in rows if row["trade_count"] > 0 or row["bucket"] == "unknown"]


def _bucket_summary(trades: pd.DataFrame, label: str) -> Dict:
    wins = trades[trades["pnl"] > 0]
    return {
        "bucket": label,
        "trade_count": int(len(trades)),
        "win_rate": round((len(wins) / len(trades)) * 100, 4) if len(trades) else None,
        "expectancy": _round_or_none(float(trades["pnl"].mean()) if len(trades) else None),
        "total_pnl": round(float(trades["pnl"].sum()), 4) if len(trades) else 0.0,
    }


def _rejection_counts(audits: pd.DataFrame) -> List[Dict]:
    if audits.empty:
        return []
    rejected = audits[audits["action"] == "reject"]
    counts: dict[str, int] = {}
    for raw_reasons in rejected["reason"].dropna():
        for reason in str(raw_reasons).split(","):
            normalized = reason.strip()
            if not normalized:
                continue
            counts[normalized] = counts.get(normalized, 0) + 1
    return [{"reason": reason, "count": count} for reason, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def _sample_counts(trades: pd.DataFrame) -> Dict[str, int]:
    if trades.empty:
        return {}
    return {
        str(sample_kind): int(count)
        for sample_kind, count in trades.groupby("sample_kind", dropna=False).size().items()
    }


def _evidence_summary(trades: pd.DataFrame) -> Dict:
    live_closed = trades[(trades["sample_kind"] == "live_paper") & (trades["status"] != "OPEN")]
    return {
        "live_paper_total": int((trades["sample_kind"] == "live_paper").sum()) if not trades.empty else 0,
        "live_paper_closed": int(len(live_closed)),
        "synthetic_total": int((trades["is_synthetic"] == 1).sum()) if not trades.empty else 0,
        "sufficient_live_sample_for_go_gate": bool(len(live_closed) >= 30),
    }


def _what_was_measurable(trades: pd.DataFrame, audits: pd.DataFrame) -> List[str]:
    measurable = [
        "paper trade counts",
        "venue/category/signal family attribution",
        "realized pnl from closed trades",
        "unrealized pnl from open venue positions",
        "drawdown from realized trade equity curve",
        "rejection counts from decision audit",
    ]
    if trades.empty:
        measurable.append("sample separation logic (live_paper vs synthetic_verify)")
    if not audits.empty:
        measurable.append("decision/reject audit trail")
    return measurable


def _what_was_not_measurable(trades: pd.DataFrame) -> List[str]:
    unavailable = []
    if trades.empty or int((trades["sample_kind"] == "live_paper").sum()) == 0:
        unavailable.append("live paper alpha evidence")
    unavailable.append("reliable historical Polymarket orderbook replay for non-crypto categories")
    unavailable.append("full historical whale/orderflow archive replay from public repo data")
    return unavailable


def _exposure_summary(trades: pd.DataFrame, positions: pd.DataFrame, key: str) -> List[Dict]:
    exposure: Dict[str, float] = {}
    if not positions.empty:
        for group_key, group in positions[positions["status"] == "OPEN"].groupby(positions[key].fillna("UNKNOWN"), dropna=False):
            exposure[str(group_key)] = exposure.get(str(group_key), 0.0) + float(group["notional_usd"].fillna(0).sum())
    if not trades.empty:
        prediction_open = trades[(trades["status"] == "OPEN") & (trades["instrument_type"] == "prediction")]
        for group_key, group in prediction_open.groupby(prediction_open[key].fillna("UNKNOWN"), dropna=False):
            exposure[str(group_key)] = exposure.get(str(group_key), 0.0) + float(group["size"].fillna(0).sum())
    return [{"bucket": bucket, "open_exposure": round(value, 4)} for bucket, value in sorted(exposure.items())]


def _max_drawdown(closed_trades: pd.DataFrame) -> float:
    if closed_trades.empty:
        return 0.0
    ordered = closed_trades.sort_values(["closed_at", "timestamp", "id"], na_position="last")
    equity = ordered["pnl"].cumsum()
    peak = equity.cummax()
    drawdown = peak - equity
    return float(drawdown.max()) if not drawdown.empty else 0.0


def _longest_losing_streak(closed_trades: pd.DataFrame) -> int:
    if closed_trades.empty:
        return 0
    ordered = closed_trades.sort_values(["closed_at", "timestamp", "id"], na_position="last")
    longest = 0
    current = 0
    for pnl in ordered["pnl"]:
        if pnl < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _ensure_columns(frame: pd.DataFrame, defaults: Dict[str, object]) -> None:
    for column, default in defaults.items():
        if column not in frame.columns:
            frame[column] = default


def _infer_trade_category(row: pd.Series) -> str:
    category = row.get("category")
    if _nonempty(category):
        return str(category)
    instrument_type = str(row.get("instrument_type") or "").lower()
    market_id = str(row.get("market_id") or "").lower()
    source_signal = str(row.get("source_signal") or "").lower()
    if instrument_type in {"futures", "spot"} or "crypto" in market_id or "binance_" in source_signal:
        return "CRYPTO"
    if "sports" in market_id:
        return "SPORTS"
    return "UNKNOWN"


def _infer_position_category(row: pd.Series) -> str:
    category = row.get("category")
    if _nonempty(category):
        return str(category)
    instrument_type = str(row.get("instrument_type") or "").lower()
    symbol = str(row.get("symbol_or_market_id") or "").lower()
    if instrument_type in {"futures", "spot"} or "btc" in symbol or "eth" in symbol:
        return "CRYPTO"
    return "UNKNOWN"


def _render_summary_markdown(summary: Dict) -> str:
    core = summary.get("core", {})
    lines = [
        "# Performance Summary",
        "",
        f"- Total trades: {core.get('total_trades', 0)}",
        f"- Closed trades: {core.get('closed_trades', 0)}",
        f"- Open trades: {core.get('open_trades', 0)}",
        f"- Win rate: {core.get('win_rate')}",
        f"- Expectancy: {core.get('expectancy')}",
        f"- Profit factor: {core.get('profit_factor')}",
        f"- Realized PnL: {core.get('realized_pnl')}",
        f"- Unrealized PnL: {core.get('unrealized_pnl')}",
        f"- Max drawdown: {core.get('max_drawdown')}",
        "",
        "## Evidence",
        "",
        f"- Live paper closed trades: {summary.get('evidence', {}).get('live_paper_closed', 0)}",
        f"- Synthetic trades excluded from core: {summary.get('synthetic_appendix', {}).get('trade_count', 0)}",
    ]
    return "\n".join(lines) + "\n"


def _flatten_for_csv(core: Dict) -> Dict:
    flat = {}
    for key, value in core.items():
        if isinstance(value, list):
            flat[key] = json.dumps(value)
        else:
            flat[key] = value
    return flat


def _round_or_none(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return round(float(value), 4)


def _nonempty(value: object) -> bool:
    return value is not None and str(value).strip() != ""
