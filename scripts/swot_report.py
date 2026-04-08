#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List


def build_swot_report(
    analysis_json_path: str = "reports/performance/summary.json",
    backtest_json_path: str = "reports/performance/backtest.json",
    output_dir: str = "reports/performance",
) -> Dict:
    analysis = _load_json(analysis_json_path)
    backtest = _load_json(backtest_json_path)
    verdict = _classify_verdict(analysis, backtest)
    swot = _build_swot(analysis, backtest)
    sensitivity = _summarize_sensitivity(backtest)

    report = {
        "strategy_summary": _strategy_summary(analysis, backtest),
        "data_sources_used": _data_sources_used(analysis, backtest),
        "what_was_measurable_vs_not": {
            "measurable": analysis.get("what_was_measurable", []),
            "not_measurable": analysis.get("what_was_not_measurable", []),
        },
        "core_performance_table": analysis.get("core", {}),
        "venue_comparison": analysis.get("venue_comparison", []),
        "category_comparison": analysis.get("category_comparison", []),
        "signal_source_comparison": analysis.get("signal_source_comparison", []),
        "sensitivity_summary": sensitivity,
        "swot": swot,
        "final_verdict": verdict,
    }

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    report_path = output_path / "swot_report.md"
    json_path = output_path / "swot_report.json"

    report_path.write_text(_render_markdown(report), encoding="utf-8")
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    report["report_files"] = {"markdown": str(report_path), "json": str(json_path)}
    return report


def _classify_verdict(analysis: Dict, backtest: Dict) -> Dict:
    evidence = analysis.get("evidence", {})
    core = analysis.get("core", {})
    live_closed = int(evidence.get("live_paper_closed", 0) or 0)
    expectancy = core.get("expectancy")
    profit_factor = core.get("profit_factor")
    max_drawdown = core.get("max_drawdown")
    backtest_status = backtest.get("status")
    backtest_test_expectancy = backtest.get("test", {}).get("expectancy")
    sensitivity = backtest.get("sensitivity", {})
    sensitivity_effects = sensitivity.get("parameter_effects", [])

    if (
        live_closed >= 30
        and expectancy is not None
        and expectancy > 0
        and profit_factor is not None
        and profit_factor >= 1.20
        and max_drawdown is not None
        and max_drawdown <= 0.15
        and (backtest_status != "ok" or backtest_test_expectancy is None or backtest_test_expectancy >= 0)
        and not _is_fragile(sensitivity_effects)
    ):
        return {
            "verdict": "GO",
            "reason": "There is a meaningful non-synthetic paper sample, positive expectancy, acceptable drawdown, and no clear out-of-sample failure in the supported replay.",
            "improvements_first": [],
        }

    if (
        live_closed >= 30
        and (
            (expectancy is not None and expectancy <= 0)
            or (profit_factor is not None and profit_factor < 1.0)
            or (max_drawdown is not None and max_drawdown > 0.25)
            or (backtest_status == "ok" and backtest_test_expectancy is not None and backtest_test_expectancy < 0)
        )
    ):
        return {
            "verdict": "NO-GO",
            "reason": "The available non-synthetic sample is large enough to evaluate and the measured performance or supported replay is not acceptable.",
            "improvements_first": [],
        }

    return {
        "verdict": "IMPROVE FIRST",
        "reason": "Insufficient evidence for a production-style go decision. Current live paper sample is too small and the supported replay is either missing or not broad enough to prove edge.",
        "improvements_first": [
            "Collect at least 30 non-synthetic closed live paper trades before making a go/no-go call.",
            "Persist and review decision_audit rejections to find the real bottleneck between coverage and trade quality.",
            "Gather a trustworthy historical crypto replay dataset before tuning thresholds aggressively.",
            "Track venue-level realized vs unrealized PnL over multiple days to detect fragile venue behavior.",
            "Separate threshold tuning from proof-mode DBs; never optimize against synthetic verification samples.",
        ],
    }


def _build_swot(analysis: Dict, backtest: Dict) -> Dict:
    venue_rows = analysis.get("venue_comparison", [])
    category_rows = analysis.get("category_comparison", [])
    source_rows = analysis.get("signal_source_comparison", [])

    return {
        "strengths": [
            "The runtime now has venue-aware attribution, so paper results can be separated by venue, category, and signal family.",
            "The strategy is conservative and category-aware; it avoids forcing trades when liquidity/spread guards fail.",
            "Multi-venue PAPER execution makes it possible to compare Polymarket, Binance Futures, and Binance Spot behavior on the same crypto signal family.",
        ],
        "weaknesses": [
            "The current repo still has little or no non-synthetic live paper evidence, so alpha is not proven yet.",
            "Whale/orderflow quality depends on external APIs and may still suffer from sparse or delayed signal coverage.",
            "Non-crypto historical replay remains unsupported with the public data currently available in the repo.",
        ],
        "opportunities": [
            "CRYPTO venue comparison can reveal whether futures or spot is contributing better expectancy with lower drawdown.",
            "Decision-audit rejection analysis can identify which thresholds are blocking too many otherwise-valid trades.",
            "A trustworthy replay dataset can unlock walk-forward threshold sensitivity without touching synthetic verification trades.",
        ],
        "threats": [
            "API changes, liquidity collapse, or spread widening can invalidate previously acceptable PAPER behavior.",
            "Whale spoofing or delayed activity ingestion can create false positives in the orderflow leg.",
            "Overfitting remains a real risk if threshold changes are made before enough live paper data is collected.",
        ],
        "context": {
            "venues_seen": [row.get("venue") for row in venue_rows],
            "categories_seen": [row.get("category") for row in category_rows],
            "signal_families_seen": [row.get("signal_family") for row in source_rows],
            "backtest_status": backtest.get("status"),
        },
    }


