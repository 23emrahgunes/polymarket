from __future__ import annotations

import re
from typing import Any, Iterable, List


HEX_ALIAS_PATTERN = re.compile(r"^(0x)?[0-9a-f]{16,}$")


def normalize_market_alias(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered in {"none", "null"}:
        return None
    return lowered


def expand_market_alias_variants(value: Any) -> List[str]:
    normalized = normalize_market_alias(value)
    if not normalized:
        return []

    variants = [normalized]
    compact = normalized[2:] if normalized.startswith("0x") else normalized
    if HEX_ALIAS_PATTERN.fullmatch(normalized) and compact and compact not in variants:
        variants.append(compact)
    if HEX_ALIAS_PATTERN.fullmatch(compact):
        prefixed = f"0x{compact}"
        if prefixed not in variants:
            variants.append(prefixed)
    return variants


def collect_alias_candidates(*values: Any, extra: Iterable[Any] | None = None) -> List[str]:
    ordered: List[str] = []
    seen = set()

    def _consume(candidate: Any) -> None:
        if isinstance(candidate, (list, tuple, set)):
            for item in candidate:
                _consume(item)
            return
        for normalized in expand_market_alias_variants(candidate):
            if normalized in seen:
                continue
            seen.add(normalized)
            ordered.append(normalized)

    for value in values:
        _consume(value)
    if extra is not None:
        _consume(list(extra))
    return ordered


def build_market_aliases(
    market_id: Any,
    token_id: Any = None,
    token_ids: Iterable[Any] | None = None,
    extra_aliases: Iterable[Any] | None = None,
) -> List[str]:
    return collect_alias_candidates(market_id, token_id, list(token_ids or []), extra=extra_aliases)


def event_is_meaningful_for_lazy_lookup(
    event: dict,
    min_amount_usd: float = 100.0,
    min_cluster_wallets: int = 3,
) -> bool:
    amount = float(event.get("amount", 0.0) or 0.0)
    wallets_count = int(event.get("wallets_count", 1) or 1)
    event_type = str(event.get("type", "")).upper()

    if event_type == "CLUSTER_DETECTED":
        return wallets_count >= min_cluster_wallets or amount >= min_amount_usd
    return amount >= min_amount_usd
