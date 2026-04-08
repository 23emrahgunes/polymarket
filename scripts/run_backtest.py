#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Iterable, List

import pandas as pd


DEFAULT_OUTPUT_DIR = "reports/performance"
SUPPORTED_REPLAY_CATEGORY = "CRYPTO"


def run_backtest(
    dataset_path: str | None = None,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    seed: int = 42,
) -> Dict:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    if not dataset_path:
        return _write_report(
            output_path,
            {
                "seed": seed,
                "status": "insufficient_evidence",
                "reason": "No structured replay dataset was provided. The repo does not include a reliable historical Polymarket orderbook / whale archive for a trustworthy all-category backtest.",
                "supported_categories": ["CRYPTO"],
                "unsupported_categories": {
                    "SPORTS": "Missing reliable historical Polymarket orderbook and whale/activity archive.",
                    "POLITICS": "Missing reliable historical Polymarket orderbook and whale/activity archive.",
                    "OTHER": "Missing reliable historical Polymarket orderbook and whale/activity archive.",
                },
                "what_was_measurable": [],
                "what_was_not_measurable": [
                    "Out-of-sample replay without a structured crypto dataset",
                    "Non-crypto Polymarket replay from current repo data",
                ],
                "train": {},
                "validation": {},
                "test": {},
                "sensitivity": {"status": "insufficient_evidence", "parameter_effects": []},
            },
        )

    replay_frame = _load_replay_dataset(dataset_path)
    replay_frame = replay_frame.sort_values("timestamp").reset_index(drop=True)
    crypto_frame = replay_frame[replay_frame["category"] == SUPPORTED_REPLAY_CATEGORY].copy()

    unsupported_categories = sorted(set(replay_frame["category"].dropna()) - {SUPPORTED_REPLAY_CATEGORY})
    report = {
        "seed": seed,
        "dataset_path": str(Path(dataset_path)),
        "status": "ok" if not crypto_frame.empty else "insufficient_evidence",
        "supported_categories": [SUPPORTED_REPLAY_CATEGORY],
        "unsupported_categories": {
            category: "Replay dataset contains rows, but the current harness is limited to crypto venue replay."
            for category in unsupported_categories
        },
        "what_was_measurable": [
            "Structured crypto replay for Binance Futures and Binance Spot",
            "Walk-forward train/validation/test metrics from replay dataset",
            "Sensitivity deltas for supported parameters present in the dataset",
        ],
        "what_was_not_measurable": [
            "Historical Polymarket binary replay unless the dataset explicitly includes trustworthy exit prices",
            "Non-crypto Polymarket replay without historical orderbook and whale archive",
        ],
    }

    if crypto_frame.empty:
        report["reason"] = "Replay dataset does not contain supported CRYPTO rows."
        report["train"] = {}
        report["validation"] = {}
        report["test"] = {}
        report["sensitivity"] = {"status": "insufficient_evidence", "parameter_effects": []}
        return _write_report(output_path, report)

    train, validation, test = _split_walk_forward(crypto_frame)
    report["train"] = _evaluate_partition(train)
    report["validation"] = _evaluate_partition(validation)
    report["test"] = _evaluate_partition(test)
    report["sensitivity"] = _run_sensitivity(crypto_frame)
    return _write_report(output_path, report)


def _load_replay_dataset(dataset_path: str) -> pd.DataFrame:
    path = Path(dataset_path)
    if not path.exists():
        raise FileNotFoundError(f"Replay dataset not found: {path}")

    if path.suffix.lower() == ".json":
        frame = pd.read_json(path)
    else:
        frame = pd.read_csv(path)

    required_columns = {
        "timestamp",
        "category",
        "venue",
        "signal_score",
        "direction",
        "entry_price",
        "exit_price",
        "trade_size",
        "spread_pct",
    }
    missing = sorted(required_columns - set(frame.columns))
    if missing:
        raise ValueError(f"Replay dataset missing required columns: {', '.join(missing)}")

    defaults = {
        "signal_family": "discovery",
        "sample_kind": "replay",
        "event_amount": None,
        "wallets_count": None,
        "price_drift_pct": None,
        "volume_24h": None,
    }
    for column, default in defaults.items():
        if column not in frame.columns:
            frame[column] = default

    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="raise")
    for numeric_column in (
        "signal_score",
        "entry_price",
        "exit_price",
        "trade_size",
        "spread_pct",
        "event_amount",
        "wallets_count",
        "price_drift_pct",
        "volume_24h",
    ):
        frame[numeric_column] = pd.to_numeric(frame[numeric_column], errors="coerce")
    return frame


