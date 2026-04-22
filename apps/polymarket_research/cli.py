from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

from .config import PolymarketResearchSettings
from .repository import PolymarketResearchRepository
from .runtime import PolymarketResearchRuntime


SUMMARY_SECTIONS = [
    "DISCOVERY_SOURCE_SUMMARY",
    "SHADOW_PROMOTION_SUMMARY",
    "WALLET_PROVENANCE_SUMMARY",
    "PRIORITY_WATCHLIST_SUMMARY",
    "IDENTITY_RESOLUTION_SUMMARY",
    "LONG_HORIZON_WATCHLIST_SUMMARY",
    "SPECIALIST_WALLET_SCORE_SUMMARY",
    "OBSERVATION_PROGRESS_SUMMARY",
    "PILOT_COPY_ADMISSION_SUMMARY",
    "LINKED_WALLET_EVIDENCE_SUMMARY",
    "SHADOW_EVIDENCE_BACKFILL_SUMMARY",
    "SHADOW_REPLAY_SUMMARY",
]


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _print_json_section(name: str, payload: Any) -> None:
    print(name)
    print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))


def _settings_from_args(args: argparse.Namespace) -> PolymarketResearchSettings:
    settings = PolymarketResearchSettings()
    if getattr(args, "db_path", None):
        settings.db_path = str(args.db_path)
    if getattr(args, "source_db_path", None):
        settings.source_db_path = str(args.source_db_path)
    return settings


def _repository_from_args(args: argparse.Namespace) -> PolymarketResearchRepository:
    settings = _settings_from_args(args)
    return PolymarketResearchRepository(settings.db_path, settings.source_db_path)


def _run_summary(args: argparse.Namespace) -> int:
    runtime = PolymarketResearchRuntime(_settings_from_args(args))
    summary = runtime.run_once()
    for name in SUMMARY_SECTIONS:
        _print_json_section(name, summary.get(name.lower(), {}))
    _print_json_section("POLYMARKET_RESEARCH_SUMMARY_JSON", summary)
    return 0


def _run_watchlist_list(args: argparse.Namespace) -> int:
    repository = _repository_from_args(args)
    rows = [_row_to_dict(row) for row in repository.fetch_watchlist_rows()]
    _print_json_section("WATCHLIST_ROWS", rows)
    return 0


def _run_watchlist_add(args: argparse.Namespace) -> int:
    repository = _repository_from_args(args)
    row = repository.add_watchlist_row(
        display_name=args.display_name,
        profile_ref=args.profile_ref,
        priority_rank=args.priority_rank,
        priority_mode=args.priority_mode,
        target_specialization=args.target_specialization,
        notes=args.notes,
    )
    _print_json_section("WATCHLIST_ROW_ADDED", _row_to_dict(row))
    return 0


def _run_watchlist_link(args: argparse.Namespace) -> int:
    repository = _repository_from_args(args)
    row = repository.link_watchlist_wallet(
        row_id=args.id,
        wallet_address=args.wallet_address,
        notes=args.notes,
    )
    _print_json_section("WATCHLIST_ROW_LINKED", _row_to_dict(row))
    return 0


def _run_watchlist_approve_pilot(args: argparse.Namespace) -> int:
    repository = _repository_from_args(args)
    row = repository.approve_watchlist_pilot(
        row_id=args.id,
        approved=not bool(args.revoke),
        notes=args.notes,
    )
    _print_json_section("WATCHLIST_ROW_PILOT_APPROVAL_UPDATED", _row_to_dict(row))
    return 0


def _add_db_path_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db-path",
        help="Override SQLite DB path. Defaults to GHOST_TRADER_DB_PATH or data/ghost_trader.db.",
    )
    parser.add_argument(
        "--source-db-path",
        help="Override linked-wallet evidence source DB. Defaults to POLYMARKET_RESEARCH_SOURCE_DB_PATH.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Polymarket research lane CLI")
    subparsers = parser.add_subparsers(dest="command")

    summary_parser = subparsers.add_parser("summary", help="Build and print the research summary")
    _add_db_path_argument(summary_parser)
    summary_parser.set_defaults(func=_run_summary)

    list_parser = subparsers.add_parser("watchlist-list", help="List priority watchlist rows")
    _add_db_path_argument(list_parser)
    list_parser.set_defaults(func=_run_watchlist_list)

    link_parser = subparsers.add_parser("watchlist-link", help="Manually link a watchlist row to a wallet")
    _add_db_path_argument(link_parser)
    link_parser.add_argument("--id", type=int, required=True, help="Watchlist row id")
    link_parser.add_argument("--wallet-address", required=True, help="Verified 0x-prefixed wallet address")
    link_parser.add_argument("--notes", default=None, help="Manual verification note")
    link_parser.set_defaults(func=_run_watchlist_link)

    approve_parser = subparsers.add_parser("watchlist-approve-pilot", help="Approve or revoke low-risk paper pilot copy for a linked watchlist wallet")
    _add_db_path_argument(approve_parser)
    approve_parser.add_argument("--id", type=int, required=True, help="Watchlist row id")
    approve_parser.add_argument("--notes", default=None, help="Operator approval note")
    approve_parser.add_argument("--revoke", action="store_true", help="Revoke pilot approval instead of approving")
    approve_parser.set_defaults(func=_run_watchlist_approve_pilot)

    add_parser = subparsers.add_parser("watchlist-add", help="Add a handle/profile to the priority watchlist")
    _add_db_path_argument(add_parser)
    add_parser.add_argument("--display-name", required=True, help="Display name or handle")
    add_parser.add_argument("--profile-ref", required=True, help="Profile URL or operator reference")
    add_parser.add_argument("--priority-rank", type=int, required=True, help="Lower rank means higher priority")
    add_parser.add_argument("--priority-mode", default="normal", help="normal or fast_track_shadow")
    add_parser.add_argument("--target-specialization", default="CRYPTO", help="Target specialization tag")
    add_parser.add_argument("--notes", default=None, help="Operator note")
    add_parser.set_defaults(func=_run_watchlist_add)

    parser.set_defaults(func=_run_summary)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if not raw_args:
        raw_args = ["summary"]
    parser = build_parser()
    args = parser.parse_args(raw_args)
    try:
        return int(args.func(args))
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
