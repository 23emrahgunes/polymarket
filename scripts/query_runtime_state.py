#!/usr/bin/env python3
from __future__ import annotations

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


def main() -> int:
    db_path = Path(os.getenv("GHOST_TRADER_DB_PATH", "data/ghost_trader.db"))
    if not db_path.exists():
        print(f"SQLite database not found: {db_path}")
        return 1

    connection = sqlite3.connect(db_path)
    cursor = connection.cursor()
    status_metrics = _read_latest_status_metrics()

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
            SELECT source_type, COUNT(*) AS count
            FROM whale_wallets
            WHERE enabled = 1
            GROUP BY source_type
            ORDER BY source_type
            """
        ).fetchall()
        whale_wallets = cursor.execute(
            """
            SELECT address, source_type, discovery_score, last_event_amount, event_count_24h, failure_streak
            FROM whale_wallets
            WHERE enabled = 1
            ORDER BY discovery_score DESC, last_event_amount DESC
            LIMIT 10
            """
        ).fetchall()
    except sqlite3.OperationalError:
        whale_counts = []
        whale_wallets = []
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
    except sqlite3.OperationalError:
        routing_breakdown = []
        sampling_decision_summary = []
        sampling_execute_count = (0,)
        mapping_miss_breakdown = []
        source_quality_summary = (0, 0, 0, 0, 0, 0)
        unsupported_side_summary = []
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
    connection.close()

    print(f"DB_PATH={db_path}")
    print(f"WALLET_BALANCE={wallet[0] if wallet else 'missing'}")
    print("VENUE_ACCOUNTS")
    for account in venue_accounts:
        print(account)
    print("RECENT_TRADES")
    for trade in trades:
        print(trade)
    print("OPEN_POSITIONS")
    for position in positions:
        print(position)
    print("OPEN_ORDERS")
    for order in orders:
        print(order)
    print("MARKET_ALIAS_COUNTS")
    for count in market_alias_counts:
        print(count)
    print("TOP_MARKET_ALIASES")
    for market_alias in market_aliases:
        print(market_alias)
    print("WHALE_WALLET_COUNTS")
    for count in whale_counts:
        print(count)
    print("TOP_WHALE_WALLETS")
    for whale_wallet in whale_wallets:
        print(whale_wallet)
    print("RECENT_DECISION_AUDIT")
    for audit_row in decision_audit:
        print(audit_row)
    print("ROUTING_BREAKDOWN")
    for row in routing_breakdown:
        print(row)
    print("SAMPLING_DECISION_SUMMARY")
    for row in sampling_decision_summary:
        print(row)
    print(("execute", sampling_execute_count[0] if sampling_execute_count else 0))
    print("MAPPING_MISS_BREAKDOWN")
    for row in mapping_miss_breakdown:
        print(row)
    print("ALIAS_PERSISTENCE_SUMMARY")
    lookup_universe_markets = int(status_metrics.get("lookup_universe_markets", 0) or 0)
    lookup_universe_aliases = int(status_metrics.get("lookup_universe_aliases", 0) or 0)
    persisted_rows = int((alias_integrity[0] if alias_integrity else 0) or 0)
    persisted_markets = int((alias_integrity[1] if alias_integrity else 0) or 0)
    print(("lookup_universe_markets", lookup_universe_markets))
    print(("lookup_universe_aliases", lookup_universe_aliases))
    print(("persisted_market_alias_rows", persisted_rows))
    print(("persisted_market_alias_markets", persisted_markets))
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
    print("UNSUPPORTED_SIDE_SUMMARY")
    for row in unsupported_side_summary:
        print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
