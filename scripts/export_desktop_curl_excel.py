#!/usr/bin/env python3
"""Trigger desktop catalog events, capture CurlDebug, write Excel in 4aug.xlsx format.

Columns (match teammate web export):
  event_name | scenario | target | correlation_id | auth_token | http_status | status | response | notes

Usage:
  # All automatable app events → FINAL Excel (includes login/logout)
  AUDIT_TARGET=qa PYTHONPATH=python:backend backend/.venv/bin/python \\
    scripts/export_desktop_curl_excel.py --connect-only --wait-sec 180

  # Login suite only → dtapplatestrun.xlsx
  AUDIT_TARGET=qa PYTHONPATH=python:backend backend/.venv/bin/python \\
    scripts/export_desktop_curl_excel.py --connect-only --login-only --wait-sec 120

  # Rebuild Excel from today's CurlDebug only (no UI)
  AUDIT_TARGET=qa PYTHONPATH=python:backend backend/.venv/bin/python \\
    scripts/export_desktop_curl_excel.py --logs-only --login-only
"""

from __future__ import annotations

import argparse
import json
import os
import re
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


AUTH_OPS = {
    "userLogoutApp",
    "userLoginFailureApp",
    "userLoginInitiatedApp",
    "identityLinked",
    "userSwitchWorkspaceApp",
}

LOGIN_OPS = frozenset(AUTH_OPS)

# App sometimes posts a different operation than the catalog name.
OP_ALIASES = {
    "appHealthStatusRefreshed": "appNetworkRefreshed",
}

SCENARIO_BY_CATEGORY = {
    "desktop_app_preference_page": "preferences",
    "font_activations": "font",
    "login": "login",
}

