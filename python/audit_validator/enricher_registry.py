"""Resolver enricher registry — aligned with mt-audit-log-resolver-service bootstrap."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).resolve().parent / "data" / "enricher_registry.json"


@lru_cache(maxsize=1)
def _load() -> dict:
    with _DATA.open(encoding="utf-8") as fh:
        return json.load(fh)


def registered_enrichers() -> frozenset[str]:
    return frozenset(_load().get("registered", []))


def disabled_enrichers() -> frozenset[str]:
    return frozenset(_load().get("disabled", []))


def not_enriched_yet() -> frozenset[str]:
    return frozenset(_load().get("not_enriched_yet", []))


def breaking_enrichers() -> frozenset[str]:
    return frozenset(_load().get("breaking", []))


def opt_out_raw_operations() -> frozenset[str]:
    return frozenset(_load().get("opt_out_raw", []))


def notification_ops_no_enricher() -> frozenset[str]:
    return frozenset(_load().get("notification_ops_no_enricher", []))


def should_simulate(operation: str) -> bool:
    """False for resolver-disabled ops (no enricher / feature off)."""
    return operation not in disabled_enrichers()


def enrichment_expected(operation: str) -> bool:
    """True when dev-confirmed enricher is active and enrichment is expected."""
    if operation in opt_out_raw_operations():
        return False
    if operation in disabled_enrichers():
        return False
    if operation in not_enriched_yet():
        return False
    if operation in notification_ops_no_enricher():
        return False
    return operation in registered_enrichers()
