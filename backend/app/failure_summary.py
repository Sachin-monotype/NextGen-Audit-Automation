"""Aggregate common Compare FAIL patterns across comparison-latest.json."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from .comparison_store import list_latest

_UNREACHABLE = re.compile(
    r"unreachable|vpn|timed?\s*out|forbidden|cloudflare|connection\s*refused",
    re.I,
)
_VALUE_MISMATCH = re.compile(r"^$")


def _bucket_notes(notes: str, *, field_path: str, source_system: str) -> str:
    n = (notes or "").strip()
    low = n.lower()
    if _UNREACHABLE.search(low):
        return "source_unreachable"
    if "typesense response missing" in low or "discovery" in low and "missing" in low:
        return "typesense_missing_field"
    if "cms response missing" in low:
        return "cms_missing_field"
    if "ums response missing" in low:
        return "ums_missing_field"
    if "language" in field_path.lower() or "locale" in field_path.lower():
        return "language_mismatch_or_missing"
    if "imported/byof" in low:
        return "imported_font_out_of_scope"
    if not n:
        return "value_mismatch"
    # Normalize: strip UUIDs / long numbers so similar notes collapse
    cleaned = re.sub(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "{id}",
        n,
        flags=re.I,
    )
    cleaned = re.sub(r"\b\d{5,}\b", "{n}", cleaned)
    return cleaned[:160]


def _mongo_find_hint(operation: str, field_path: str) -> str:
    return (
        f'db.enriched.find({{"source.operation":"{operation}"}})'
        f'.sort({{occurredAt:-1}}).limit(1)\n'
        f'// Inspect path: {field_path}'
    )


def _investigate_curl(operation: str, base: str = "http://localhost:3200") -> str:
    return f'curl -s "{base}/api/curl/{operation}" | jq .'


def build_failure_summary(
    project_root,
    *,
    api_base: str = "http://localhost:3200",
) -> dict[str, Any]:
    data = list_latest(project_root)
    rows = list(data.get("rows") or [])
    fail_rows = [r for r in rows if str(r.get("match_status") or "").upper() == "FAIL"]

    groups: dict[str, dict[str, Any]] = {}
    for r in fail_rows:
        op = str(r.get("operation") or "")
        path = str(r.get("field_path") or r.get("field") or "")
        src = str(r.get("source_system") or "")
        notes = str(r.get("notes") or "")
        bucket = _bucket_notes(notes, field_path=path, source_system=src)
        key = f"{src}|{path}|{bucket}"
        g = groups.get(key)
        if not g:
            g = {
                "key": key,
                "source_system": src,
                "field_path": path,
                "pattern": bucket,
                "sample_notes": notes[:300],
                "count": 0,
                "operations": [],
                "sample_enriched": str(r.get("value_in_enriched") or "")[:200],
                "sample_source": str(r.get("value_in_source") or "")[:200],
                "mongo_query": _mongo_find_hint(op, path),
                "curl": _investigate_curl(op, api_base),
            }
            groups[key] = g
        g["count"] += 1
        if op and op not in g["operations"]:
            g["operations"].append(op)
            # Keep curl / mongo hint on first op; refresh if empty
            if len(g["operations"]) == 1:
                g["mongo_query"] = _mongo_find_hint(op, path)
                g["curl"] = _investigate_curl(op, api_base)

    ranked = sorted(groups.values(), key=lambda x: (-x["count"], x["field_path"]))
    pattern_counts = Counter(g["pattern"] for g in ranked)

    return {
        "total_fail_rows": len(fail_rows),
        "distinct_patterns": len(ranked),
        "operations_with_fails": len({r.get("operation") for r in fail_rows if r.get("operation")}),
        "pattern_counts": dict(pattern_counts.most_common()),
        "groups": ranked[:80],
    }