_AUTH_HEADER_RE = re.compile(
    r"""-H\s+['"]Authorization:\s*(?:Bearer\s+)?([^'"]+)['"]""",
    re.IGNORECASE,
)
_STATUS_RE = re.compile(r"\|\s*Status:\s*(\w+)", re.IGNORECASE)
_HTTP_CODE_RE = re.compile(r"\bHTTP[/\s]*(\d{3})\b", re.IGNORECASE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_auth_from_curl(curl: str) -> str:
    m = _AUTH_HEADER_RE.search(curl or "")
    if not m:
        return ""
    token = (m.group(1) or "").strip()
    if not token or token.upper() in {"[REDACTED]", "REDACTED"}:
        return ""
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


def _extract_http_status(curl: str) -> tuple[str, str]:
    """Return (http_status, status_label) from CurlDebug trailer."""
    m = _STATUS_RE.search(curl or "")
    label = (m.group(1) if m else "").strip()
    code_m = _HTTP_CODE_RE.search(curl or "")
    if code_m:
        code = code_m.group(1)
    elif label.lower() == "success":
        code = "200"
    elif label.lower() in {"failed", "failure", "error"}:
        code = "500"
    else:
        code = ""
    ok = label.lower() == "success" or code == "200"
    return code, ("OK" if ok and code else (label.upper() or "UNKNOWN"))


def _extract_curl_body(curl: str) -> str:
    if " -d '" not in (curl or ""):
        return curl or ""
    body = curl.split(" -d '", 1)[1]
    # Trailer: '  | Time: … | Status: …
    if "'  | " in body:
        body = body.split("'  | ", 1)[0]
    elif body.endswith("'"):
        body = body[:-1]
    return body


def _resolve_auth_token(curl_token: str = "") -> str:
    if curl_token and curl_token.startswith("eyJ"):
        return curl_token
    for key in ("BEARER_TOKEN", "NEXTGEN_BEARER_TOKEN"):
        val = (os.getenv(key) or "").strip()
        if val.startswith("eyJ"):
            return val
    try:
        from audit_validator.token_manager import ensure_fresh_bearer

        st = ensure_fresh_bearer(ROOT)
        token = getattr(st, "token", None) or getattr(st, "bearer", None) or ""
        if not token and hasattr(st, "present"):
            # TokenStatus keeps the live env token when valid.
            token = (os.getenv("BEARER_TOKEN") or "").strip()
        if str(token).startswith("eyJ"):
            return str(token)
    except Exception:  # noqa: BLE001
        pass
    return curl_token or ""


def _scenario_for(category: str, operation: str) -> str:
    if category in SCENARIO_BY_CATEGORY:
        return SCENARIO_BY_CATEGORY[category]
    if operation.lower().startswith("font"):
        return "font"
    if operation.lower().startswith("user"):
        return "login"
    return "preferences"


def _write_excel(
    *,
    out_path: Path,
    catalog: list[dict],
    rows_by_op: dict[str, dict],
    auth_token: str,
) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "last trigger run"
    headers = [
        "event_name",
        "scenario",
        "target",
        "correlation_id",
        "auth_token",
        "http_status",
        "status",
        "response",
        "notes",
    ]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for meta in catalog:
        op = meta["operation"]
        row = rows_by_op.get(op)
        scenario = _scenario_for(meta.get("category") or "", op)
        if not row:
            ws.append(
                [
                    op,
                    scenario,
                    "app",
                    "",
                    auth_token,
                    "",
                    "NO_CURL",
                    "",
                    meta.get("notes") or "No CurlDebug ingress captured for this catalog op",
                ]
            )
            continue
        notes = meta.get("notes") or ""
        if row.get("mapped_from"):
            notes = (
                f"Mapped from curl operation={row['mapped_from']}"
                + (f"; {notes}" if notes else "")
            )
        ws.append(
            [
                op,
                scenario,
                "app",
                row.get("xCorrelationId") or "",
                row.get("auth_token") or auth_token,
                row.get("http_status") or "",
                row.get("status") or "",
                row.get("response") or "",
                notes,
            ]
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Desktop CurlDebug → FINAL-format Excel (event_name/scenario/correlation_id/…)"
    )
    parser.add_argument("--connect-only", action="store_true", default=True)
    parser.add_argument("--logs-only", action="store_true", help="Skip UI; build Excel from today's CurlDebug")
    parser.add_argument("--wait-sec", type=float, default=180.0)
    parser.add_argument("--settle-sec", type=float, default=3.0)
    parser.add_argument("--mongo-db", default="")
    parser.add_argument(
        "--skip-auth-events",
        action="store_true",
        default=False,
        help="Skip login/logout/workspace ops (default: include them)",
    )
    parser.add_argument(
        "--include-auth-events",
        action="store_true",
        help="Deprecated alias — auth events are included by default",
    )
    parser.add_argument(
        "--login-only",
        action="store_true",
        help="Only login-category ops; default out → reports/curl-from-logs/dtapplatestrun.xlsx",
    )
    parser.add_argument("--include-today-logs", action="store_true", default=True)
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    if args.include_auth_events:
        args.skip_auth_events = False

    os.environ.setdefault("AUDIT_TARGET", "qa")
    from audit_validator.env_profiles import apply_audit_profile, get_audit_profile, mongo_db_for_profile

    apply_audit_profile(project_root=ROOT)
    if not args.mongo_db:
        args.mongo_db = (
            (os.getenv("DESKTOP_MONGO_DB") or "").strip()
            or (os.getenv("MONGO_DB_NAME") or "").strip()
            or mongo_db_for_profile(get_audit_profile())
        )

    from audit_validator.desktop.config import default_log_dir, is_audit_ingress_curl
    from audit_validator.desktop.log_extractor import (
        _extract_payloads_from_curl,
        extract_ingress_events_from_logs,
    )
    from audit_validator.desktop.navigation import load_desktop_events

    events = load_desktop_events(automatable_only=True)
    if args.login_only:
        events = [
            e
            for e in events
            if e.operation in LOGIN_OPS or (e.category or "").strip().lower() == "login"
        ]
        args.skip_auth_events = False
    if args.skip_auth_events:
        events = [e for e in events if e.operation not in AUTH_OPS]

    catalog: list[dict] = []
    seen_ops: set[str] = set()
    for e in events:
        if e.operation in seen_ops:
            continue
        seen_ops.add(e.operation)
        notes = ""
        if e.operation == "appNetworkRefreshed":
            notes = "App may emit appHealthStatusRefreshed"
        elif e.operation == "fontTempActivated":
            notes = "App may emit fontActivationTypeSwitched (source.type Font temp activation)"
        elif e.operation == "userLoginFailureApp":
            notes = "Auth0 wrong password then Close modal (CancelledByUser)"
        elif e.operation == "identityLinked":
            notes = "Emitted after successful OAuth deeplink"
        elif e.operation == "userSwitchWorkspaceApp":
            notes = "Requires a second workspace on the signed-in user"
        catalog.append(
            {
                "operation": e.operation,
                "event_name": e.event_name,
                "category": e.category,
                "notes": notes,
            }
        )

    ui_triggered: set[str] = set()
    ui_errors: list[str] = []
    log_dir = default_log_dir()
    log = sorted(log_dir.glob("file-*.log"))[-1]
    offset = log.stat().st_size

    if not args.logs_only:
        from audit_validator.desktop.runner import run_desktop_ui_automation

        print(f"Triggering {len(events)} events (unique ops={len(catalog)})")
        print(f"Mongo profile db (reference): {args.mongo_db}")
        result = run_desktop_ui_automation(
            project_root=ROOT,
            log_dir=log_dir,
            operations={e.operation for e in events},
            validate_only=False,
            connect_only=args.connect_only,
            settle_sec=args.settle_sec,
            post_settle_sec=0,
            db=None,
            progress=print,
        )
        ui_triggered = set(result.ui.triggered_operations)
        ui_errors = list(result.ui.errors)
        print(f"UI done: triggered={len(ui_triggered)} errors={len(ui_errors)}")
        print(f"Waiting {args.wait_sec:.0f}s for CurlDebug flush…")
        time.sleep(max(0.0, args.wait_sec))
    else:
        print("logs-only: skipping UI triggers")
        offset = 0

    # cid → row (keep latest occurredAt per catalog op later)
    by_cid: dict[str, dict] = {}

    def _ingest(curl: str, log_file: str) -> None:
        if not is_audit_ingress_curl(curl):
            return
        auth = _extract_auth_from_curl(curl)
        http_status, status = _extract_http_status(curl)
        body = _extract_curl_body(curl)
        for payload in _extract_payloads_from_curl(curl):
            cid = str(payload.get("xCorrelationId") or "").strip()
            if not cid:
                continue
            raw_op = str((payload.get("source") or {}).get("operation") or "").strip()
            catalog_op = OP_ALIASES.get(raw_op, raw_op)
            # Per-event body (not the full multi-event batch POST).
            event_body = json.dumps(payload, ensure_ascii=False)
            by_cid[cid] = {
                "operation": catalog_op,
                "mapped_from": raw_op if raw_op != catalog_op else "",
                "xCorrelationId": cid,
                "occurredAt": str(payload.get("occurredAt") or ""),
                "auth_token": auth,
                "http_status": http_status,
                "status": status,
                "response": event_body,
                "batch_curl_body": body if len(body) < 200_000 else body[:200_000] + "…",
                "log_file": log_file,
            }

    with log.open("r", encoding="utf-8", errors="ignore") as f:
        f.seek(min(offset, log.stat().st_size) if not args.logs_only else 0)
        chunk = f.read()
    for line in chunk.splitlines():
        if "[CurlDebug]" not in line:
            continue
        _ingest(line.split("[CurlDebug]", 1)[1].strip(), log.name)

    if args.include_today_logs or args.logs_only:
        for ev in extract_ingress_events_from_logs(log_dir, today_only=True):
            op = OP_ALIASES.get(ev.operation, ev.operation)
            if op not in seen_ops and ev.operation not in seen_ops:
                continue
            if ev.x_correlation_id in by_cid:
                continue
            _ingest(ev.raw_curl, ev.log_file)

    # Latest per catalog operation
    latest: dict[str, dict] = {}
    for row in by_cid.values():
        op = row["operation"]
        if op not in seen_ops:
            continue
        prev = latest.get(op)
        if prev is None or row["occurredAt"] >= prev["occurredAt"]:
            latest[op] = row

    # Special: fontTempActivated — prefer payload whose source.type mentions temp
    for row in by_cid.values():
        if row["operation"] != "fontActivationTypeSwitched":
            continue
        resp = row.get("response") or ""
        if "Font temp activation" in resp or "fontTemp" in resp:
            # Also fill fontTempActivated if empty, using same curl body
            if "fontTempActivated" in seen_ops:
                cand = dict(row)
                cand["operation"] = "fontTempActivated"
                cand["mapped_from"] = row.get("mapped_from") or "fontActivationTypeSwitched"
                prev = latest.get("fontTempActivated")
                if prev is None or cand["occurredAt"] >= prev["occurredAt"]:
                    latest["fontTempActivated"] = cand

    auth_token = _resolve_auth_token(
        next((r["auth_token"] for r in latest.values() if r.get("auth_token")), "")
    )
    if auth_token:
        print(f"auth_token resolved (len={len(auth_token)})")
    else:
        print("WARNING: auth_token empty (ConnectService redacts Authorization; set BEARER_TOKEN)")

    if args.out:
        out = Path(args.out)
    elif args.login_only:
        out = ROOT / "reports" / "curl-from-logs" / "dtapplatestrun.xlsx"
    else:
        out = ROOT / "reports" / "curl-from-logs" / "desktop-curl-export-FINAL.xlsx"

    path = _write_excel(
        out_path=out,
        catalog=catalog,
        rows_by_op=latest,
        auth_token=auth_token,
    )
    with_curl = sum(1 for m in catalog if m["operation"] in latest)
    print(f"\nExcel: {path}")
    print(f"Format: FINAL (last trigger run) — {with_curl}/{len(catalog)} with correlation_id")
    missing = [m["operation"] for m in catalog if m["operation"] not in latest]
    if missing:
        print("Missing:", ", ".join(missing))
    for err in ui_errors:
        print(f"UI: {err}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
