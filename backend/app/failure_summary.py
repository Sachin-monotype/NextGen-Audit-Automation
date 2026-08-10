"""Aggregate common Compare FAIL patterns across comparison-latest.json / QA store."""

from __future__ import annotations

import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .comparison_store import (
    _clean_app_ui_be_defaults,
    _clean_benign_client_ua_rows,
    _clean_import_provenance_notes,
    _clean_legacy_raw_envelope_rows,
    _clean_scope_rows,
    _dedupe_channel_variants,
    _load_for_target,
    _load_qa_results_prefer_mongo,
    store_audit_target,
)

logger = logging.getLogger(__name__)

_UNREACHABLE = re.compile(
    r"unreachable|vpn|timed?\s*out|forbidden|cloudflare|connection\s*refused",
    re.I,
)


def _bucket_notes(notes: str, *, field_path: str, source_system: str) -> str:
    n = (notes or "").strip()
    low = n.lower()
    if _UNREACHABLE.search(low):
        return "source_unreachable"
    if "typesense response missing" in low or ("discovery" in low and "missing" in low):
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
    project_root: Path,
    *,
    api_base: str = "http://localhost:3200",
    target: str | None = None,
) -> dict[str, Any]:
    audit_target = store_audit_target(project_root, target)

    if audit_target == "qa":
        data_map, _, _ = _load_qa_results_prefer_mongo(project_root, include_rows=True)
    else:
        data_map = _load_for_target(project_root, audit_target)

    data_map, _ = _dedupe_channel_variants(data_map)

    fail_rows: list[dict[str, Any]] = []
    for op, item in data_map.items():
        rows = item.get("rows") or []
        if not rows:
            continue
        cleaned, _ = _clean_scope_rows(rows)
        cleaned, _ = _clean_legacy_raw_envelope_rows(cleaned)
        cleaned, _ = _clean_import_provenance_notes(cleaned)
        cleaned, _ = _clean_benign_client_ua_rows(cleaned)
        cleaned, _ = _clean_app_ui_be_defaults(cleaned)
        for r in cleaned:
            if str(r.get("match_status") or "").upper() == "FAIL":
                r_copy = dict(r)
                if not r_copy.get("operation"):
                    r_copy["operation"] = op
                fail_rows.append(r_copy)

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
            if len(g["operations"]) == 1:
                g["mongo_query"] = _mongo_find_hint(op, path)
                g["curl"] = _investigate_curl(op, api_base)

    ranked = sorted(groups.values(), key=lambda x: (-x["count"], x["field_path"]))
    pattern_counts = Counter(g["pattern"] for g in ranked)
    ops_with_fails = len({r.get("operation") for r in fail_rows if r.get("operation")})

    logger.info(
        "Failure summary for target '%s': %d total FAIL row(s), %d unique failed pattern(s) across %d operation(s).",
        audit_target,
        len(fail_rows),
        len(ranked),
        ops_with_fails,
    )
    print(
        f"[FAILURE LOG] Target '{audit_target}': {len(fail_rows)} total FAIL row(s), "
        f"{len(ranked)} unique failed case pattern(s) across {ops_with_fails} operation(s)."
    )

    return {
        "audit_target": audit_target,
        "total_fail_rows": len(fail_rows),
        "distinct_patterns": len(ranked),
        "operations_with_fails": ops_with_fails,
        "pattern_counts": dict(pattern_counts.most_common()),
        "groups": ranked[:100],
    }
