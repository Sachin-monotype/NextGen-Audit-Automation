#!/usr/bin/env python3
"""Live-test schema-correct TouchPoint payloads against PP GraphQL before shipping the sheet.

Passes when GraphQL accepts the variable shape (no BAD_USER_INPUT / variable errors).
PP Discovery / search-update network timeouts are WARN-only — they are not wrong curls.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:
    pass

import requests

from audit_validator.utility.operation_graphql import get_document_for_operation
from touchpoint_payloads import (
    SeedIds,
    assert_add_font_list_families_shape,
    variables_for,
)


def _bearer() -> str:
    raw = (os.getenv("BEARER_TOKEN") or "").strip()
    return raw[7:].strip() if raw.lower().startswith("bearer ") else raw


def _endpoint() -> str:
    return (
        os.getenv("NEXTGEN_GRAPHQL_ENDPOINT")
        or os.getenv("GRAPHQL_ENDPOINT")
        or "https://nextgen.monotype-pp.com/graph"
    ).rstrip("/")


def _is_shape_reject(body: dict) -> bool:
    for err in body.get("errors") or []:
        msg = str(err.get("message") or "")
        code = str((err.get("extensions") or {}).get("code") or "")
        if code in {"BAD_USER_INPUT", "GRAPHQL_VALIDATION_FAILED"}:
            return True
        if "Variable \"$" in msg or "got invalid value" in msg:
            return True
        if 'Field "' in msg and "is not defined" in msg:
            return True
    return False


def _is_downstream_noise(body: dict) -> bool:
    blob = json.dumps(body).lower()
    needles = (
        "network timeout",
        "etimedout",
        "econnreset",
        "asset_creation_failed",
        "internal_server_error",
    )
    return any(n in blob for n in needles)


def gql_raw(operation: str, variables: dict) -> dict:
    doc = get_document_for_operation(operation)
    if not doc:
        raise RuntimeError(f"No GraphQL document for {operation}")
    m = re.search(r"(?:mutation|query)\s+(\w+)", doc, re.I)
    op_name = m.group(1) if m else operation
    payload = {
        "operationName": op_name,
        "variables": variables,
        "extensions": {"clientLibrary": {"name": "@apollo/client", "version": "4.0.9"}},
        "query": doc,
    }
    resp = requests.post(
        _endpoint(),
        headers={
            "Authorization": f"Bearer {_bearer()}",
            "Content-Type": "application/json",
            "Accept": "application/graphql-response+json,application/json;q=0.9",
            "Origin": "https://nextgen.monotype-pp.com",
            "Referer": "https://nextgen.monotype-pp.com/search",
        },
        json=payload,
        timeout=90,
    )
    try:
        body = resp.json()
    except Exception:
        body = {"errors": [{"message": f"non-JSON HTTP {resp.status_code}: {resp.text[:300]}"}]}
    body["_http"] = resp.status_code
    return body


def assert_shape_ok(operation: str, variables: dict, *, label: str) -> dict:
    """Fail only on schema/variable rejection. Downstream PP flakiness → WARN."""
    body = gql_raw(operation, variables)
    if _is_shape_reject(body):
        raise RuntimeError(f"{label}: SHAPE REJECTED — {json.dumps(body)[:900]}")
    data = body.get("data") or {}
    # Mutation-level field errors that look like missing required fields
    for key, val in data.items():
        if isinstance(val, dict):
            for err in val.get("errors") or []:
                msg = str(err.get("message") or "").lower()
                code = str(err.get("code") or "")
                if code in {"BAD_USER_INPUT", "INVALID_INPUT"} or "required" in msg:
                    raise RuntimeError(f"{label}: input errors — {err}")
    if body.get("errors") and _is_downstream_noise(body):
        print(f"  WARN: {label} — shape accepted, PP downstream failed: {json.dumps(body.get('errors'))[:240]}")
        return {"__downstream__": True}
    if body.get("errors"):
        raise RuntimeError(f"{label}: unexpected errors — {json.dumps(body)[:900]}")
    return data


def _fallback_list_id() -> str:
    env = os.getenv("TOUCHPOINT_LIST_ID", "").strip()
    if env:
        return env
    seed_path = ROOT / "reports" / "touchpoint-live-seed.json"
    if seed_path.exists():
        try:
            return json.loads(seed_path.read_text()).get("list_id") or ""
        except Exception:
            pass
    return "210ad1c8-b742-49e4-9074-4be65d4be610"  # last sheet provision


def main() -> int:
    token = _bearer()
    if not token:
        print("FAIL: BEARER_TOKEN missing")
        return 1

    env_family = os.getenv("SEED_FAMILY_ID", "").strip()
    if os.getenv("TOUCHPOINT_USE_ENV_SEED", "").strip() in {"1", "true", "yes"}:
        family = env_family or "910130168"
    else:
        family = os.getenv("TOUCHPOINT_FAMILY_ID", "").strip() or "910130168"
    style = (
        os.getenv("TOUCHPOINT_STYLE_ID", "").strip()
        or os.getenv("SEED_STYLE_ID", "").strip()
        or "920374778"
    )
    md5 = os.getenv("SEED_VARIATION_MD5", "").strip() or "b783215634650cf0a55e0d723123d5e0"
    ts = int(time.time())
    seed = SeedIds(
        family_id=family,
        style_id=style,
        md5=md5,
        list_name=f"QA_TP_Test_List_{ts}",
        project_name=f"QA_TP_Test_Proj_{ts}",
    )

    print("=== 0. Prove OLD wrong curl is rejected ===")
    bad = {
        "input": {"listId": "00000000-0000-0000-0000-000000000001"},
        "styleFilterInput": {"pagination": {"skip": 0, "limit": 10}},
    }
    bad_body = gql_raw("addFontListFamilies", bad)
    if not _is_shape_reject(bad_body):
        # Some gateways may pass unknown fields silently — still require families when using listId-only
        print("  note: wrong-shape response:", json.dumps(bad_body)[:400])
        if "fontListId" not in json.dumps(bad_body) and not bad_body.get("errors"):
            print("  FAIL: expected GraphQL to reject listId-only input")
            return 1
    else:
        print("  OK: listId-only / missing families rejected")
        print(" ", json.dumps((bad_body.get("errors") or [])[:1])[:300])

    print("=== 1. Create List ===")
    data = assert_shape_ok(
        "createAsset", variables_for("createAsset", seed), label="createAsset"
    )
    if not data.get("__downstream__"):
        seed.list_id = ((data.get("createAsset") or {}).get("asset") or {}).get("id") or ""
    if not seed.list_id:
        seed.list_id = _fallback_list_id()
        print("  using fallback list_id", seed.list_id)
    else:
        print("  list_id", seed.list_id)

    print("=== 2. AddFontListFamilies (correct shape) ===")
    vars_add = variables_for("addFontListFamilies", seed, touch="List (FONTLIST)")
    assert_add_font_list_families_shape(vars_add)
    assert "listId" not in vars_add["input"]
    assert "families" in vars_add["input"]
    print("  variables", json.dumps(vars_add, indent=2))
    assert_shape_ok("addFontListFamilies", vars_add, label="addFontListFamilies")

    print("=== 3. ActivateFamily from List ===")
    vars_act = variables_for("activateFamily", seed, touch="List (FONTLIST)")
    print("  variables", json.dumps(vars_act, indent=2))
    assert_shape_ok("activateFamily", vars_act, label="activateFamily/List")

    print("=== 4. Create Project + addFontProjectFamilies ===")
    data = assert_shape_ok(
        "createProject", variables_for("createProject", seed), label="createProject"
    )
    if not data.get("__downstream__"):
        seed.project_id = ((data.get("createProject") or {}).get("asset") or {}).get("id") or ""
    if not seed.project_id:
        seed.project_id = os.getenv("TOUCHPOINT_PROJECT_ID", "").strip() or "c2a7788f-e731-4f65-8cc6-1db42ff41138"
        print("  using fallback project_id", seed.project_id)
    else:
        print("  project_id", seed.project_id)
    vars_pf = variables_for("addFontProjectFamilies", seed, touch="Project")
    assert "fontProjectId" in vars_pf["input"] and "families" in vars_pf["input"]
    print("  variables", json.dumps(vars_pf, indent=2))
    assert_shape_ok("addFontProjectFamilies", vars_pf, label="addFontProjectFamilies")

    print("=== 5. ActivateFamily from Project ===")
    vars_proj = variables_for("activateFamily", seed, touch="Project")
    print("  variables", json.dumps(vars_proj, indent=2))
    assert_shape_ok("activateFamily", vars_proj, label="activateFamily/Project")

    print("=== ALL SHAPE CHECKS PASSED ===")
    summary = {
        "family_id": seed.family_id,
        "style_id": seed.style_id,
        "list_id": seed.list_id,
        "project_id": seed.project_id,
    }
    print(json.dumps(summary, indent=2))
    out = ROOT / "reports" / "touchpoint-live-seed.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                **summary,
                "md5": seed.md5,
                "list_name": seed.list_name,
                "project_name": seed.project_name,
            },
            indent=2,
        )
        + "\n"
    )
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
