#!/usr/bin/env python3
"""Build NEW export TestRail pack + GQL curl samples + comparison mappings.

Does NOT modify fdc14091_testrail_map.json — writes separate push artifacts only.

Usage:
  PYTHONPATH=python python3 scripts/build_export_testrail_pack.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "python"))

from audit_validator.export_ui_catalog import export_spec, export_touchpoint
from audit_validator.ui_case_recipes import testrail_steps_separated
from audit_validator.utility.operation_graphql import get_document_for_operation, get_operation_entry
from audit_validator.source_validation.mapping_registry import get_operation_mapping

DATA = REPO / "python" / "audit_validator" / "data"
REPORTS = REPO / "reports"

# User-requested export ops (all use exportOp(global) touchpoint)
TARGET_OPS = [
    "exportFontAssets",
    "exportFontProjects",
    "exportFontUsers",
    "exportFontWebkits",
    "exportImportedFonts",
    "exportMyLibrary",
    "exportNotifications",
    "exportRoles",
    "exportTags",
]

PRECONDITIONS = (
    "1. NextGen PP/QA is reachable; log in if not already signed in.\n"
    "2. Prefer reuse of existing data on the page (no rebuild unless empty).\n"
    "3. Capture response header correlation-id (not x-correlation-id) from GraphQL.\n"
    "4. Follow plain-English steps; emit AUDIT_RESULT with real UUID after the export mutation.\n"
    "5. Jira: FDC-14091."
)

GQL_INPUTS: dict[str, dict] = {
    "exportFontAssets": {"format": "CSV", "fontId": "<familyOrStyleId>", "fontName": "QA Export Font"},
    "exportFontProjects": {"format": "CSV", "fontId": "<familyOrStyleId>", "fontName": "QA Export Font"},
    "exportFontUsers": {"format": "CSV", "fontId": "<familyOrStyleId>", "fontName": "QA Export Font"},
    "exportFontWebkits": {"format": "CSV", "fontId": "<familyOrStyleId>", "fontName": "QA Export Font"},
    "exportImportedFonts": {"format": "CSV"},
    "exportMyLibrary": {"format": "CSV"},
    "exportNotifications": {"format": "CSV"},
    "exportRoles": {"format": "CSV"},
    "exportTags": {"format": "CSV"},
    "exportTeams": {"format": "CSV", "filters": {"profileCount": 0}},
    "exportUsers": {"format": "CSV"},
    "exportCompanyLibrary": {"format": "CSV"},
}


def _operation_name(op: str) -> str:
    entry = get_operation_entry(op)
    if entry:
        doc = get_document_for_operation(op) or ""
        m = re.search(r"mutation\s+(\w+)", doc)
        if m:
            return m.group(1)
    parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)", op)
    return "".join(p.capitalize() for p in parts)


def _gql_body(op: str) -> dict:
    op_name = _operation_name(op)
    doc = get_document_for_operation(op) or ""
    inp = GQL_INPUTS.get(op, {"format": "CSV"})
    return {
        "operationName": op_name,
        "variables": {"input": inp},
        "extensions": {"clientLibrary": {"name": "@apollo/client", "version": "4.0.9"}},
        "query": doc.replace("\n", "\\n") if doc else "",
    }


def _curl_sample(op: str, *, base_url: str = "https://nextgen.monotype-pp.com") -> str:
    op_name = _operation_name(op)
    body = _gql_body(op)
    payload = json.dumps(
        {
            "operationName": body["operationName"],
            "variables": body["variables"],
            "extensions": body["extensions"],
            "query": get_document_for_operation(op),
        },
        ensure_ascii=False,
    )
    spec = export_spec(op) or {}
    referer = base_url + str(spec.get("url_hint") or "/")
    return (
        f"curl '{base_url}/graph' \\\n"
        f"  -H 'accept: application/graphql-response+json,application/json;q=0.9' \\\n"
        f"  -H 'authorization: Bearer <BEARER_TOKEN>' \\\n"
        f"  -H 'content-type: application/json' \\\n"
        f"  -H 'origin: {base_url}' \\\n"
        f"  -H 'referer: {referer}' \\\n"
        f"  -H 'x-correlation-id: <CORRELATION_UUID>' \\\n"
        f"  -X POST \\\n"
        f"  --data-raw '{payload}'"
    )


def _comparison_rows(op: str) -> list[dict]:
    rows = []
    for f in get_operation_mapping(op):
        rows.append(
            {
                "field_path": f.enriched_path,
                "field": f.field,
                "source_system": f.source_system,
                "source_api": f.source_api,
                "validate": f.validate,
                "layer": f.layer,
                "notes": f.data_mapping,
            }
        )
    return rows


def main() -> int:
    cases = []
    gql_samples: dict[str, dict] = {}
    comparison: dict[str, list] = {}

    for op in TARGET_OPS:
        touch = export_touchpoint(op)
        label = f"{op}(global)"
        spec = export_spec(op) or {}
        entry = get_operation_entry(op)
        steps = testrail_steps_separated(op, touch, label=label)
        cases.append(
            {
                "title": f"Verify {op} — Export (global)",
                "operation": op,
                "touchpoint": touch,
                "label": label,
                "key": f"{op}::{touch}",
                "url_hint": spec.get("url_hint"),
                "export_button": spec.get("button"),
                "gql_export": entry.export_name if entry else spec.get("gql_export"),
                "gql_mutation": entry.root_field if entry else op,
                "custom_preconds": PRECONDITIONS,
                "custom_steps_separated": steps,
                "estimate": "15m",
                "refs": "FDC-14091",
            }
        )
        gql_samples[op] = {
            "operation": op,
            "label": label,
            "gql_export": entry.export_name if entry else None,
            "operation_name": _operation_name(op),
            "variables": {"input": GQL_INPUTS.get(op, {"format": "CSV"})},
            "document": get_document_for_operation(op),
            "curl_pp": _curl_sample(op),
        }
        comparison[op] = _comparison_rows(op)

    testrail_pack = {
        "version": 1,
        "jira": "FDC-14091",
        "suite": 22395,
        "suite_section": 4066542,
        "notes": (
            "NEW export batch cases only. Push with scripts/push_export_testrail_cases.py — "
            "does not update fdc14091_testrail_map.json or existing TestRail case ids."
        ),
        "preconditions_template": PRECONDITIONS,
        "operations": TARGET_OPS,
        "cases": cases,
    }

    out_testrail = DATA / "fdc14091_export_testrail_cases.json"
    out_gql = DATA / "export_gql_curl_samples.json"
    out_map = DATA / "export_comparison_mappings.json"
    out_csv = REPORTS / "export_ui_test_cases.csv"

    out_testrail.write_text(json.dumps(testrail_pack, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    out_gql.write_text(
        json.dumps({"version": 1, "base_url": "https://nextgen.monotype-pp.com", "samples": gql_samples}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    out_map.write_text(
        json.dumps({"version": 1, "operations": comparison}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # refresh CSV for reports
    import csv

    REPORTS.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "operation",
                "label",
                "touchpoint",
                "url_hint",
                "export_button",
                "gql_export",
                "gql_mutation",
                "step_count",
            ],
        )
        w.writeheader()
        for c in cases:
            w.writerow(
                {
                    "operation": c["operation"],
                    "label": c["label"],
                    "touchpoint": c["touchpoint"],
                    "url_hint": c.get("url_hint") or "",
                    "export_button": c.get("export_button") or "",
                    "gql_export": c.get("gql_export") or "",
                    "gql_mutation": c.get("gql_mutation") or "",
                    "step_count": len(c.get("custom_steps_separated") or []),
                }
            )

    print(f"Wrote {len(cases)} TestRail cases → {out_testrail}")
    print(f"Wrote GQL curl samples → {out_gql}")
    print(f"Wrote comparison mappings → {out_map}")
    print(f"Wrote CSV → {out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