def _split_walk_forward(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    total = len(frame)
    train_end = max(int(total * 0.50), 1)
    validation_end = max(int(total * 0.75), train_end + 1)
    validation_end = min(validation_end, total)
    return (
        frame.iloc[:train_end].copy(),
        frame.iloc[train_end:validation_end].copy(),
        frame.iloc[validation_end:].copy(),
    )


def _evaluate_partition(frame: pd.DataFrame) -> Dict:
    if frame.empty:
        return {"trade_count": 0, "status": "insufficient_evidence"}

    simulated = frame.copy()
    simulated["pnl"] = simulated.apply(_row_pnl, axis=1)
    simulated = simulated[simulated["pnl"].notna()].copy()
    if simulated.empty:
        return {"trade_count": 0, "status": "insufficient_evidence"}

    wins = simulated[simulated["pnl"] > 0]
    losses = simulated[simulated["pnl"] < 0]
    gross_profit = float(wins["pnl"].sum()) if not wins.empty else 0.0
    gross_loss = float(abs(losses["pnl"].sum())) if not losses.empty else 0.0
    return {
        "trade_count": int(len(simulated)),
        "win_rate": round((len(wins) / len(simulated)) * 100, 4) if len(simulated) else None,
        "expectancy": round(float(simulated["pnl"].mean()), 4) if len(simulated) else None,
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss > 0 else None,
        "total_pnl": round(float(simulated["pnl"].sum()), 4),
        "venues": sorted(simulated["venue"].dropna().unique().tolist()),
    }


def _row_pnl(row: pd.Series) -> float | None:
    venue = str(row["venue"]).lower()
    direction = str(row["direction"]).upper()
    entry_price = float(row["entry_price"])
    exit_price = float(row["exit_price"])
    trade_size = float(row["trade_size"])
    if entry_price <= 0:
        return None

    if venue == "binance_futures":
        notional = trade_size * 2
        quantity = notional / entry_price
        if direction == "LONG":
            return round(quantity * (exit_price - entry_price), 4)
        return round(quantity * (entry_price - exit_price), 4)

    if venue == "binance_spot":
        if direction != "LONG":
            return None
        quantity = trade_size / entry_price
        return round(quantity * (exit_price - entry_price), 4)

    if venue == "polymarket":
        if exit_price < 0 or exit_price > 1:
            return None
        shares = trade_size / entry_price
        if direction == "LONG":
            payout = shares * exit_price
        else:
            payout = shares * (1 - exit_price)
        return round(payout - trade_size, 4)

    return None


def _run_sensitivity(frame: pd.DataFrame) -> Dict:
    parameter_effects: list[Dict] = []
    baseline = _evaluate_partition(frame)

    if baseline.get("trade_count", 0) == 0:
        return {"status": "insufficient_evidence", "parameter_effects": []}

    scenarios = [
        ("min_score", "signal_score", [0.68, 0.72, 0.78]),
        ("max_spread", "spread_pct", [0.005, 0.01, 0.02]),
        ("trade_size", "trade_size_multiplier", [0.5, 1.0, 1.5]),
        ("venue_enable_disable", "venue_toggle", ["all", "binance_futures_only", "binance_spot_only"]),
    ]

    for name, parameter_type, values in scenarios:
        for value in values:
            scenario_frame = frame.copy()
            if parameter_type == "signal_score":
                scenario_frame = scenario_frame[scenario_frame["signal_score"] >= float(value)]
            elif parameter_type == "spread_pct":
                scenario_frame = scenario_frame[scenario_frame["spread_pct"] <= float(value)]
            elif parameter_type == "trade_size_multiplier":
                scenario_frame["trade_size"] = scenario_frame["trade_size"] * float(value)
            elif parameter_type == "venue_toggle":
                if value == "binance_futures_only":
                    scenario_frame = scenario_frame[scenario_frame["venue"] == "binance_futures"]
                elif value == "binance_spot_only":
                    scenario_frame = scenario_frame[scenario_frame["venue"] == "binance_spot"]

            metrics = _evaluate_partition(scenario_frame)
            parameter_effects.append(
                {
                    "parameter": name,
                    "value": value,
                    "trade_count": metrics.get("trade_count", 0),
                    "expectancy": metrics.get("expectancy"),
                    "total_pnl": metrics.get("total_pnl"),
                    "delta_expectancy_vs_baseline": _delta(metrics.get("expectancy"), baseline.get("expectancy")),
                    "delta_trade_count_vs_baseline": metrics.get("trade_count", 0) - baseline.get("trade_count", 0),
                }
            )

    unsupported_parameters = []
    if frame["volume_24h"].dropna().empty:
        unsupported_parameters.append("min_volume")
    if frame["price_drift_pct"].dropna().empty:
        unsupported_parameters.append("max_price_drift")
    if frame["event_amount"].dropna().empty:
        unsupported_parameters.append("whale_notional_threshold")
    if frame["wallets_count"].dropna().empty:
        unsupported_parameters.append("cluster_wallet_threshold")

    return {
        "status": "ok",
        "baseline": baseline,
        "parameter_effects": parameter_effects,
        "unsupported_parameters": unsupported_parameters,
    }


def _delta(value: float | None, baseline: float | None) -> float | None:
    if value is None or baseline is None:
        return None
    return round(float(value) - float(baseline), 4)


def _write_report(output_path: Path, report: Dict) -> Dict:
    report_path = output_path / "backtest.json"
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    report["report_file"] = str(report_path)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic replay/backtest for Ghost Trader.")
    parser.add_argument("--dataset", help="Structured replay CSV/JSON dataset.")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Directory for replay outputs.")
    parser.add_argument("--seed", default=42, type=int, help="Deterministic seed recorded in the report.")
    args = parser.parse_args()

    report = run_backtest(dataset_path=args.dataset, output_dir=args.output_dir, seed=args.seed)
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
