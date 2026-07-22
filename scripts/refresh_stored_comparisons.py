#!/usr/bin/env python3
"""Re-run source validation for stored Result-tab ops; updates comparison-latest.json.

Prefer the backend venv (has pymysql when SOURCE_TRUTH=db):
  backend/.venv/bin/python scripts/refresh_stored_comparisons.py --since 2026-07-21
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "python"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ops", default="", help="Comma-separated operations (default: all in store)")
    ap.add_argument("--since", default="", help="Only ops compared on/after date YYYY-MM-DD")
    ap.add_argument("--with-fails", action="store_true", help="Only ops with failed>0 in store")
    args = ap.parse_args()

    store_path = REPO / "reports" / "comparison-latest.json"
    data = json.loads(store_path.read_text(encoding="utf-8")) if store_path.is_file() else {}

    if args.ops.strip():
        ops = [o.strip() for o in args.ops.split(",") if o.strip()]
    else:
        ops = sorted(data.keys())
        if args.since:
            ops = [
                o
                for o in ops
                if str(data[o].get("compared_at") or "")[:10] >= args.since[:10]
            ]
        if args.with_fails:
            ops = [
                o
                for o in ops
                if int((data[o].get("summary") or {}).get("failed") or 0) > 0
            ]

    if not ops:
        print("No operations to refresh", file=sys.stderr)
        return 1

    from audit_validator.source_validation.runner import run_source_validation

    sys.path.insert(0, str(REPO / "backend"))
    from app.comparison_store import save_batch_results

    job_id = str(uuid.uuid4())
    routing_path = REPO / "python" / "audit_validator" / "data" / "outbound-routing-map.json"
    routing = json.loads(routing_path.read_text(encoding="utf-8"))

    def _row_dict(r) -> dict:
        op = str(r.operation)
        base = op.split("(", 1)[0] if "(" in op else op
        return {
            "operation": r.operation,
            "field": r.field,
            "field_path": r.field_path,
            "node": r.node,
            "sub_node": r.sub_node,
            "layer": r.layer,
            "source_system": r.source_system,
            "source_api": r.source_api,
            "value_in_source": r.value_in_source,
            "value_in_enriched": r.value_in_enriched,
            "match_status": r.match_status,
            "notes": r.notes,
            "routing_key": routing.get(base, ""),
        }

    saved = 0

    def _on_operation_rows(operation: str, op_rows: list) -> None:
        nonlocal saved
        save_batch_results(
            REPO,
            rows=[_row_dict(r) for r in op_rows],
            job_id=job_id,
            job_kind="compare",
            compared_at=_now(),
        )
        saved += 1
        s = {
            "PASS": sum(1 for r in op_rows if r.match_status == "PASS"),
            "FAIL": sum(1 for r in op_rows if r.match_status == "FAIL"),
            "SKIP": sum(1 for r in op_rows if r.match_status == "SKIP"),
        }
        print(f"  ✓ {operation}: PASS={s['PASS']} FAIL={s['FAIL']} SKIP={s['SKIP']}")

    print(f"Refreshing {len(ops)} operation(s)…")
    report = run_source_validation(
        project_root=REPO,
        operations=ops,
        iteration=1,
        sample_source="fresh",
        progress=lambda msg: print(msg) if msg.strip().startswith(("▸", "  ✓", "  ⚠", "✖")) else None,
        on_operation_rows=_on_operation_rows,
    )
    rows = [_row_dict(r) for r in report.comparison_rows]
    save_batch_results(
        REPO,
        rows=rows,
        job_id=job_id,
        job_kind="compare",
        compared_at=_now(),
    )
    print(
        f"Done — PASS={report.passed} FAIL={report.failed} SKIP={report.skipped} "
        f"({saved} progressive saves)"
    )
    return 0 if report.failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
