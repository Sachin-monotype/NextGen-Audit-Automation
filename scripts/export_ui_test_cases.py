#!/usr/bin/env python3
"""Write human-readable export UI test case CSV from export_ui_catalog.json."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "python"))

from audit_validator.export_ui_catalog import export_ops, export_spec, export_touchpoint
from audit_validator.ui_case_recipes import recipe_for, testrail_steps_text
from audit_validator.utility.operation_graphql import get_operation_entry


def main() -> int:
    out = REPO / "reports" / "export_ui_test_cases.csv"
    out.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, str]] = []
    for op in export_ops():
        spec = export_spec(op) or {}
        touch = export_touchpoint(op)
        gql = get_operation_entry(op)
        recipe = testrail_steps_text(op, touch, label=f"{op}({touch})")

        rows.append(
            {
                "operation": op,
                "touchpoint": touch,
                "url_hint": str(spec.get("url_hint") or ""),
                "export_button": str(spec.get("button") or ""),
                "gql_export": gql.export_name if gql else str(spec.get("gql_export") or ""),
                "gql_mutation": gql.root_field if gql else op,
                "ui_steps": " | ".join(spec.get("steps") or []),
                "testrail_recipe": recipe.replace("\n", " / "),
                "web_gap": str(spec.get("web_gap") or ""),
            }
        )

    fields = [
        "operation",
        "touchpoint",
        "url_hint",
        "export_button",
        "gql_export",
        "gql_mutation",
        "ui_steps",
        "testrail_recipe",
        "web_gap",
    ]
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    print(f"Wrote {len(rows)} export test cases → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
