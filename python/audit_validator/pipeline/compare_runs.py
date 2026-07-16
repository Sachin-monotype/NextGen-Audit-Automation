"""Compare backlog-only validation vs fresh E2E validation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models import ValidationStatus


@dataclass
class RunSnapshot:
    label: str
    raw_correlations: int = 0
    enriched_correlations: int = 0
    operations_raw: set[str] = field(default_factory=set)
    operations_enriched: set[str] = field(default_factory=set)
    operations_paired: set[str] = field(default_factory=set)
    raw_without_enriched: list[str] = field(default_factory=list)
    enriched_without_raw: list[str] = field(default_factory=list)
    routing_keys: set[str] = field(default_factory=set)
    pass_ops: set[str] = field(default_factory=set)
    fail_ops: set[str] = field(default_factory=set)
    warn_ops: set[str] = field(default_factory=set)


def _load_validation(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    return data.get("results", [])


def _checks_by_name(row: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for check in row.get("checks", []):
        name = check.get("check") or check.get("name")
        status = check.get("status")
        if name and status:
            out[str(name)] = str(status)
    return out


def snapshot_from_validation(path: Path, *, label: str) -> RunSnapshot:
    snap = RunSnapshot(label=label)
    for row in _load_validation(path):
        op = str(row.get("operation") or "unknown")
        status = str(row.get("status") or "")
        checks = _checks_by_name(row)

        if op == "routing-key" or row.get("template_id") == "routing-key-coverage":
            continue

        if checks.get("enriched_timeout") == ValidationStatus.FAIL.value:
            snap.raw_without_enriched.append(op)
            snap.operations_raw.add(op)
            snap.fail_ops.add(op)
            continue

        if checks.get("missing_raw") in {ValidationStatus.WARN.value, "WARN"}:
            snap.enriched_without_raw.append(op)
            snap.operations_enriched.add(op)
            snap.warn_ops.add(op)
            continue

        if checks.get("query_no_enrichment") == ValidationStatus.PASS.value:
            snap.operations_raw.add(op)
            snap.pass_ops.add(op)
            continue

        if status == ValidationStatus.PASS.value:
            snap.operations_paired.add(op)
            snap.operations_raw.add(op)
            snap.operations_enriched.add(op)
            snap.pass_ops.add(op)
        elif status == ValidationStatus.FAIL.value:
            snap.fail_ops.add(op)
            if op in snap.operations_raw or checks:
                snap.operations_raw.add(op)
        elif status == ValidationStatus.WARN.value:
            snap.warn_ops.add(op)
            snap.operations_raw.add(op)
            snap.operations_enriched.add(op)

        rk = row.get("routing_key") or checks.get("routing_key")
        if isinstance(rk, str) and rk:
            snap.routing_keys.add(rk)

    snap.raw_without_enriched = sorted(set(snap.raw_without_enriched))
    snap.enriched_without_raw = sorted(set(snap.enriched_without_raw))
    return snap


def compare_runs(
    backlog_path: Path,
    fresh_path: Path,
) -> dict[str, Any]:
    backlog = snapshot_from_validation(backlog_path, label="backlog")
    fresh = snapshot_from_validation(fresh_path, label="fresh")

    return {
        "backlog": _snap_dict(backlog),
        "fresh": _snap_dict(fresh),
        "diff": {
            "operations_only_in_backlog": sorted(backlog.operations_paired - fresh.operations_paired),
            "operations_only_in_fresh": sorted(fresh.operations_paired - backlog.operations_paired),
            "paired_in_both": sorted(backlog.operations_paired & fresh.operations_paired),
            "raw_without_enriched_backlog": backlog.raw_without_enriched,
            "raw_without_enriched_fresh": fresh.raw_without_enriched,
            "enriched_without_raw_backlog": backlog.enriched_without_raw,
            "enriched_without_raw_fresh": fresh.enriched_without_raw,
            "routing_keys_only_in_backlog": sorted(backlog.routing_keys - fresh.routing_keys),
            "routing_keys_only_in_fresh": sorted(fresh.routing_keys - backlog.routing_keys),
            "fail_only_in_backlog": sorted(backlog.fail_ops - fresh.fail_ops),
            "fail_only_in_fresh": sorted(fresh.fail_ops - backlog.fail_ops),
        },
        "summary": {
            "backlog_paired": len(backlog.operations_paired),
            "fresh_paired": len(fresh.operations_paired),
            "backlog_raw_no_enriched": len(backlog.raw_without_enriched),
            "fresh_raw_no_enriched": len(fresh.raw_without_enriched),
            "backlog_enriched_no_raw": len(backlog.enriched_without_raw),
            "fresh_enriched_no_raw": len(fresh.enriched_without_raw),
        },
    }


def _snap_dict(snap: RunSnapshot) -> dict[str, Any]:
    return {
        "label": snap.label,
        "operations_raw": sorted(snap.operations_raw),
        "operations_enriched": sorted(snap.operations_enriched),
        "operations_paired": sorted(snap.operations_paired),
        "raw_without_enriched": snap.raw_without_enriched,
        "enriched_without_raw": snap.enriched_without_raw,
        "routing_keys": sorted(snap.routing_keys),
        "pass": sorted(snap.pass_ops),
        "fail": sorted(snap.fail_ops),
        "warn": sorted(snap.warn_ops),
    }


def print_compare_report(data: dict[str, Any]) -> None:
    summary = data.get("summary", {})
    diff = data.get("diff", {})

    print("\n" + "=" * 72)
    print("  BACKLOG vs FRESH E2E COMPARISON")
    print("=" * 72)
    print(f"  Paired operations (backlog)     : {summary.get('backlog_paired', 0)}")
    print(f"  Paired operations (fresh)       : {summary.get('fresh_paired', 0)}")
    print(f"  Raw without enriched (backlog)  : {summary.get('backlog_raw_no_enriched', 0)}")
    print(f"  Raw without enriched (fresh)    : {summary.get('fresh_raw_no_enriched', 0)}")
    print(f"  Enriched without raw (backlog)  : {summary.get('backlog_enriched_no_raw', 0)}")
    print(f"  Enriched without raw (fresh)    : {summary.get('fresh_enriched_no_raw', 0)}")

    def _print_list(title: str, items: list[str], *, limit: int = 25) -> None:
        if not items:
            return
        print(f"\n  {title} ({len(items)}):")
        for item in items[:limit]:
            print(f"    - {item}")
        if len(items) > limit:
            print(f"    ... and {len(items) - limit} more")

    _print_list("Resolver failed to enrich (backlog)", diff.get("raw_without_enriched_backlog", []))
    _print_list("Resolver failed to enrich (fresh)", diff.get("raw_without_enriched_fresh", []))
    _print_list("Enriched orphan — no raw (backlog)", diff.get("enriched_without_raw_backlog", []))
    _print_list("Enriched orphan — no raw (fresh)", diff.get("enriched_without_raw_fresh", []))
    _print_list("Paired only in backlog", diff.get("operations_only_in_backlog", []))
    _print_list("Paired only in fresh run", diff.get("operations_only_in_fresh", []))
    _print_list("Fail only in backlog", diff.get("fail_only_in_backlog", []))
    _print_list("Fail only in fresh", diff.get("fail_only_in_fresh", []))
    print("=" * 72)
