#!/usr/bin/env python3
"""Run Monotype Connect desktop UI automation and validate today's ConnectService logs.

Opens the desktop app (or attaches via CDP), triggers events from desktop_navigation.json,
extracts xCorrelationId from [CurlDebug] log lines targeting the ingress audit URL,
and writes an Excel validation report.

Usage:
  python scripts/run_desktop_ui_automation.py
  python scripts/run_desktop_ui_automation.py --validate-only
  python scripts/run_desktop_ui_automation.py --operations appSettingsPluginInstallAllEnabled,appLogsExported
  python scripts/run_desktop_ui_automation.py --connect-only
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
except ImportError:
    pass

from audit_validator.desktop.config import default_log_dir  # noqa: E402
from audit_validator.desktop.runner import run_desktop_ui_automation  # noqa: E402
from audit_validator.project_root import find_project_root  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Desktop UI navigation automation + log validation")
    parser.add_argument("--project-root", type=Path, default=None)
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=None,
        help="ConnectService log directory (default: LOGS_PATH env or platform default)",
    )
    parser.add_argument(
        "--operations",
        default=None,
        help="Comma-separated operation names to run (default: all automatable events)",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Skip UI automation; parse today's logs only",
    )
    parser.add_argument(
        "--connect-only",
        action="store_true",
        help="Attach to running desktop app via CDP (DESKTOP_CDP_PORT, default 9222)",
    )
    parser.add_argument(
        "--include-manual",
        action="store_true",
        help="Include non-automatable events in validation report",
    )
    parser.add_argument("--settle-sec", type=float, default=2.0, help="Pause after each UI trigger")
    parser.add_argument(
        "--post-settle-sec",
        type=float,
        default=120.0,
        help="Wait before reading logs after all triggers (ConnectService often lags ~2 min)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    root = args.project_root or find_project_root()
    log_dir = args.log_dir or default_log_dir()
    ops = {o.strip() for o in (args.operations or "").split(",") if o.strip()} or None

    if not log_dir.is_dir():
        print(f"Error: log directory not found: {log_dir}", file=sys.stderr)
        print("Set LOGS_PATH in .env (see .env.example)", file=sys.stderr)
        return 1

    db = None
    try:
        import os

        sys.path.insert(0, str(ROOT / "backend"))
        from app.config import load_settings
        from app.db import AuditDatabase

        settings = load_settings()
        # Connect/ingress events land in Preprod even when AUDIT_TARGET=qa.
        mongo_db = (os.getenv("DESKTOP_MONGO_DB") or "AuditLogsPreprod").strip()
        settings.mongo_db = mongo_db
        db = AuditDatabase(settings)
        print(f"Mongo verify db: {mongo_db}")
    except Exception as exc:  # noqa: BLE001
        print(f"Mongo verify disabled: {exc}", file=sys.stderr)

    result = run_desktop_ui_automation(
        project_root=root,
        log_dir=log_dir,
        operations=ops,
        validate_only=args.validate_only,
        connect_only=args.connect_only,
        include_manual=args.include_manual,
        settle_sec=args.settle_sec,
        post_settle_sec=args.post_settle_sec,
        db=db,
        progress=print,
    )

    if result.ui.errors:
        for err in result.ui.errors:
            print(f"UI warning: {err}", file=sys.stderr)

    print(f"\nJSON: {result.json_path}")
    print(f"Excel: {result.xlsx_path}")
    print(
        f"Summary: PASS={result.validation.summary.pass_count} "
        f"FAIL={result.validation.summary.fail_count} "
        f"SKIP={result.validation.summary.skip_count}"
    )
    return 0 if result.validation.summary.fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
