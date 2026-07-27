#!/usr/bin/env python3
"""Report GraphQL catalog ops missing TestRail case ids (Generate-in-UI gaps).

Usage:
  cd python && python -m audit_validator.scripts.gql_ui_gap_report
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

from audit_validator.operation_sources import operation_source_report
from audit_validator.ui_case_recipes import recipe_for
from audit_validator.ui_testrail_map import case_id_for_selection_item


def main() -> int:
    root = Path(__file__).resolve().parents[3]
    catalog = operation_source_report().get("catalog") or []
    gql = [c for c in catalog if c.get("kind") == "graphql"]

    missing: list[dict] = []
    has: list[dict] = []
    for item in gql:
        op = str(item.get("operation") or "")
        tp = str(item.get("touchpoint") or "")
        cid = case_id_for_selection_item(item)
        row = {
            "id": item.get("id"),
            "operation": op,
            "touchpoint": tp,
            "label": item.get("label"),
            "case_id": cid,
            "has_recipe": bool(recipe_for(op, tp)),
        }
        (has if cid else missing).append(row)

    by_op = defaultdict(list)
    for m in missing:
        by_op[m["operation"]].append(m)

    report = {
        "summary": {
            "graphql_catalog_items": len(gql),
            "with_testrail_case": len(has),
            "missing_testrail_case": len(missing),
            "unique_ops_missing": len(by_op),
        },
        "missing": missing,
        "by_operation": {k: [x["id"] for x in v] for k, v in sorted(by_op.items())},
    }

    out = root / "reports" / "gql-ui-testcase-gaps.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    s = report["summary"]
    print(
        f"GraphQL catalog: {s['graphql_catalog_items']} items | "
        f"mapped: {s['with_testrail_case']} | missing: {s['missing_testrail_case']} "
        f"({s['unique_ops_missing']} unique ops)"
    )
    print(f"Report → {out}")
    if missing:
        print("\nMissing ops:")
        for op in sorted(by_op):
            print(f"  - {op}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
