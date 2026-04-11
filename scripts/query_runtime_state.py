#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
from pathlib import Path


STATUS_METRIC_PATTERN = re.compile(r"([A-Za-z_]+)=([^\s]+)")


def _coerce_metric_value(raw: str):
    if raw.replace(".", "", 1).isdigit():
        return float(raw) if "." in raw else int(raw)
    return raw


def _print_row(row) -> None:
    if row is None:
        print(None)
        return
    if isinstance(row, sqlite3.Row):
        print(tuple(row))
        return
    if hasattr(row, "keys"):
        try:
            print(tuple(row[key] for key in row.keys()))
            return
        except Exception:
            pass
    if isinstance(row, dict):
        print(tuple(row.values()))
        return
    print(row)


def _summarize_whale_copy_recovery(rows) -> dict[str, int]:
    summary = {
        "relaxed_gate_attempts": 0,
        "relaxed_gate_decisions": 0,
        "token_recovery_attempts": 0,
        "token_recovery_hits": 0,
        "token_recovery_failed": 0,
        "missing_token_rejects": 0,
    }
    for row in rows or []:
        action = row["action"] if isinstance(row, sqlite3.Row) else row[0]
        reason = str((row["reason"] if isinstance(row, sqlite3.Row) else row[1]) or "")
        raw_inputs = (row["inputs_json"] if isinstance(row, sqlite3.Row) else row[2]) or "{}"
        try:
            inputs = json.loads(raw_inputs)
        except (TypeError, json.JSONDecodeError):
            continue
        copy_policy = str(inputs.get("copy_policy") or "").lower()
        is_gated_copy = copy_policy == "gated_whale_copy" or bool(inputs.get("whale_copy_relaxed_gate"))
        if not is_gated_copy and "token_recovery" not in reason:
            continue
        if inputs.get("whale_copy_relaxed_gate"):
            summary["relaxed_gate_attempts"] += 1
            if action == "decision":
                summary["relaxed_gate_decisions"] += 1
        if inputs.get("token_recovery_attempted"):
            summary["token_recovery_attempts"] += 1
        if inputs.get("token_recovery_hit"):
            summary["token_recovery_hits"] += 1
        if inputs.get("token_recovery_failed") or "token_recovery_failed" in reason:
            summary["token_recovery_failed"] += 1
        if "missing_polymarket_token_price" in reason:
            summary["missing_token_rejects"] += 1
    return summary