def _summarize_sensitivity(backtest: Dict) -> Dict:
    sensitivity = backtest.get("sensitivity", {})
    effects = sensitivity.get("parameter_effects", [])
    if not effects:
        return {
            "status": sensitivity.get("status", "insufficient_evidence"),
            "most_impactful_parameters": [],
            "strategy_breakers": [],
            "balanced_ranges": [],
            "notes": ["Sensitivity evidence is currently insufficient or unavailable."],
        }

    sorted_effects = sorted(
        [effect for effect in effects if effect.get("delta_expectancy_vs_baseline") is not None],
        key=lambda effect: abs(effect.get("delta_expectancy_vs_baseline", 0.0)),
        reverse=True,
    )
    most_impactful = []
    for effect in sorted_effects[:5]:
        most_impactful.append(
            {
                "parameter": effect["parameter"],
                "value": effect["value"],
                "delta_expectancy": effect["delta_expectancy_vs_baseline"],
                "delta_trade_count": effect["delta_trade_count_vs_baseline"],
            }
        )

    strategy_breakers = [
        effect
        for effect in effects
        if effect.get("trade_count", 0) == 0 or (effect.get("expectancy") is not None and effect.get("expectancy") < 0)
    ]
    balanced = [
        effect
        for effect in effects
        if effect.get("trade_count", 0) > 0 and effect.get("delta_expectancy_vs_baseline") is not None and abs(effect["delta_expectancy_vs_baseline"]) <= 0.25
    ]

    return {
        "status": sensitivity.get("status", "ok"),
        "most_impactful_parameters": most_impactful,
        "strategy_breakers": strategy_breakers[:5],
        "balanced_ranges": balanced[:5],
        "notes": [f"Unsupported parameters: {', '.join(sensitivity.get('unsupported_parameters', []))}" if sensitivity.get("unsupported_parameters") else "All configured sensitivity parameters were evaluable in the supplied replay dataset."],
    }


def _strategy_summary(analysis: Dict, backtest: Dict) -> str:
    core = analysis.get("core", {})
    evidence = analysis.get("evidence", {})
    return (
        "Ghost Trader is now measurable as a venue-aware PAPER strategy, but the repo currently lacks enough non-synthetic live paper evidence to claim statistical edge. "
        f"Live paper closed trades: {evidence.get('live_paper_closed', 0)}. "
        f"Core expectancy: {core.get('expectancy')}. "
        f"Supported replay status: {backtest.get('status', 'unknown')}."
    )


def _data_sources_used(analysis: Dict, backtest: Dict) -> List[str]:
    db_paths = analysis.get("db_paths", [])
    sources = [f"SQLite paper trade DB: {db_path}" for db_path in db_paths]
    if backtest.get("dataset_path"):
        sources.append(f"Structured replay dataset: {backtest['dataset_path']}")
    else:
        sources.append("No replay dataset supplied; backtest report is an insufficiency assessment.")
    return sources


def _is_fragile(parameter_effects: List[Dict]) -> bool:
    if not parameter_effects:
        return True
    large_negative_moves = [
        effect
        for effect in parameter_effects
        if effect.get("delta_expectancy_vs_baseline") is not None and effect["delta_expectancy_vs_baseline"] < -0.50
    ]
    return len(large_negative_moves) >= 3


def _load_json(path: str) -> Dict:
    file_path = Path(path)
    if not file_path.exists():
        return {}
    return json.loads(file_path.read_text(encoding="utf-8"))


def _render_markdown(report: Dict) -> str:
    verdict = report.get("final_verdict", {})
    swot = report.get("swot", {})
    lines = [
        "# Strategy Evaluation Report",
        "",
        "## Strategy Summary",
        report.get("strategy_summary", ""),
        "",
        "## Data Sources Used",
    ]
    lines.extend([f"- {source}" for source in report.get("data_sources_used", [])])
    lines.extend(
        [
            "",
            "## What Was Measurable",
        ]
    )
    lines.extend([f"- {item}" for item in report.get("what_was_measurable_vs_not", {}).get("measurable", [])])
    lines.extend(["", "## What Was Not Measurable"])
    lines.extend([f"- {item}" for item in report.get("what_was_measurable_vs_not", {}).get("not_measurable", [])])
    lines.extend(
        [
            "",
            "## Final Verdict",
            f"- Verdict: {verdict.get('verdict', 'IMPROVE FIRST')}",
            f"- Reason: {verdict.get('reason', '')}",
        ]
    )
    if verdict.get("improvements_first"):
        lines.extend(["- Improve first:"])
        lines.extend([f"  - {item}" for item in verdict["improvements_first"]])
    lines.extend(["", "## SWOT"])
    for section in ("strengths", "weaknesses", "opportunities", "threats"):
        lines.append(f"### {section.capitalize()}")
        lines.extend([f"- {item}" for item in swot.get(section, [])])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build SWOT and final verdict report for Ghost Trader.")
    parser.add_argument("--analysis-json", default="reports/performance/summary.json")
    parser.add_argument("--backtest-json", default="reports/performance/backtest.json")
    parser.add_argument("--output-dir", default="reports/performance")
    args = parser.parse_args()

    report = build_swot_report(
        analysis_json_path=args.analysis_json,
        backtest_json_path=args.backtest_json,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
