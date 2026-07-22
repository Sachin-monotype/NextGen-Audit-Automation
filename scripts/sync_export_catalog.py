#!/usr/bin/env python3
"""Add batch export mutations to audit catalog (GQL docs, routing, FLOW_DEFS, registry)."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "python" / "audit_validator" / "data"
PAYLOADS = ROOT / "python" / "audit_validator" / "touchpoint" / "payloads.py"
RESOLVER_ROUTING = (
    ROOT.parent.parent
    / "MT Connect NextGen"
    / "mt-audit-log-resolver-service"
    / "config"
    / "outbound-routing-map.json"
)

# op -> (InputType, response selection)
EXPORT_MUTATIONS: dict[str, tuple[str, str]] = {
    "exportFontAssets": ("ExportFontAssetsInput", "success batchId status errors { code message }"),
    "exportFontProjects": ("ExportFontProjectsInput", "success batchId status errors { code message }"),
    "exportFontUsers": ("ExportFontUsersInput", "success batchId status errors { code message }"),
    "exportFontWebkits": ("ExportFontWebkitsInput", "success batchId status errors { code message }"),
    "exportReportingFonts": ("ExportReportingFontsInput", "success batchId status errors { code message }"),
    "exportReportingUsers": ("ExportReportingUsersInput", "success batchId status errors { code message }"),
    "exportReportingWebkits": ("ExportReportingWebkitsInput", "success batchId status errors { code message }"),
    "exportUserAssets": ("ExportUserAssetsInput", "success batchId status errors { code message }"),
    "exportUserFonts": ("ExportUserFontsInput", "success batchId status errors { code message }"),
    "exportUserProjects": ("ExportUserProjectsInput", "success batchId status errors { code message }"),
    "exportWebkitDomains": ("ExportWebkitDomainsInput", "success batchId status errors { code message }"),
    "exportWebkitFonts": ("ExportWebkitFontsInput", "success batchId status errors { code message }"),
    "exportCompanyLibrary": ("ExportCompanyLibraryInput", "batchId status message"),
    "exportMyLibrary": ("ExportMyLibraryInput", "batchId status message"),
    "exportImportedFonts": ("ExportImportedFontsInput", "batchId status message"),
    "exportLeavingSoonFonts": ("ExportFontsLeavingSoonInput", "success batchId status errors { code message }"),
    "exportNotifications": ("ExportNotificationsInput", "batchId status message"),
    "exportTags": ("ExportTagsInput", "batchId status message"),
    "exportServiceAccount": ("ExportServiceAccountInput", "batchId status message"),
    "exportSsoMappings": ("ExportSsoMappingsInput", "success batchId status errors { code message }"),
    "exportTeams": ("ExportTeamsInput", "batchId status message"),
    "exportRoles": ("ExportRolesInput", "batchId status message"),
    "exportUsers": ("ExportUsersInput", "batchId status message"),
}


def _export_name(op: str) -> str:
    parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)", op)
    return "_".join(p.upper() for p in parts)


def _gql_doc(op: str, input_type: str, fields: str) -> str:
    export = _export_name(op)
    title = export.replace("_", " ").title().replace(" ", "")
    return (
        f"mutation {title}($input: {input_type}!) {{ "
        f"{op}(input: $input) {{ {fields} }} }}"
    )


def _payload_builder_snippet() -> str:
    lines = [
        '        # ── Batch exports (async Conductor) ──',
    ]
    for op in EXPORT_MUTATIONS:
        if op in {
            "exportFontAssets",
            "exportFontProjects",
            "exportFontUsers",
            "exportFontWebkits",
        }:
            lines.append(
                f'        "{op}": lambda: export_font_scoped(seed),'
            )
        elif op in {"exportUserAssets", "exportUserFonts", "exportUserProjects"}:
            lines.append(f'        "{op}": lambda: export_user_scoped(seed),')
        elif op in {"exportWebkitDomains", "exportWebkitFonts"}:
            lines.append(f'        "{op}": lambda: export_webkit_scoped(seed),')
        elif op == "exportRoles":
            lines.append(f'        "{op}": lambda: export_roles(seed),')
        elif op == "exportUsers":
            lines.append(f'        "{op}": lambda: export_users(seed),')
        elif op == "exportNotifications":
            lines.append(f'        "{op}": lambda: export_notifications(seed),')
        else:
            lines.append(f'        "{op}": lambda: export_csv_only(),')
    return "\n".join(lines)


def _helper_functions() -> str:
    return '''

def export_csv_only() -> dict[str, Any]:
    return {"input": {"format": "CSV"}}


def export_font_scoped(seed: SeedIds) -> dict[str, Any]:
    return {
        "input": {
            "format": "CSV",
            "fontId": seed.family_id or seed.style_id or "",
            "fontName": seed.family_name or "QA Export Font",
        }
    }


def export_user_scoped(seed: SeedIds) -> dict[str, Any]:
    return {
        "input": {
            "format": "CSV",
            "subjectUserId": seed.profile_id or "",
            "subjectUserName": "QA Export User",
        }
    }


def export_webkit_scoped(seed: SeedIds) -> dict[str, Any]:
    return {
        "input": {
            "format": "CSV",
            "webkitId": seed.project_id or seed.list_id or "",
        }
    }


def export_roles(seed: SeedIds) -> dict[str, Any]:
    return {"input": {"format": "CSV"}}


def export_users(seed: SeedIds) -> dict[str, Any]:
    return {"input": {"format": "CSV"}}


def export_notifications(seed: SeedIds) -> dict[str, Any]:
    return {"input": {"format": "CSV"}}
'''


def _flow_defs_snippet() -> str:
    lines = ["    # Batch export mutations — GQL-only single-step flows", "    {"]
    for op in EXPORT_MUTATIONS:
        lines.append(f'        "{op}": {{')
        lines.append('            "Discovery/Browse (global)": ["' + op + '"],')
        lines.append("        },")
    lines.append("    }")
    return "\n".join(lines)


def main() -> None:
    gql_path = DATA / "graphql_documents.json"
    routing_path = DATA / "outbound-routing-map.json"
    registry_path = DATA / "enricher_registry.json"

    docs = json.loads(gql_path.read_text(encoding="utf-8"))
    added_gql: list[str] = []
    for op, (input_type, fields) in EXPORT_MUTATIONS.items():
        key = _export_name(op)
        if key not in docs:
            docs[key] = _gql_doc(op, input_type, fields)
            added_gql.append(key)
    gql_path.write_text(json.dumps(docs, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    routing = json.loads(routing_path.read_text(encoding="utf-8"))
    if RESOLVER_ROUTING.is_file():
        resolver_map = json.loads(RESOLVER_ROUTING.read_text(encoding="utf-8"))
        for op in EXPORT_MUTATIONS:
            if op in resolver_map:
                routing[op] = resolver_map[op]
    added_routing = [op for op in EXPORT_MUTATIONS if op not in routing]
    routing_path.write_text(json.dumps(routing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registered: list[str] = registry.get("registered") or []
    reg_set = set(registered)
    for op in EXPORT_MUTATIONS:
        reg_set.add(op)
    registry["registered"] = sorted(reg_set)
    registry_path.write_text(json.dumps(registry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    text = PAYLOADS.read_text(encoding="utf-8")
    if "def export_csv_only()" not in text:
        anchor = "def variables_for(operation: str, seed: SeedIds, *, touch: str = \"\") -> dict[str, Any]:"
        text = text.replace(anchor, _helper_functions().strip() + "\n\n\n" + anchor)

    builder_snip = _payload_builder_snippet()
    if '"exportFontAssets"' not in text:
        text = text.replace(
            '        "exportFontTemplate": lambda: {',
            builder_snip + '\n        "exportFontTemplate": lambda: {',
        )

    flow_snip = _flow_defs_snippet()
    if '"exportFontAssets"' not in text.split("FLOW_DEFS")[-1]:
        text = text.rstrip() + "\n\nFLOW_DEFS.update(\n" + flow_snip + "\n)\n"

    PAYLOADS.write_text(text, encoding="utf-8")

    import sys

    sys.path.insert(0, str(ROOT / "python"))
    from audit_validator.utility.operation_graphql import write_operation_manifest

    write_operation_manifest()
    from audit_validator.utility import operation_graphql as og

    og.load_operation_index.cache_clear()

    print(f"Added {len(added_gql)} GraphQL docs: {added_gql[:5]}{'...' if len(added_gql) > 5 else ''}")
    print(f"Routing keys for exports: {len(EXPORT_MUTATIONS)}")
    print(f"Registry now has {len(registry['registered'])} operations")


if __name__ == "__main__":
    main()
