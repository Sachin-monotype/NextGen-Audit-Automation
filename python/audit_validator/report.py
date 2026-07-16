"""Validation report formatting and export."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .models import CheckResult, ValidationResult, ValidationStatus


def summarize(results: list[ValidationResult]) -> dict:
    by_status: dict[str, int] = {}
    for r in results:
        by_status[r.status.value] = by_status.get(r.status.value, 0) + 1

    templates: dict[str, int] = {}
    for r in results:
        templates[r.template_id] = templates.get(r.template_id, 0) + 1

    return {
        "total": len(results),
        "by_status": by_status,
        "unique_templates": len(templates),
        "templates": templates,
        "failed_operations": [r.operation for r in results if r.status == ValidationStatus.FAIL],
    }


def print_report(results: list[ValidationResult]) -> None:
    summary = summarize(results)
    print("\n" + "=" * 72)
    print("  AUDIT EVENT VALIDATION REPORT")
    print("=" * 72)
    print(f"  Total operations : {summary['total']}")
    print(f"  Templates used   : {summary['unique_templates']}")
    for status, count in sorted(summary["by_status"].items()):
        print(f"  {status:8s}         : {count}")

    failed = [r for r in results if r.status == ValidationStatus.FAIL]
    if failed:
        print("\n  FAILURES:")
        for r in failed:
            print(f"\n  ✗ {r.operation} (template: {r.template_id})")
            for check in r.failed_checks:
                loc = f" [{check.path}]" if check.path else ""
                print(f"      - [{check.layer}] {check.check}{loc}: {check.message}")

    warned = [r for r in results if r.status == ValidationStatus.WARN]
    if warned:
        print(f"\n  WARNINGS: {len(warned)} operation(s)")

    passed = [r for r in results if r.status == ValidationStatus.PASS]
    if passed:
        print(f"\n  PASSED: {len(passed)} operation(s)")

    print("\n" + "=" * 72 + "\n")


def print_raw_enriched_report(results: list[ValidationResult]) -> None:
    """Focused report for raw ↔ enriched correlation validation."""
    relevant = [
        r
        for r in results
        if r.template_id != "routing-key-coverage"
        and not any(c.check == "not_simulated" for c in r.checks)
    ]
    if not relevant:
        print("\n" + "=" * 72)
        print("  RAW ↔ ENRICHED VALIDATION")
        print("=" * 72)
        print("  No events captured on test queues during this run.")
        print("  Check platform routing to raw/enriched tap queues and GraphQL flow PASSes.")
        print("=" * 72 + "\n")
        return

    by_status: dict[str, list[ValidationResult]] = {}
    for r in relevant:
        by_status.setdefault(r.status.value, []).append(r)

    print("\n" + "=" * 72)
    print("  RAW ↔ ENRICHED VALIDATION")
    print("=" * 72)
    print(f"  Pairs validated : {len(relevant)}")
    for status in ("PASS", "FAIL", "WARN", "SKIP"):
        items = by_status.get(status, [])
        if items:
            print(f"  {status:4s}            : {len(items)}")

    failed = by_status.get("FAIL", [])
    if failed:
        print("\n  FAILURES (raw/enriched mismatch or missing enriched):")
        for r in failed:
            print(f"\n  ✗ {r.operation}")
            for check in r.failed_checks:
                loc = f" [{check.path}]" if check.path else ""
                print(f"      - [{check.layer}] {check.check}{loc}: {check.message}")

    warned = by_status.get("WARN", [])
    if warned:
        print(f"\n  WARNINGS ({len(warned)}):")
        for r in warned[:15]:
            msg = r.failed_checks[0].message if r.failed_checks else ""
            print(f"    ⚠ {r.operation}: {msg}")
        if len(warned) > 15:
            print(f"    … and {len(warned) - 15} more")

    passed = by_status.get("PASS", [])
    if passed:
        print(f"\n  PASSED ({len(passed)}):")
        for r in passed[:20]:
            print(f"    ✓ {r.operation}")
        if len(passed) > 20:
            print(f"    … and {len(passed) - 20} more")

    print("\n" + "=" * 72 + "\n")


def write_json_report(results: list[ValidationResult], output_path: Path) -> None:
    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "summary": summarize(results),
        "results": [
            {
                **{k: v for k, v in asdict(r).items() if k != "checks"},
                "checks": [asdict(c) for c in r.checks],
            }
            for r in results
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def load_validation_results(path: Path) -> list[ValidationResult]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[ValidationResult] = []
    for row in data.get("results") or []:
        checks = [
            CheckResult(
                layer=str(c.get("layer") or ""),
                check=str(c.get("check") or ""),
                status=ValidationStatus(str(c.get("status") or "FAIL")),
                message=str(c.get("message") or ""),
                path=c.get("path"),
            )
            for c in row.get("checks") or []
        ]
        out.append(
            ValidationResult(
                operation=str(row.get("operation") or ""),
                service=str(row.get("service") or ""),
                template_id=str(row.get("template_id") or ""),
                status=ValidationStatus(str(row.get("status") or "FAIL")),
                checks=checks,
                raw_path=row.get("raw_path"),
                enriched_path=row.get("enriched_path"),
            )
        )
    return out


def append_validation_results(path: Path, extra: list[ValidationResult]) -> None:
    combined = load_validation_results(path) + list(extra)
    write_json_report(combined, path)
