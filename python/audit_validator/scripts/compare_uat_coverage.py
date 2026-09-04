#!/usr/bin/env python3
"""Compare the web automatable coverage catalog against the UAT comparison
results and write the events/scenarios that are missing to a file.

UAT results live in MongoDB Atlas (``AutomationResult.NextgenAuditCoparisionResult``,
configured via ``RESULTS_MONGO_URL_UAT`` / ``RESULTS_MONGO_DB_UAT`` /
``RESULTS_MONGO_COLLECTION_UAT`` in the project ``.env``). This script reads
straight from Mongo by default and falls back to the local
``reports/comparison-latest-uat.json`` snapshot only if Mongo is unreachable
or not configured.

An (event, scenario) pair is reported as missing only when it has no
document at all in Mongo. Scenarios that exist in Mongo but have failing
rows are left out — this reports coverage gaps, not test failures.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from audit_validator.operation_sources import operation_source_report
from audit_validator.scripts.build_coverage_workbook import (
    _automatable_web_targets,
    _canonical_ops,
    _catalog_scenarios_by_event,
    _norm_operation,
    _norm_scenario,
)

_KEY_RE = re.compile(r"^(?P<event>.+?)\((?P<scenario>[^)]*)\)$")

_ROOT = Path(__file__).resolve().parents[3]


def _load_uat_from_mongo(target: str = "uat") -> dict[str, dict] | None:
    """Return {scenario_key: item} from Atlas, or None if Mongo isn't usable."""
    backend_dir = _ROOT / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    try:
        from dotenv import load_dotenv

        load_dotenv(_ROOT / ".env")
    except Exception:
        pass
    try:
        from app.qa_results_store import load_all_scenarios, results_mongo_enabled
    except Exception as exc:
        print(f"[warn] could not import qa_results_store: {exc}", file=sys.stderr)
        return None
    if not results_mongo_enabled(target):
        print(f"[warn] RESULTS_MONGO_URL_{target.upper()} not configured", file=sys.stderr)
        return None
    data = load_all_scenarios(include_rows=False, target=target)
    if not data:
        print("[warn] Mongo query returned no scenarios", file=sys.stderr)
        return None
    return data


def _load_uat_from_file(path: Path) -> dict[str, dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_uat_keys(data: dict, *, canonical: dict[str, str]) -> dict[tuple[str, str], dict]:
    """Map (event, scenario) -> summary dict for every key in the UAT result."""
    out: dict[tuple[str, str], dict] = {}
    for key, payload in data.items():
        m = _KEY_RE.match(str(key).strip())
        if not m:
            continue
        event = _norm_operation(m.group("event"), canonical)
        scenario = _norm_scenario(m.group("scenario"))
        summary = (payload or {}).get("summary") or {}
        out[(event, scenario)] = summary
    return out


def compare(
    *,
    uat_data: dict,
) -> tuple[list[dict], dict]:
    catalog = operation_source_report().get("catalog") or []
    canonical = _canonical_ops(catalog)

    web_targets = _automatable_web_targets(catalog)
    by_event_catalog = _catalog_scenarios_by_event(web_targets, canonical=canonical)

    uat_by_key = _parse_uat_keys(uat_data, canonical=canonical)

    missing: list[dict] = []
    for event in sorted(by_event_catalog):
        for scenario in by_event_catalog[event]:
            summary = uat_by_key.get((event, scenario))
            if summary is not None:
                continue  # present in Mongo (regardless of pass/fail) — not "missing"
            missing.append(
                {"event": event, "scenario": scenario, "reason": "not present in UAT result"}
            )

    stats = {
        "total_web_targets": sum(len(v) for v in by_event_catalog.values()),
        "uat_keys": len(uat_by_key),
        "missing": len(missing),
    }
    return missing, stats


def _write_missing(missing: list[dict], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() == ".json":
        output.write_text(json.dumps(missing, indent=2), encoding="utf-8")
        return
    if output.suffix.lower() == ".csv":
        import csv

        with output.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["event", "scenario", "reason"])
            writer.writeheader()
            writer.writerows(missing)
        return
    lines = [f"{row['event']}({row['scenario']}) - {row['reason']}" for row in missing]
    output.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare web coverage catalog against UAT comparison results"
    )
    parser.add_argument(
        "--source",
        choices=["mongo", "file"],
        default="mongo",
        help="Where to read UAT results from (default: mongo, falls back to file)",
    )
    parser.add_argument(
        "--uat-file",
        type=Path,
        default=_ROOT / "reports" / "comparison-latest-uat.json",
        help="Local UAT snapshot, used with --source=file or as a Mongo fallback",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_ROOT / "reports" / "uat-missing-events.txt",
        help="Where to write the missing events (.txt, .csv, or .json)",
    )
    args = parser.parse_args()

    uat_data = None
    source_used = "file"
    if args.source == "mongo":
        uat_data = _load_uat_from_mongo("uat")
        if uat_data is not None:
            source_used = "mongo"
    if uat_data is None:
        uat_data = _load_uat_from_file(args.uat_file)
        source_used = "file"

    missing, stats = compare(uat_data=uat_data)
    _write_missing(missing, args.output)

    print(f"UAT source: {source_used}")
    print(
        f"Web targets: {stats['total_web_targets']} | "
        f"UAT keys: {stats['uat_keys']} | "
        f"Missing: {stats['missing']}"
    )
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
