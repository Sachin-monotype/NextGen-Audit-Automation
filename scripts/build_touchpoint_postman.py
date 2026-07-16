#!/usr/bin/env python3
"""Build a Postman collection for TouchPoint multi-step GQL flows.

Folders chain create → seed → trigger. Test scripts persist listId / projectId
into collection variables so step 2 uses the id from step 1.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:
    pass

from audit_validator.utility.operation_graphql import get_document_for_operation
from touchpoint_payloads import FLOW_DEFS, SeedIds, variables_for

OUT = ROOT / "docs" / "mappings" / "TouchPoint.postman_collection.json"

# Every multi-step flow from FLOW_DEFS (same as Excel AutomationFlows)
FLOWS: list[tuple[str, str, list[str]]] = [
    (f"{op} / {touch}", touch, ops)
    for op, touches in FLOW_DEFS.items()
    for touch, ops in touches.items()
]

def _bearer() -> str:
    raw = (os.getenv("BEARER_TOKEN") or "").strip()
    return raw[7:].strip() if raw.lower().startswith("bearer ") else raw


def _op_name(doc: str, operation: str) -> str:
    m = re.search(r"(?:mutation|query)\s+(\w+)", doc or "", re.I)
    return m.group(1) if m else operation[:1].upper() + operation[1:]


def _item(operation: str, variables: dict, *, touch: str, idx: int) -> dict:
    doc = get_document_for_operation(operation) or f"mutation {operation} {{ {operation} }}"
    # Substitute seed placeholders with Postman {{var}} for chaining
    body_vars = json.loads(json.dumps(variables))

    def walk(obj):
        if isinstance(obj, dict):
            return {k: walk(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [walk(x) for x in obj]
        if isinstance(obj, str):
            return (
                obj.replace("PENDING_LIST_ID", "{{listId}}")
                .replace("PENDING_PROJECT_ID", "{{projectId}}")
                .replace("project_PENDING_PROJECT_ID", "project_{{projectId}}")
            )
        return obj

    body_vars = walk(body_vars)
    # For create/add chain: replace known seed list/project with Postman vars when present
    inp = body_vars.get("input")
    if isinstance(inp, dict):
        if operation in {"addFontListFamilies", "addFontListStyles", "activateList", "deActivateList", "bulkActivateLists", "bulkDeactivateLists"}:
            if "fontListId" in inp:
                inp["fontListId"] = "{{listId}}"
            if "listId" in inp:
                inp["listId"] = "{{listId}}"
            if "listIds" in inp and isinstance(inp["listIds"], list):
                inp["listIds"] = ["{{listId}}"]
            if "lists" in inp:
                inp["lists"] = [{"id": "{{listId}}"}]
        if operation in {"addFontProjectFamilies", "addFontProjectStyles", "activateFontProject", "deActivateFontProject"}:
            if "fontProjectId" in inp:
                inp["fontProjectId"] = "{{projectId}}"
            if "projectId" in inp:
                inp["projectId"] = "{{projectId}}"
        if operation == "activateFamily":
            if inp.get("listType") == "FONTLIST" and "listIds" in inp:
                inp["listIds"] = ["{{listId}}"]
            if inp.get("listType") == "FONTPROJECT":
                inp["listIds"] = ["project_{{projectId}}"]
                inp["projectId"] = "{{projectId}}"
            if "projectId" in inp and inp.get("listType") == "FONTLIST":
                inp["projectId"] = "{{projectId}}"
        if "families" in inp and isinstance(inp["families"], dict):
            inp["families"]["familyIds"] = ["{{familyId}}"]
        if "familyIds" in inp:
            inp["familyIds"] = ["{{familyId}}"]
        if "styleIds" in inp:
            inp["styleIds"] = ["{{styleId}}"]
        if "styles" in inp and isinstance(inp["styles"], list) and inp["styles"]:
            if isinstance(inp["styles"][0], dict) and "styleId" in inp["styles"][0]:
                inp["styles"] = [{"styleId": "{{styleId}}"}]
            elif isinstance(inp["styles"][0], dict) and "id" in inp["styles"][0]:
                inp["styles"] = [{"id": "{{styleId}}"}]

    payload = {
        "operationName": _op_name(doc, operation),
        "variables": body_vars,
        "extensions": {"clientLibrary": {"name": "@apollo/client", "version": "4.0.9"}},
        "query": doc,
    }

    events = []
    if operation == "createAsset":
        events.append(
            {
                "listen": "test",
                "script": {
                    "type": "text/javascript",
                    "exec": [
                        "const j = pm.response.json();",
                        "const id = j?.data?.createAsset?.asset?.id;",
                        "if (id) {",
                        "  pm.collectionVariables.set('listId', id);",
                        "  console.log('listId=', id);",
                        "} else {",
                        "  console.warn('createAsset did not return asset.id', j);",
                        "}",
                        "pm.test('createAsset HTTP ok', () => pm.response.to.have.status(200));",
                    ],
                },
            }
        )
    if operation == "createProject":
        events.append(
            {
                "listen": "test",
                "script": {
                    "type": "text/javascript",
                    "exec": [
                        "const j = pm.response.json();",
                        "const id = j?.data?.createProject?.asset?.id;",
                        "if (id) {",
                        "  pm.collectionVariables.set('projectId', id);",
                        "  console.log('projectId=', id);",
                        "}",
                        "pm.test('createProject HTTP ok', () => pm.response.to.have.status(200));",
                    ],
                },
            }
        )
    if operation == "addFontListFamilies":
        events.append(
            {
                "listen": "prerequest",
                "script": {
                    "type": "text/javascript",
                    "exec": [
                        "if (!pm.collectionVariables.get('listId')) {",
                        "  throw new Error('listId missing — run Create List first in this folder');",
                        "}",
                    ],
                },
            }
        )
        events.append(
            {
                "listen": "test",
                "script": {
                    "type": "text/javascript",
                    "exec": [
                        "const j = pm.response.json();",
                        "pm.test('no GraphQL variable errors', () => {",
                        "  const errs = j.errors || [];",
                        "  const bad = errs.some(e => String(e.message||'').includes('Variable'));",
                        "  pm.expect(bad).to.eql(false);",
                        "});",
                        "const ok = j?.data?.addFontListFamilies?.success;",
                        "console.log('addFontListFamilies success=', ok, 'errors=', j?.data?.addFontListFamilies?.errors || j.errors);",
                    ],
                },
            }
        )

    titles = {
        "createAsset": f"{idx}. Create List",
        "createProject": f"{idx}. Create Project",
        "addFontListFamilies": f"{idx}. Add family to list",
        "addFontListStyles": f"{idx}. Add style to list",
        "addFontProjectFamilies": f"{idx}. Add family to project",
        "addFontProjectStyles": f"{idx}. Add style to project",
        "addFavoriteFamilies": f"{idx}. Add family to favourites",
        "activateFamily": f"{idx}. Activate family",
        "activateList": f"{idx}. Activate list",
        "activateFontProject": f"{idx}. Activate project fonts",
    }

    return {
        "name": titles.get(operation, f"{idx}. {operation}"),
        "event": events,
        "request": {
            "method": "POST",
            "header": [
                {
                    "key": "Authorization",
                    "value": "Bearer {{bearerToken}}",
                },
                {
                    "key": "Content-Type",
                    "value": "application/json",
                },
                {
                    "key": "Accept",
                    "value": "application/graphql-response+json,application/json;q=0.9",
                },
                {"key": "Origin", "value": "https://nextgen.monotype-pp.com"},
                {"key": "Referer", "value": "https://nextgen.monotype-pp.com/search"},
            ],
            "body": {
                "mode": "raw",
                "raw": json.dumps(payload, indent=2),
                "options": {"raw": {"language": "json"}},
            },
            "url": "{{graphqlUrl}}",
            "description": f"TouchPoint `{touch}` — operation `{operation}`",
        },
    }


def build() -> Path:
    family = os.getenv("TOUCHPOINT_FAMILY_ID", "").strip() or "910130168"
    style = os.getenv("TOUCHPOINT_STYLE_ID", "").strip() or os.getenv("SEED_STYLE_ID", "").strip() or "920374778"
    md5 = os.getenv("SEED_VARIATION_MD5", "").strip() or "b783215634650cf0a55e0d723123d5e0"
    seed = SeedIds(
        family_id=family,
        style_id=style,
        md5=md5,
        list_id="{{listId}}",
        project_id="{{projectId}}",
        list_name="QA_Postman_List",
        project_name="QA_Postman_Project",
    )

    folders = []
    for folder_name, touch, ops in FLOWS:
        items = []
        for i, op in enumerate(ops, 1):
            # Use empty ids so variables_for doesn't bake stale UUIDs; Postman {{listId}} applied after
            local = SeedIds(
                family_id=seed.family_id,
                style_id=seed.style_id,
                md5=seed.md5,
                list_id="PENDING_LIST_ID",
                project_id="PENDING_PROJECT_ID",
                list_name=f"QA_PM_{op}_{i}",
                project_name=f"QA_PM_Project_{i}",
            )
            vars_ = variables_for(op, local, touch=touch)
            items.append(_item(op, vars_, touch=touch, idx=i))
        folders.append({"name": folder_name, "item": items})

    collection = {
        "info": {
            "name": "NextGen Audit — TouchPoint GQL",
            "description": (
                "Multi-step GraphQL flows for audit event generation.\n\n"
                "Run requests **in order** inside a folder. Create List / Create Project "
                "test scripts save `listId` / `projectId` for later steps.\n\n"
                "Set collection vars: bearerToken, familyId, styleId."
            ),
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "variable": [
            {
                "key": "graphqlUrl",
                "value": os.getenv("NEXTGEN_GRAPHQL_ENDPOINT", "https://nextgen.monotype-pp.com/graph"),
            },
            {"key": "bearerToken", "value": _bearer()},
            {"key": "familyId", "value": family},
            {"key": "styleId", "value": style},
            {"key": "listId", "value": ""},
            {"key": "projectId", "value": ""},
        ],
        "item": folders,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(collection, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}")
    return OUT


if __name__ == "__main__":
    build()
