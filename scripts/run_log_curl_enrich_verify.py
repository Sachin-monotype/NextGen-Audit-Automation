#!/usr/bin/env python3
"""Trigger (optional) → extract curl/CID from ConnectService logs → enrich verify.

Flow:
  1. Parse today's ConnectService [CurlDebug] lines for ingress curls + xCorrelationId
  2. Optionally replay latest-per-op to ingress with a fresh xCorrelationId (trigger)
  3. Poll Mongo raw + enriched by those correlation ids
  4. Write a JSON report you can use for enrich / Compare verification

Examples:
  # Extract today's log CIDs and check Mongo QA (no replay)
  AUDIT_TARGET=qa python scripts/run_log_curl_enrich_verify.py

  # Replay latest-per-op, then poll enrich in QA
  AUDIT_TARGET=qa python scripts/run_log_curl_enrich_verify.py --replay --wait-sec 120

  # Override Mongo DB explicitly
  AUDIT_TARGET=qa python scripts/run_log_curl_enrich_verify.py --mongo-db AuditLogsQA
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
except ImportError:
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _latest_per_op(events: list[Any]) -> dict[str, Any]:
    by_op: dict[str, Any] = {}
    for ev in events:
        by_op[ev.operation] = ev
    return by_op


def _mongo_lookup(db: Any, cid: str) -> tuple[bool, bool, dict[str, Any]]:
    from audit_validator.correlation import mongo_correlation_filter

    filt = mongo_correlation_filter(cid)
    proj = {
        "_id": 0,
        "xCorrelationId": 1,
        "occurredAt": 1,
        "enrichedAt": 1,
        "source.operation": 1,
        "routingKey": 1,
    }
    raw = db["raw"].find_one(filt, projection=proj)
    enr = db["enriched"].find_one(filt, projection=proj)
    meta = {
        "raw_occurredAt": (raw or {}).get("occurredAt"),
        "enriched_occurredAt": (enr or {}).get("occurredAt"),
        "enrichedAt": (enr or {}).get("enrichedAt"),
    }
    return bool(raw), bool(enr), meta


def _replay_event(client: Any, log_event: Any) -> dict[str, Any]:
    payload = copy.deepcopy(log_event.payload)
    if not isinstance(payload, dict):
        return {
            "operation": log_event.operation,
            "ok": False,
            "error": "missing payload",
            "source_log_cid": log_event.x_correlation_id,
        }
    fresh_cid = str(uuid.uuid4())
    payload["xCorrelationId"] = fresh_cid
    payload["eventId"] = str(uuid.uuid4())
    payload["occurredAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    status, body = client.post_event(payload)
    ok = 200 <= status < 300
    event_id = ""
    try:
        parsed = json.loads(body) if body else {}
        events = (parsed.get("events") or []) if isinstance(parsed, dict) else []
        if events and isinstance(events[0], dict):
            event_id = str(events[0].get("eventId") or "")
            fresh_cid = str(events[0].get("xCorrelationId") or fresh_cid)
    except json.JSONDecodeError:
        parsed = {}
    return {
        "operation": log_event.operation,
        "ok": ok,
        "http_status": status,
        "xCorrelationId": fresh_cid,
        "eventId": event_id,
        "source_log_cid": log_event.x_correlation_id,
        "response": (body or "")[:400],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract ConnectService curl/CID details and verify enrich in Mongo"
    )
    parser.add_argument("--log-dir", type=Path, default=None)
    parser.add_argument("--today-only", action="store_true", default=True)
    parser.add_argument(
        "--operations",
        default="",
        help="Comma-separated ops (default: all found in logs)",
    )
    parser.add_argument(
        "--replay",
        action="store_true",
        help="POST latest-per-op payloads to ingress with fresh xCorrelationId",
    )
    parser.add_argument(
        "--mongo-db",
        default="",
        help="Mongo database (default: DESKTOP_MONGO_DB / MONGO_DB_NAME / "
        "profile db — AuditLogsQA when AUDIT_TARGET=qa)",
    )
    parser.add_argument("--wait-sec", type=float, default=90.0, help="Poll window for enrich")
    parser.add_argument("--poll-sec", type=float, default=5.0)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Report path (default: reports/curl-from-logs/enrich-verify-<ts>.json)",
    )
    args = parser.parse_args()

    from audit_validator.desktop.config import default_log_dir
    from audit_validator.desktop.log_extractor import extract_ingress_events_from_logs
    from audit_validator.env_profiles import (
        apply_audit_profile,
        get_audit_profile,
        mongo_db_for_profile,
    )
    from audit_validator.mongo_client import create_mongo_client

    profile = apply_audit_profile(project_root=ROOT)
    log_dir = args.log_dir or default_log_dir()
    if not log_dir.is_dir():
        print(f"Error: log directory not found: {log_dir}", file=sys.stderr)
        return 1

    ops_filter = {o.strip() for o in args.operations.split(",") if o.strip()} or None
    events = extract_ingress_events_from_logs(
        log_dir,
        operations=ops_filter,
        today_only=args.today_only,
    )
    by_op = _latest_per_op(events)
    if not by_op:
        print("No CurlDebug ingress events found in logs.", file=sys.stderr)
        return 1

    print(f"Log dir: {log_dir}")
    print(f"Today's CurlDebug events: {len(events)}  unique ops: {len(by_op)}")
    print("\n=== Log document details (latest per op) ===")
    for op, ev in sorted(by_op.items()):
        print(
            f"  {op}\n"
            f"    file={ev.log_file}:{ev.line_no}\n"
            f"    xCorrelationId={ev.x_correlation_id}\n"
            f"    occurredAt={ev.occurred_at}"
        )

    rows: list[dict[str, Any]] = []
    for op, ev in sorted(by_op.items()):
        rows.append(
            {
                "operation": op,
                "log_file": ev.log_file,
                "line_no": ev.line_no,
                "log_occurred_at": ev.occurred_at,
                "source_log_cid": ev.x_correlation_id,
                "verify_cid": ev.x_correlation_id,
                "triggered": False,
                "http_status": None,
                "curl_preview": (ev.raw_curl or "")[:240],
            }
        )

    if args.replay:
        from audit_validator.ingress.client import IngressClient, load_ingress_client_config

        cfg = load_ingress_client_config()
        if not cfg.ready:
            print("Error: ingress bearer token missing (INGRESS_BEARER_TOKEN / BEARER_TOKEN_PP)", file=sys.stderr)
            return 1
        client = IngressClient(cfg)
        print(f"\n=== Replay trigger → {cfg.base_url} ===")
        for row in rows:
            ev = by_op[row["operation"]]
            result = _replay_event(client, ev)
            row["triggered"] = True
            row["http_status"] = result.get("http_status")
            row["verify_cid"] = result.get("xCorrelationId") or row["source_log_cid"]
            row["eventId"] = result.get("eventId")
            row["trigger_ok"] = bool(result.get("ok"))
            status = "OK" if result.get("ok") else "FAIL"
            print(
                f"  {row['operation']}: {status} http={result.get('http_status')} "
                f"cid={row['verify_cid']}"
            )

    mongo_db = (
        (args.mongo_db or "").strip()
        or (os.getenv("DESKTOP_MONGO_DB") or "").strip()
        or (os.getenv("MONGO_DB_NAME") or "").strip()
        or mongo_db_for_profile(get_audit_profile())
    )
    mongo_url = (os.getenv("MONGO_DB_URL") or "").strip()
    if not mongo_url:
        print("Error: MONGO_DB_URL not set", file=sys.stderr)
        return 1

    client = create_mongo_client(mongo_url, serverSelectionTimeoutMS=10000)
    db = client[mongo_db]
    print(f"\n=== Enrich verify (mongo={mongo_db}, profile={profile.name}) ===")

    deadline = time.monotonic() + max(0.0, args.wait_sec)
    pending = {r["verify_cid"] for r in rows if r.get("verify_cid")}

    while True:
        still: set[str] = set()
        for row in rows:
            cid = row["verify_cid"]
            raw_ok, enr_ok, meta = _mongo_lookup(db, cid)
            row["mongo_raw"] = raw_ok
            row["mongo_enriched"] = enr_ok
            row.update(meta)
            if raw_ok and enr_ok:
                row["status"] = "PASS"
            elif raw_ok:
                row["status"] = "RAW_ONLY"
                still.add(cid)
            else:
                row["status"] = "MISSING"
                still.add(cid)
        if not still or time.monotonic() >= deadline:
            break
        print(f"  waiting enrich for {len(still)} cid(s)…")
        time.sleep(max(1.0, args.poll_sec))

    pass_n = sum(1 for r in rows if r.get("status") == "PASS")
    raw_n = sum(1 for r in rows if r.get("status") == "RAW_ONLY")
    miss_n = sum(1 for r in rows if r.get("status") == "MISSING")
    print(f"\nSummary: PASS={pass_n} RAW_ONLY={raw_n} MISSING={miss_n}")
    for row in rows:
        print(
            f"  {row['status']:8} {row['operation']:28} "
            f"cid={row['verify_cid']}  raw={row.get('mongo_raw')} enr={row.get('mongo_enriched')}"
        )

    out = args.out
    if out is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = ROOT / "reports" / "curl-from-logs" / f"enrich-verify-{ts}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "checked_at": _now_iso(),
        "audit_target": profile.name,
        "log_dir": str(log_dir),
        "mongo_db": mongo_db,
        "replay": bool(args.replay),
        "wait_sec": args.wait_sec,
        "summary": {"pass": pass_n, "raw_only": raw_n, "missing": miss_n, "total": len(rows)},
        "results": rows,
    }
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport: {out}")
    return 0 if miss_n == 0 and raw_n == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
