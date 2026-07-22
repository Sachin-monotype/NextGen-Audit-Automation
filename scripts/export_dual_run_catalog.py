#!/usr/bin/env python3
"""Export CSV of scenarios with TestRail mapping, data mapping, and UI+GQL runnable."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "python"))

from audit_validator.source_validation.mapping_registry import get_operation_mapping
from audit_validator.touchpoint.scenarios import list_scenarios
from audit_validator.ui_case_recipes import recipe_for
from audit_validator.ui_testrail_map import case_id_for_selection_item, reload_map
from audit_validator.utility.operation_graphql import get_operation_entry


def main() -> int:
    out = REPO / "reports" / "dual_run_catalog.csv"
    out.parent.mkdir(parents=True, exist_ok=True)

    reload_map()
    scenarios = [s for s in list_scenarios() if s.get("kind") == "graphql"]
    rows: list[dict[str, str]] = []

    for s in scenarios:
        op = str(s.get("operation") or "").strip()
        touch = str(s.get("touchpoint") or "").strip()
        key = str(s.get("id") or f"{op}::{touch}")
        if not op:
            continue

        case_id = case_id_for_selection_item(
            {"operation": op, "touchpoint": touch, "id": key, "label": s.get("label")}
        ) or ""
        gql_entry = get_operation_entry(op)
        mapping = get_operation_mapping(op)
        mapping_count = len(mapping or [])

        try:
            ui_steps = recipe_for(op, touch, label=str(s.get("label") or key))
            ui_ok = bool(ui_steps)
        except Exception:
            ui_ok = False

        gql_ok = gql_entry is not None and gql_entry.kind == "mutation"
        testrail_ok = bool(case_id)
        mapping_ok = mapping_count > 0
        dual_ok = testrail_ok and mapping_ok and gql_ok and ui_ok

        rows.append(
            {
                "scenario_key": key,
                "operation": op,
                "touchpoint": touch,
                "label": str(s.get("label") or ""),
                "testrail_case_id": str(case_id) if case_id else "",
                "testrail_mapped": "yes" if testrail_ok else "no",
                "data_mapping_fields": str(mapping_count),
                "data_mapping": "yes" if mapping_ok else "no",
                "gql_runnable": "yes" if gql_ok else "no",
                "gql_export": gql_entry.export_name if gql_entry else "",
                "ui_runnable": "yes" if ui_ok else "no",
                "ui_and_gql": "yes" if dual_ok else "no",
                "flow_steps": " → ".join(s.get("steps") or []),
            }
        )

    rows.sort(key=lambda r: (r["ui_and_gql"], r["operation"], r["touchpoint"]), reverse=True)

    fields = [
        "scenario_key",
        "operation",
        "touchpoint",
        "label",
        "testrail_case_id",
        "testrail_mapped",
        "data_mapping_fields",
        "data_mapping",
        "gql_runnable",
        "gql_export",
        "ui_runnable",
        "ui_and_gql",
        "flow_steps",
    ]
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    dual = sum(1 for r in rows if r["ui_and_gql"] == "yes")
    print(f"Wrote {len(rows)} scenarios → {out}")
    print(f"UI + GQL + TestRail + mapping: {dual}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
