"""
Resolver outbound routing map — authoritative operation → routing key bindings.

Source: mt-audit-log-resolver-service branch `m2m_node_cache_temp`
        config/outbound-routing-map.json

The resolver publishes enriched audit events to exchange `mt.platform.events`
using these routing keys. Pair raw ↔ enriched on `xCorrelationId`.
"""

from __future__ import annotations

import json
from pathlib import Path

_MAP_PATH = Path(__file__).resolve().parent.parent / "data" / "outbound-routing-map.json"
_ALIASES_PATH = Path(__file__).resolve().parent.parent / "data" / "operation_routing_aliases.json"


def _load_map() -> dict[str, str]:
    with _MAP_PATH.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid routing map at {_MAP_PATH}")
    return {str(k): str(v) for k, v in data.items()}


OPERATION_TO_ROUTING_KEY: dict[str, str] = _load_map()


def _load_aliases() -> dict[str, list[str]]:
    if not _ALIASES_PATH.is_file():
        return {}
    with _ALIASES_PATH.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid routing aliases at {_ALIASES_PATH}")
    out: dict[str, list[str]] = {}
    for op, keys in data.items():
        if isinstance(keys, list):
            out[str(op)] = [str(k) for k in keys]
    return out


OPERATION_ROUTING_ALIASES: dict[str, list[str]] = _load_aliases()

_alias_values = {rk for keys in OPERATION_ROUTING_ALIASES.values() for rk in keys}
RESOLVER_ROUTING_KEYS: frozenset[str] = frozenset(OPERATION_TO_ROUTING_KEY.values()) | frozenset(
    _alias_values
)

RESOLVER_ROUTING_KEYS_LIST: list[str] = sorted(RESOLVER_ROUTING_KEYS)

RESOLVER_MAPPED_OPERATIONS: frozenset[str] = frozenset(OPERATION_TO_ROUTING_KEY.keys())


def expected_routing_key(operation: str) -> str | None:
    return OPERATION_TO_ROUTING_KEY.get(operation)


def acceptable_routing_keys(operation: str) -> frozenset[str]:
    keys: set[str] = set(OPERATION_ROUTING_ALIASES.get(operation, []))
    primary = OPERATION_TO_ROUTING_KEY.get(operation)
    if primary:
        keys.add(primary)
    return frozenset(keys)


def routing_key_matches(operation: str, actual: str | None) -> bool:
    if not actual:
        return False
    return actual in acceptable_routing_keys(operation)


def operations_for_routing_key(routing_key: str) -> list[str]:
    ops = {op for op, rk in OPERATION_TO_ROUTING_KEY.items() if rk == routing_key}
    for op, aliases in OPERATION_ROUTING_ALIASES.items():
        if routing_key in aliases:
            ops.add(op)
    return sorted(ops)
