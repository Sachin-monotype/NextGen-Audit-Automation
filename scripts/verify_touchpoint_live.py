#!/usr/bin/env python3
"""Live-verify TouchPoint payloads: schema OK, success=true where possible, RabbitMQ/Mongo raw.

Focus modules first: activation + library create/add (highest value).
Full 189-op sweep is rate-limited — use --module and --limit.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:
    pass

import requests

from audit_validator.touchpoint.modules import module_for
from audit_validator.touchpoint.payloads import SeedIds, variables_for
from audit_validator.utility.operation_graphql import get_document_for_operation

# Safe smoke set — create + add + activate paths (non-destructive admin)
SMOKE_OPS = [
    ("createAsset", "List (FONTLIST)"),
    ("addFontListFamilies", "List (FONTLIST)"),
    ("activateFamily", "Discovery/Browse (global)"),
    ("activateFamily", "List (FONTLIST)"),
    ("activateFamily", "Favourite"),
    ("createProject", "Project"),
    ("addFontProjectFamilies", "Project"),
    ("activateFamily", "Project"),
    ("addFavoriteFamilies", "Favourite"),
    ("createTeam", "Discovery/Browse (global)"),
    ("createPrivateTags", "Discovery/Browse (global)"),
]


def _bearer() -> str:
    raw = (os.getenv("BEARER_TOKEN") or "").strip()
    return raw[7:].strip() if raw.lower().startswith("bearer ") else raw


def _endpoint() -> str:
    return (
        os.getenv("NEXTGEN_GRAPHQL_ENDPOINT")
        or os.getenv("GRAPHQL_ENDPOINT")
        or "https://nextgen.monotype-pp.com/graph"
    ).rstrip("/")


def _seed() -> SeedIds:
    live = ROOT / "reports" / "touchpoint-live-seed.json"
    d = json.loads(live.read_text()) if live.exists() else {}
    token = _bearer()
    gcid = ""
    try:
        import base64

        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        info = claims.get("https://secure.monotype.com/info") or {}
        gcid = str(info.get("parentCustomerId") or claims.get("https://api.monotype.com/gcid") or "")
    except Exception:
        pass
    ts = int(time.time())
    return SeedIds(
        family_id=d.get("family_id") or os.getenv("TOUCHPOINT_FAMILY_ID") or "910130168",
        style_id=d.get("style_id") or os.getenv("TOUCHPOINT_STYLE_ID") or "920374778",
        md5=d.get("md5") or "b783215634650cf0a55e0d723123d5e0",
        list_id=d.get("list_id") or "",
        project_id=d.get("project_id") or "",
        list_name=f"QA_Verify_List_{ts}",
        project_name=f"QA_Verify_Project_{ts}",
        customer_id=gcid or os.getenv("OAUTH_GCID") or "",
        profile_id=os.getenv("TOUCHPOINT_PROFILE_ID") or "",
        role_id=os.getenv("TOUCHPOINT_ROLE_ID") or "",
        tag_id=os.getenv("TOUCHPOINT_TAG_ID") or "",
        team_id=os.getenv("TOUCHPOINT_TEAM_ID") or "",
    )


def _shape_reject(body: dict) -> bool:
    for err in body.get("errors") or []:
        code = str((err.get("extensions") or {}).get("code") or "")
        msg = str(err.get("message") or "")
        if code in {"BAD_USER_INPUT", "GRAPHQL_VALIDATION_FAILED"}:
            return True
        if "Variable \"$" in msg or "got invalid value" in msg:
            return True
    return False


def gql(operation: str, variables: dict, *, correlation_id: str | None = None) -> dict:
    doc = get_document_for_operation(operation)
    if not doc:
        return {"_error": f"no document for {operation}"}
    m = re.search(r"(?:mutation|query)\s+(\w+)", doc, re.I)
    op_name = m.group(1) if m else operation
    cid = correlation_id or str(uuid.uuid4())
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
            "x-correlation-id": cid,
        },
        json=payload,
        timeout=90,
    )
    try:
        body = resp.json()
    except Exception:
        body = {"errors": [{"message": resp.text[:300]}]}
    body["_http"] = resp.status_code
    body["_correlation_id"] = cid
    return body


def _mutation_success(body: dict, operation: str) -> bool | None:
    data = body.get("data") or {}
    node = data.get(operation)
    if not isinstance(node, dict):
        # try first key
        if len(data) == 1:
            node = next(iter(data.values()))
    if isinstance(node, dict) and "success" in node:
        return bool(node.get("success"))
    return None


def verify_mongo_raw(cid: str, operation: str, timeout: float = 45.0) -> dict:
    """Poll Mongo for owned raw event if ingestion DB is configured."""
    try:
        from audit_validator.mongo_store import MongoStore  # type: ignore
    except Exception:
        try:
            from audit_validator.db import get_mongo  # type: ignore
        except Exception:
            return {"ok": False, "reason": "mongo client not available"}
    # Prefer generate_run_report lookup if MongoEventStore exists
    try:
        from audit_validator.ingestion.store import EventStore  # type: ignore

        store = EventStore()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            raw = store.find_raw_by_correlation(cid) if hasattr(store, "find_raw_by_correlation") else None
            if raw:
                return {"ok": True, "raw": True, "operation": (raw.get("source") or {}).get("operation")}
            time.sleep(3)
        return {"ok": False, "reason": "timeout waiting for raw", "cid": cid}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)[:200], "cid": cid}


def run_smoke(seed: SeedIds) -> dict:
    results = []
    for op, touch in SMOKE_OPS:
        vars_ = variables_for(op, seed, touch=touch)
        # skip if required ids missing for scoped ops
        if op in {"addFontListFamilies", "activateFamily"} and touch.startswith("List") and not seed.list_id:
            # create list first handled by createAsset in SMOKE_OPS order
            pass
        body = gql(op, vars_)
        cid = body.get("_correlation_id")
        shape_bad = _shape_reject(body)
        success = _mutation_success(body, op)
        # Capture ids from creates
        data = body.get("data") or {}
        if op == "createAsset" and (data.get("createAsset") or {}).get("asset"):
            seed.list_id = data["createAsset"]["asset"]["id"]
        if op == "createProject" and (data.get("createProject") or {}).get("asset"):
            seed.project_id = data["createProject"]["asset"]["id"]
        if op == "createPrivateTags":
            # try extract tag id
            tags = ((data.get("createPrivateTags") or {}).get("data") or [])
            if tags and isinstance(tags[0], dict):
                tag = (tags[0].get("tag") or {})
                if tag.get("id"):
                    seed.tag_id = tag["id"]
        if op == "createTeam" and (data.get("createTeam") or {}).get("id"):
            seed.team_id = data["createTeam"]["id"]

        row = {
            "operation": op,
            "touch": touch,
            "module": module_for(op),
            "shape_ok": not shape_bad,
            "success": success,
            "cid": cid,
            "http": body.get("_http"),
            "errors": body.get("errors"),
            "mutation_errors": (data.get(op) or {}).get("errors") if isinstance(data.get(op), dict) else None,
        }
        # Rabbit/Mongo verify only when success true or no success field but no errors
        if row["shape_ok"] and success is not False and not shape_bad:
            if os.getenv("TOUCHPOINT_VERIFY_RAW", "").strip() in {"1", "true", "yes"}:
                row["raw_verify"] = verify_mongo_raw(str(cid), op)
        results.append(row)
        status = "PASS" if row["shape_ok"] and success is not False else "FAIL"
        if success is False:
            status = "FAIL"
        if not row["shape_ok"]:
            status = "SHAPE_FAIL"
        print(
            f"[{status}] {op} / {touch} success={success} shape_ok={row['shape_ok']} "
            f"cid={(cid or '')[:8]}"
        )
        if status != "PASS" and body.get("errors"):
            print("  ", json.dumps(body.get("errors"))[:300])
    return {"results": results, "seed": seed.__dict__}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true", default=True)
    parser.add_argument("--verify-raw", action="store_true")
    args = parser.parse_args()
    if args.verify_raw:
        os.environ["TOUCHPOINT_VERIFY_RAW"] = "1"
    if not _bearer():
        print("FAIL: BEARER_TOKEN missing")
        return 1
    seed = _seed()
    out = run_smoke(seed)
    # persist seed
    seed_path = ROOT / "reports" / "touchpoint-live-seed.json"
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed_path.write_text(json.dumps(out["seed"], indent=2) + "\n")
    report = ROOT / "reports" / "touchpoint-verify-smoke.json"
    report.write_text(json.dumps(out, indent=2, default=str) + "\n")
    shape_fails = [r for r in out["results"] if not r["shape_ok"]]
    hard_fails = [r for r in out["results"] if r["success"] is False]
    print(
        f"\nSummary: {len(out['results'])} checks, "
        f"{len(shape_fails)} shape fails, {len(hard_fails)} success=false"
    )
    print("wrote", report)
    # Shape fails are hard failures; success=false may be downstream PP
    return 1 if shape_fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