def _read_latest_status_metrics() -> dict:
    try:
        result = subprocess.run(
            ["journalctl", "-u", "ghost-trader", "-n", "200", "--no-pager"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return {}

    latest_status = None
    for line in (result.stdout or "").splitlines():
        if "[STATUS]" in line:
            latest_status = line
    if latest_status is None:
        return {}

    metrics = {}
    for key, value in STATUS_METRIC_PATTERN.findall(latest_status):
        metrics[key] = _coerce_metric_value(value)
    return metrics


def _read_runtime_status_snapshot(cursor: sqlite3.Cursor) -> dict:
    try:
        row = cursor.execute(
            """
            SELECT updated_at, metrics_json
            FROM runtime_status_snapshot
            WHERE id = 1
            """
        ).fetchone()
    except sqlite3.OperationalError:
        return {}

    if row is None:
        return {}

    metrics_json = row["metrics_json"] if isinstance(row, sqlite3.Row) else row[1]
    if not metrics_json:
        return {}

    try:
        metrics = json.loads(metrics_json)
    except (TypeError, json.JSONDecodeError):
        return {}

    if not isinstance(metrics, dict):
        return {}

    updated_at = row["updated_at"] if isinstance(row, sqlite3.Row) else row[0]
    metrics["updated_at"] = updated_at
    if metrics.get("lookup_hydration_warning") == "none":
        metrics["lookup_hydration_warning"] = None
    return metrics


def _fetch_orderflow_mapping_stats(cursor: sqlite3.Cursor, recent_window: bool = False) -> tuple[int, int]:
    if recent_window:
        row = cursor.execute(
            """
            WITH anchor AS (
                SELECT MAX(occurred_at) AS max_occurred_at
                FROM decision_audit
                WHERE signal_family IN ('activity_orderflow', 'whale')
            )
            SELECT
                COALESCE(SUM(CASE WHEN decision_audit.signal_family IN ('activity_orderflow', 'whale') THEN 1 ELSE 0 END), 0) AS total_orderflow,
                COALESCE(SUM(CASE WHEN decision_audit.signal_family IN ('activity_orderflow', 'whale') AND decision_audit.reason LIKE 'market_not_mapped%' THEN 1 ELSE 0 END), 0) AS unmapped_orderflow
            FROM decision_audit
            CROSS JOIN anchor
            WHERE anchor.max_occurred_at IS NOT NULL
              AND decision_audit.occurred_at >= datetime(anchor.max_occurred_at, '-60 minutes')
            """
        ).fetchone()
    else:
        row = cursor.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale') THEN 1 ELSE 0 END), 0) AS total_orderflow,
                COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale') AND reason LIKE 'market_not_mapped%' THEN 1 ELSE 0 END), 0) AS unmapped_orderflow
            FROM decision_audit
            """
        ).fetchone()

    if row is None:
        return (0, 0)
    return (int(row[0] or 0), int(row[1] or 0))


def main() -> int:
    db_path = Path(os.getenv("GHOST_TRADER_DB_PATH", "data/ghost_trader.db"))
    if not db_path.exists():
        print(f"SQLite database not found: {db_path}")
        return 1

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    cursor = connection.cursor()
    snapshot_metrics = _read_runtime_status_snapshot(cursor)
    status_metrics = snapshot_metrics or _read_latest_status_metrics()

    wallet = cursor.execute("SELECT balance FROM wallet WHERE id = 1").fetchone()
    venue_accounts = cursor.execute(
        """
        SELECT venue, execution_mode, cash_balance, equity, available_balance
        FROM venue_accounts
        ORDER BY venue, execution_mode
        """
    ).fetchall()
    trades = cursor.execute(
        """
        SELECT id, venue, instrument_type, market_id, side, size, price, confidence, whale_address, timestamp
        FROM trades
        ORDER BY id DESC
        LIMIT 10
        """
    ).fetchall()
    positions = cursor.execute(
        """
        SELECT id, venue, instrument_type, symbol_or_market_id, side, entry_price, mark_price, notional_usd, unrealized_pnl, realized_pnl, status
        FROM venue_positions
        ORDER BY id DESC
        LIMIT 10
        """
    ).fetchall()
    orders = cursor.execute(
        """
        SELECT id, venue, symbol_or_market_id, order_type, side, qty, price, stop_price, reduce_only, status
        FROM venue_orders
        WHERE status = 'OPEN'
        ORDER BY id DESC
        LIMIT 10
        """
    ).fetchall()
    try:
        market_alias_counts = cursor.execute(
            """
            SELECT source, COUNT(*) AS count
            FROM market_aliases
            GROUP BY source
            ORDER BY source
            """
        ).fetchall()
        market_aliases = cursor.execute(
            """
            SELECT alias, alias_type, market_id, category, volume_24h, active, source
            FROM market_aliases
            ORDER BY volume_24h DESC, last_seen_at DESC
            LIMIT 10
            """
        ).fetchall()
    except sqlite3.OperationalError:
        market_alias_counts = []
        market_aliases = []
    try:
        whale_counts = cursor.execute(
            """
            SELECT whale_wallet_sources.source_type, COUNT(DISTINCT LOWER(whale_wallet_sources.address)) AS count
            FROM whale_wallet_sources
            INNER JOIN whale_wallets ON LOWER(whale_wallets.address) = LOWER(whale_wallet_sources.address)
            WHERE whale_wallets.enabled = 1
            GROUP BY whale_wallet_sources.source_type
            ORDER BY whale_wallet_sources.source_type
            """
        ).fetchall()
    except sqlite3.OperationalError:
        try:
            whale_counts = cursor.execute(
                """
                SELECT source_type, COUNT(*) AS count
                FROM whale_wallets
                WHERE enabled = 1
                GROUP BY source_type
                ORDER BY source_type
                """
            ).fetchall()
        except sqlite3.OperationalError:
            whale_counts = []
    try:
        whale_wallets = cursor.execute(
            """
            SELECT address, source_type, discovery_score, last_event_amount, event_count_24h, failure_streak
            FROM whale_wallets
            WHERE enabled = 1
            ORDER BY discovery_score DESC, last_event_amount DESC
            LIMIT 10
            """
        ).fetchall()
        trusted_whales = cursor.execute(
            """
            SELECT
                whale_stats.address,
                COALESCE(whale_wallets.source_type, 'trusted_only') AS source_type,
                whale_stats.trust_score,
                whale_stats.total_trades,
                whale_stats.wins,
                whale_stats.total_pnl
            FROM whale_stats
            LEFT JOIN whale_wallets ON whale_wallets.address = whale_stats.address
            WHERE whale_stats.total_trades > 0
            ORDER BY whale_stats.trust_score DESC, whale_stats.total_trades DESC, whale_stats.total_pnl DESC
            LIMIT 10
            """
        ).fetchall()
        trusted_whale_count_row = cursor.execute(
            """
            SELECT COUNT(*) AS count
            FROM whale_stats
            WHERE total_trades > 0
            """
        ).fetchone()
        trusted_whale_count = int(trusted_whale_count_row[0] if trusted_whale_count_row else 0)
        tracked_whale_count_row = cursor.execute(
            """
            SELECT COUNT(*) AS count
            FROM whale_wallets
            WHERE enabled = 1
            """
        ).fetchone()
        tracked_whale_count = int(tracked_whale_count_row[0] if tracked_whale_count_row else 0)
    except sqlite3.OperationalError:
        whale_wallets = []
        trusted_whales = []
        trusted_whale_count = 0
        tracked_whale_count = 0
    try:
        decision_audit = cursor.execute(
            """
            SELECT occurred_at, venue, category, signal_family, action, reason, decision_score, trade_size
            FROM decision_audit
            ORDER BY id DESC
            LIMIT 10
            """
        ).fetchall()
    except sqlite3.OperationalError:
        decision_audit = []
    try:
        routing_breakdown = cursor.execute(
            """
            SELECT
                CASE
                    WHEN raw_source_signal = 'discovery' AND reason = 'route_whale_orderflow_only' THEN 'discovery_route_only'
                    WHEN signal_family IN ('activity_orderflow', 'whale') AND strategy_profile = 'sampling_relaxed' THEN 'sampling_orderflow'
                    WHEN signal_family IN ('activity_orderflow', 'whale') THEN 'baseline_orderflow'
                    ELSE 'other'
                END AS flow_classification,
                COUNT(*) AS count
            FROM decision_audit
            GROUP BY flow_classification
            HAVING flow_classification != 'other'
            ORDER BY count DESC, flow_classification ASC
            """
        ).fetchall()
        sampling_decision_summary = cursor.execute(
            """
            SELECT action, COUNT(*) AS count
            FROM decision_audit
            WHERE strategy_profile = 'sampling_relaxed'
              AND signal_family IN ('activity_orderflow', 'whale')
            GROUP BY action
            ORDER BY count DESC, action ASC
            """
        ).fetchall()
        sampling_execute_count = cursor.execute(
            """
            SELECT COUNT(*) AS count
            FROM trades
            WHERE strategy_profile = 'sampling_relaxed'
              AND signal_family IN ('activity_orderflow', 'whale')
              AND venue = 'polymarket'
            """
        ).fetchone()
        mapping_miss_breakdown = cursor.execute(
            """
            SELECT reason, COUNT(*) AS count
            FROM decision_audit
            WHERE reason LIKE 'market_not_mapped%'
               OR reason IN ('unsupported_side_filtered', 'sell_side_not_supported')
            GROUP BY reason
            ORDER BY count DESC, reason ASC
            """
        ).fetchall()
        source_quality_summary = cursor.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale') THEN 1 ELSE 0 END), 0) AS total_orderflow,
                COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale') AND reason NOT LIKE 'market_not_mapped%' THEN 1 ELSE 0 END), 0) AS orderflow_after_mapping,
                COALESCE(SUM(CASE WHEN raw_source_signal = 'discovery' AND reason = 'route_whale_orderflow_only' THEN 1 ELSE 0 END), 0) AS discovery_route_only,
                COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale') AND strategy_profile = 'sampling_relaxed' THEN 1 ELSE 0 END), 0) AS sampling_orderflow,
                COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale') AND strategy_profile != 'sampling_relaxed' THEN 1 ELSE 0 END), 0) AS baseline_orderflow,
                COALESCE(SUM(CASE WHEN reason IN ('unsupported_side_filtered', 'sell_side_not_supported') THEN 1 ELSE 0 END), 0) AS unsupported_side_filtered
            FROM decision_audit
            """
        ).fetchone()
        unsupported_side_summary = cursor.execute(
            """
            SELECT reason, COUNT(*) AS count
            FROM decision_audit
            WHERE reason IN ('unsupported_side_filtered', 'sell_side_not_supported')
            GROUP BY reason
            ORDER BY count DESC, reason ASC
            """
        ).fetchall()
        whale_copy_summary = cursor.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale') THEN 1 ELSE 0 END), 0) AS total_whale_events,
                COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale')
                                      AND COALESCE(reason, '') NOT LIKE 'market_not_mapped%'
                                      AND COALESCE(reason, '') NOT LIKE '%unsupported_side_filtered%'
                                      AND COALESCE(reason, '') NOT LIKE '%sell_side_not_supported%'
                                 THEN 1 ELSE 0 END), 0) AS resolved_whale_events,
                COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale')
                                      AND COALESCE(reason, '') NOT LIKE 'market_not_mapped%'
                                      AND COALESCE(reason, '') NOT LIKE '%unsupported_side_filtered%'
                                      AND COALESCE(reason, '') NOT LIKE '%sell_side_not_supported%'
                                      AND COALESCE(reason, '') NOT LIKE '%route_whale_orderflow_only%'
                                 THEN 1 ELSE 0 END), 0) AS whale_copy_candidates,
                COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale')
                                      AND action = 'reject'
                                      AND COALESCE(reason, '') NOT LIKE 'market_not_mapped%'
                                      AND COALESCE(reason, '') NOT LIKE '%unsupported_side_filtered%'
                                      AND COALESCE(reason, '') NOT LIKE '%sell_side_not_supported%'
                                      AND COALESCE(reason, '') NOT LIKE '%route_whale_orderflow_only%'
                                 THEN 1 ELSE 0 END), 0) AS gated_rejects,
                COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale')
                                      AND action = 'decision'
                                 THEN 1 ELSE 0 END), 0) AS gated_decisions
            FROM decision_audit
            """
        ).fetchone()
        gated_reject_breakdown = cursor.execute(
            """
            SELECT reason
            FROM decision_audit
            WHERE signal_family IN ('activity_orderflow', 'whale')
              AND action = 'reject'
              AND COALESCE(reason, '') NOT LIKE 'market_not_mapped%'
              AND COALESCE(reason, '') NOT LIKE '%unsupported_side_filtered%'
              AND COALESCE(reason, '') NOT LIKE '%sell_side_not_supported%'
              AND COALESCE(reason, '') NOT LIKE '%route_whale_orderflow_only%'
            ORDER BY id DESC
            LIMIT 300
            """
        ).fetchall()
    except sqlite3.OperationalError:
        routing_breakdown = []
        sampling_decision_summary = []
        sampling_execute_count = (0,)
        mapping_miss_breakdown = []
        source_quality_summary = (0, 0, 0, 0, 0, 0)
        unsupported_side_summary = []
        whale_copy_summary = (0, 0, 0, 0, 0)
        gated_reject_breakdown = []
    try:
        whale_copy_recovery_rows = cursor.execute(
            """
            SELECT action, reason, inputs_json
            FROM decision_audit
            WHERE signal_family IN ('activity_orderflow', 'whale')
              AND inputs_json IS NOT NULL
            ORDER BY id DESC
            LIMIT 500
            """
        ).fetchall()
    except sqlite3.OperationalError:
        whale_copy_recovery_rows = []
    try:
        alias_integrity = cursor.execute(
            """
            SELECT
                COUNT(*) AS alias_rows,
                COUNT(DISTINCT market_id) AS market_rows
            FROM market_aliases
            """
        ).fetchone()
    except sqlite3.OperationalError:
        alias_integrity = (0, 0)
    try:
        historical_total_orderflow, historical_unmapped_orderflow = _fetch_orderflow_mapping_stats(cursor, recent_window=False)
        recent_total_orderflow, recent_unmapped_orderflow = _fetch_orderflow_mapping_stats(cursor, recent_window=True)
    except sqlite3.OperationalError:
        historical_total_orderflow, historical_unmapped_orderflow = (0, 0)
        recent_total_orderflow, recent_unmapped_orderflow = (0, 0)
    connection.close()

    print(f"DB_PATH={db_path}")
    print(f"WALLET_BALANCE={wallet[0] if wallet else 'missing'}")
    print("VENUE_ACCOUNTS")
    for account in venue_accounts:
        _print_row(account)
    print("RECENT_TRADES")
    for trade in trades:
        _print_row(trade)
    print("OPEN_POSITIONS")
    for position in positions:
        _print_row(position)
    print("OPEN_ORDERS")
    for order in orders:
        _print_row(order)
    print("MARKET_ALIAS_COUNTS")
    for count in market_alias_counts:
        _print_row(count)
    print("TOP_MARKET_ALIASES")
    for market_alias in market_aliases:
        _print_row(market_alias)
    print("WHALE_WALLET_COUNTS")
    for count in whale_counts:
        _print_row(count)
    print("TOP_WHALE_WALLETS")
    for whale_wallet in whale_wallets:
        _print_row(whale_wallet)
    print("TRUSTED_WHALES")
    for whale_wallet in trusted_whales:
        _print_row(whale_wallet)
    print("RECENT_DECISION_AUDIT")
    for audit_row in decision_audit:
        _print_row(audit_row)
    print("ROUTING_BREAKDOWN")
    for row in routing_breakdown:
        _print_row(row)
    print("SAMPLING_DECISION_SUMMARY")
    for row in sampling_decision_summary:
        _print_row(row)
    print(("execute", sampling_execute_count[0] if sampling_execute_count else 0))
    print("MAPPING_MISS_BREAKDOWN")
    for row in mapping_miss_breakdown:
        _print_row(row)
    print("MAPPING_RATE_SUMMARY")
    live_market_not_mapped_rate = status_metrics.get("market_not_mapped_rate")
    print(("live_market_not_mapped_rate", live_market_not_mapped_rate if live_market_not_mapped_rate is not None else "missing"))
    print(("recent_total_orderflow", recent_total_orderflow))
    print(("recent_unmapped_orderflow", recent_unmapped_orderflow))
    print(("recent_market_not_mapped_rate", round((recent_unmapped_orderflow / recent_total_orderflow) * 100.0, 1) if recent_total_orderflow else 0.0))
    print(("historical_total_orderflow", historical_total_orderflow))
    print(("historical_unmapped_orderflow", historical_unmapped_orderflow))
    print(("historical_market_not_mapped_rate", round((historical_unmapped_orderflow / historical_total_orderflow) * 100.0, 1) if historical_total_orderflow else 0.0))
    print("ALIAS_PERSISTENCE_SUMMARY")
    lookup_universe_markets = int(status_metrics.get("lookup_universe_markets", 0) or 0)
    lookup_universe_aliases = int(status_metrics.get("lookup_universe_aliases", 0) or 0)
    hydrated_lookup_markets = int(status_metrics.get("hydrated_lookup_markets", 0) or 0)
    hydrated_lookup_aliases = int(status_metrics.get("hydrated_lookup_aliases", 0) or 0)
    persisted_rows = int((alias_integrity[0] if alias_integrity else 0) or 0)
    persisted_markets = int((alias_integrity[1] if alias_integrity else 0) or 0)
    print(("lookup_universe_markets", lookup_universe_markets))
    print(("lookup_universe_aliases", lookup_universe_aliases))
    print(("persisted_market_alias_rows", persisted_rows))
    print(("persisted_market_alias_markets", persisted_markets))
    print(("hydrated_lookup_markets", hydrated_lookup_markets))
    print(("hydrated_lookup_aliases", hydrated_lookup_aliases))
    print(("lookup_hydration_warning", status_metrics.get("lookup_hydration_warning") or "none"))
    print(("alias_persistence_gap", max(lookup_universe_aliases - persisted_rows, 0)))
    print("SOURCE_QUALITY_SUMMARY")
    source_labels = (
        "total_orderflow",
        "orderflow_after_mapping",
        "discovery_route_only",
        "sampling_orderflow",
        "baseline_orderflow",
        "unsupported_side_filtered",
    )
    for label, value in zip(source_labels, source_quality_summary or ()):
        print((label, value))
    print("WHALE_UNIVERSE_SUMMARY")
    whale_count_map = {str(row[0]): int(row[1]) for row in whale_counts}
    print(("tracked_whales", tracked_whale_count))
    print(("leaderboard_wallets", whale_count_map.get("leaderboard", 0)))
    print(("activity_discovered_wallets", whale_count_map.get("activity_discovery", 0)))
    print(("graph_discovered_wallets", whale_count_map.get("graph_discovery", 0)))
    print(("trusted_whales", trusted_whale_count))
    print("WHALE_COPY_SUMMARY")
    whale_copy_labels = (
        "total_whale_events",
        "resolved_whale_events",
        "whale_copy_candidates",
        "gated_rejects",
        "gated_decisions",
    )
    for label, value in zip(whale_copy_labels, whale_copy_summary or ()):
        print((label, value))
    print(("gated_executes", sampling_execute_count[0] if sampling_execute_count else 0))
    print("WHALE_COPY_RECOVERY_SUMMARY")
    for label, value in _summarize_whale_copy_recovery(whale_copy_recovery_rows).items():
        print((label, value))
    print("GATED_REJECT_BREAKDOWN")
    gated_counts: dict[str, int] = {}
    for row in gated_reject_breakdown:
        reason_text = str(row[0] if not isinstance(row, sqlite3.Row) else row["reason"] or "").strip()
        if not reason_text:
            continue
        for reason in [part.strip() for part in reason_text.split(",") if part.strip()]:
            gated_counts[reason] = gated_counts.get(reason, 0) + 1
    for reason, count in sorted(gated_counts.items(), key=lambda item: (-item[1], item[0]))[:10]:
        print((reason, count))
    print("UNSUPPORTED_SIDE_SUMMARY")
    for row in unsupported_side_summary:
        _print_row(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
