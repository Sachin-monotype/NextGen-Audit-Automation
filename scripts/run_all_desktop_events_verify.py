#!/usr/bin/env python3
"""Trigger ALL desktop navigation events, wait for ConnectService log lag, verify QA Mongo.

Ready path for when enrich is fixed:
  1. CDP desktop triggers for every event in desktop_navigation.json
  2. Wait (default 210s) for CurlDebug flush
  3. Extract fresh xCorrelationIds
  4. Poll AuditLogsQA raw + enriched

Usage:
  AUDIT_TARGET=qa python scripts/run_all_desktop_events_verify.py --connect-only
  AUDIT_TARGET=qa python scripts/run_all_desktop_events_verify.py --connect-only --wait-sec 240
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "backend"))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
except ImportError:
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description="Trigger all desktop events + QA enrich verify")
    parser.add_argument("--connect-only", action="store_true", default=True)
    parser.add_argument("--wait-sec", type=float, default=210.0)
    parser.add_argument("--settle-sec", type=float, default=3.0)
    parser.add_argument("--mongo-db", default="", help="Default: MONGO_DB_NAME / AuditLogsQA for AUDIT_TARGET=qa")
    parser.add_argument(
        "--skip-auth-events",
        action="store_true",
        help="Skip logout/login/identity/workspace events (keeps session intact)",
    )
    parser.add_argument(
        "--operations",
        default="",
        help="Optional comma-separated subset of operations",
    )
    args = parser.parse_args()

    os.environ.setdefault("AUDIT_TARGET", "qa")
    from audit_validator.env_profiles import apply_audit_profile, get_audit_profile, mongo_db_for_profile

    apply_audit_profile(project_root=ROOT)
    if not args.mongo_db:
        args.mongo_db = (
            (os.getenv("DESKTOP_MONGO_DB") or "").strip()
            or (os.getenv("MONGO_DB_NAME") or "").strip()
            or mongo_db_for_profile(get_audit_profile())
        )

    from audit_validator.correlation import mongo_correlation_filter
    from audit_validator.desktop.config import TARGET_URL, default_log_dir, is_audit_ingress_curl
    from audit_validator.desktop.log_extractor import _extract_payloads_from_curl
    from audit_validator.desktop.navigation import load_desktop_events
    from audit_validator.desktop.runner import run_desktop_ui_automation
    from audit_validator.mongo_client import create_mongo_client

    auth_ops = {
        "userLogoutApp",
        "userLoginFailureApp",
        "userLoginInitiatedApp",
        "identityLinked",
        "userSwitchWorkspaceApp",
    }
    events = load_desktop_events(automatable_only=False)
    if args.skip_auth_events:
        events = [e for e in events if e.operation not in auth_ops]
    ops_filter = {o.strip() for o in args.operations.split(",") if o.strip()} or None
    if ops_filter:
        events = [e for e in events if e.operation in ops_filter]

    ops = {e.operation for e in events}
    print(f"Events to trigger: {len(events)} (unique ops={len(ops)})")
    for e in events:
        print(f"  - {e.operation}  steps={len(e.steps)}")

    log_dir = default_log_dir()
    log = sorted(log_dir.glob("file-*.log"))[-1]
    offset = log.stat().st_size
    print(f"Log baseline: {log.name} offset={offset}")

    db = None
    try:
        from app.config import load_settings
        from app.db import AuditDatabase

        settings = load_settings()
        settings.mongo_db = args.mongo_db
        db = AuditDatabase(settings)
        print(f"Mongo verify db: {args.mongo_db}")
    except Exception as exc:  # noqa: BLE001
        print(f"Mongo binding skipped: {exc}")

    # post_settle_sec=0 here — we do our own longer wait + full curl scan after.
    result = run_desktop_ui_automation(
        project_root=ROOT,
        log_dir=log_dir,
        operations=ops,
        validate_only=False,
        connect_only=args.connect_only,
        settle_sec=args.settle_sec,
        post_settle_sec=0,
        db=db,
        progress=print,
    )

    print(
        f"\nUI done: triggered={len(result.ui.triggered_operations)} "
        f"errors={len(result.ui.errors)}"
    )
    for err in result.ui.errors:
        print(f"  UI: {err}")

    print(f"\nWaiting {args.wait_sec:.0f}s for ConnectService CurlDebug flush…")
    time.sleep(max(0.0, args.wait_sec))

    log = sorted(log_dir.glob("file-*.log"))[-1]
    with log.open("r", encoding="utf-8", errors="ignore") as f:
        f.seek(min(offset, log.stat().st_size))
        chunk = f.read()

    fresh: list[dict] = []
    seen: set[str] = set()
    for line in chunk.splitlines():
        if "[CurlDebug]" not in line:
            continue
        curl = line.split("[CurlDebug]", 1)[1].strip()
        if not is_audit_ingress_curl(curl):
            continue
        for payload in _extract_payloads_from_curl(curl):
            cid = str(payload.get("xCorrelationId") or "")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            src = payload.get("source") or {}
            fresh.append(
                {
                    "operation": src.get("operation") or "",
                    "xCorrelationId": cid,
                    "occurredAt": payload.get("occurredAt") or "",
                    "eventId": payload.get("eventId") or "",
                    "curl_preview": curl[:320],
                }
            )

    print(f"\nFresh CurlDebug CIDs: {len(fresh)}")
    for row in fresh:
        print(f"  {row['operation']:40} {row['xCorrelationId']}")

    mongo_url = (os.getenv("MONGO_DB_URL") or "").strip()
    if mongo_url and fresh:
        client = create_mongo_client(mongo_url, serverSelectionTimeoutMS=12000)
        mdb = client[args.mongo_db]
        print(f"\nPolling {args.mongo_db} enrich (up to 90s)…")
        deadline = time.monotonic() + 90
        while True:
            pending = 0
            for row in fresh:
                filt = mongo_correlation_filter(row["xCorrelationId"])
                raw = mdb["raw"].find_one(filt, projection={"_id": 0, "occurredAt": 1})
                enr = mdb["enriched"].find_one(
                    filt, projection={"_id": 0, "occurredAt": 1, "enrichedAt": 1}
                )
                row["mongo_raw"] = bool(raw)
                row["mongo_enriched"] = bool(enr)
                row["raw_occurredAt"] = (raw or {}).get("occurredAt")
                row["enrichedAt"] = (enr or {}).get("enrichedAt")
                if raw and enr:
                    row["status"] = "PASS"
                elif raw:
                    row["status"] = "RAW_ONLY"
                    pending += 1
                elif enr:
                    # Enrich without raw is a pipeline anomaly — stop waiting on it.
                    row["status"] = "ENRICH_ONLY"
                else:
                    row["status"] = "MISSING"
                    pending += 1
            if pending == 0 or time.monotonic() >= deadline:
                break
            print(f"  waiting enrich for {pending}…")
            time.sleep(10)

        print(f"\n=== {args.mongo_db} ===")
        for row in fresh:
            print(
                f"  {row.get('status','?'):8} {row['operation']:40} "
                f"raw={row.get('mongo_raw')} enr={row.get('mongo_enriched')} "
                f"cid={row['xCorrelationId']}"
            )

    out_dir = ROOT / "reports" / "curl-from-logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = out_dir / f"all-23-verify-{ts}.json"
    report = {
        "checked_at": _now(),
        "mongo_db": args.mongo_db,
        "wait_sec": args.wait_sec,
        "catalog_events": len(events),
        "triggered_operations": result.ui.triggered_operations,
        "ui_errors": result.ui.errors,
        "step_results": [
            {
                "operation": s.event_operation,
                "description": s.step_description,
                "status": s.status,
                "error": s.error,
            }
            for s in result.ui.step_results
        ],
        "fresh_events": fresh,
        "summary": {
            "triggered": len(result.ui.triggered_operations),
            "ui_errors": len(result.ui.errors),
            "fresh_curls": len(fresh),
            "mongo_pass": sum(1 for r in fresh if r.get("status") == "PASS"),
            "mongo_raw_only": sum(1 for r in fresh if r.get("status") == "RAW_ONLY"),
            "mongo_missing": sum(1 for r in fresh if r.get("status") == "MISSING"),
        },
    }
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport: {out}")
    print("Summary:", report["summary"])

    # Soft success: UI attempted all; curl/mongo may lag until enrich build is fixed.
    return 0 if result.ui.triggered_operations else 1


if __name__ == "__main__":
    raise SystemExit(main())
