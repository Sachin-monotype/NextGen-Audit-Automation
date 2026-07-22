#!/usr/bin/env python3
"""Sync GraphQL Generate CasePilot recipes → TestRail section 4066542 (FDC-14091).

Updates existing mapped cases with detailed custom_steps_separated from
``ui_case_recipes.testrail_steps_separated``. Uses qa_agent .env TestRail creds.

Usage:
  PYTHONPATH=python python3 scripts/sync_fdc14091_testrail_steps.py
  PYTHONPATH=python python3 scripts/sync_fdc14091_testrail_steps.py --dry-run
  PYTHONPATH=python python3 scripts/sync_fdc14091_testrail_steps.py --limit 5
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import dotenv_values

REPO = Path(__file__).resolve().parent.parent
MAP_PATH = REPO / "python" / "audit_validator" / "data" / "fdc14091_testrail_map.json"
QA_ENV = REPO.parent / "qa_agent" / ".env"
LOCAL_ENV = REPO / ".env"

SECTION_ID = 4066542
REFS = "FDC-14091"
ESTIMATE = "20m"
# Required custom fields — type 6 dropdowns (single int), not multi-select arrays
PLATFORMS = 2  # matches existing FDC-14091 cases
LEVELS = 2


def _load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    for p in (QA_ENV, LOCAL_ENV):
        if p.is_file():
            for k, v in dotenv_values(p).items():
                if v and k not in out:
                    out[k] = v
    # process env wins
    for k in (
        "TESTRAIL_URL",
        "TESTRAIL_USERNAME",
        "TESTRAIL_PASSWORD",
        "TESTRAIL_API_KEY",
    ):
        if os.getenv(k):
            out[k] = os.environ[k]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ops", default="", help="Comma-separated operation filter")
    args = ap.parse_args()

    sys.path.insert(0, str(REPO / "python"))
    from audit_validator.touchpoint.scenarios import list_scenarios
    from audit_validator.ui_case_recipes import testrail_steps_separated, testrail_steps_text
    from audit_validator.ui_testrail_map import reload_map, _load_map

    reload_map()
    m = _load_map()
    env = _load_env()
    base = (env.get("TESTRAIL_URL") or "").rstrip("/")
    user = env.get("TESTRAIL_USERNAME") or ""
    pwd = env.get("TESTRAIL_API_KEY") or env.get("TESTRAIL_PASSWORD") or ""
    if not base or not user or not pwd:
        print("Missing TESTRAIL_URL / USERNAME / API_KEY (check qa_agent/.env)", file=sys.stderr)
        return 1

    auth = (user, pwd)
    headers = {"Content-Type": "application/json"}
    op_filter = {x.strip() for x in args.ops.split(",") if x.strip()}

    catalog = [s for s in list_scenarios() if s.get("kind") == "graphql"]
    by_key = m.get("by_key") or {}
    title_by_id = {
        int(c["case_id"]): str(c.get("title") or "")
        for c in (m.get("cases") or [])
        if isinstance(c, dict) and c.get("case_id")
    }
    work: list[dict] = []
    seen: set[int] = set()
    catalog_keys: set[str] = set()

    def _add_item(case_id: int, op: str, touch: str, key: str, title: str) -> None:
        if case_id in seen:
            return
        if op_filter and op not in op_filter:
            return
        seen.add(case_id)
        work.append(
            {
                "case_id": case_id,
                "title": title,
                "operation": op,
                "touchpoint": touch,
                "key": key,
            }
        )

    for s in catalog:
        key = s.get("id") or f"{s.get('operation')}::{s.get('touchpoint')}"
        catalog_keys.add(key)
        case_id = by_key.get(key)
        if not case_id:
            continue
        _add_item(
            int(case_id),
            str(s.get("operation") or ""),
            str(s.get("touchpoint") or ""),
            key,
            title_by_id.get(int(case_id)) or str(s.get("label") or key),
        )

    # Mapped TestRail cases that are seed/setup ops (createRole, createPrivateTags, …)
    # but not standalone entries in list_scenarios().
    for c in m.get("cases") or []:
        if not isinstance(c, dict) or not c.get("case_id"):
            continue
        key = str(c.get("key") or "")
        if not key or key in catalog_keys:
            continue
        case_id = by_key.get(key) or c.get("case_id")
        if not case_id:
            continue
        op = str(c.get("operation") or "")
        touch = str(c.get("touchpoint") or "")
        if not op:
            continue
        _add_item(
            int(case_id),
            op,
            touch,
            key,
            str(c.get("title") or title_by_id.get(int(case_id)) or key),
        )

    if args.limit:
        work = work[: args.limit]

    print(f"Updating {len(work)} cases in section {SECTION_ID} (dry_run={args.dry_run})")
    ok = fail = 0
    for i, item in enumerate(work, 1):
        steps = testrail_steps_separated(item["operation"], item["touchpoint"])
        custom_preconds = (
            "1. NextGen PP/QA is reachable; log in if not already signed in.\n"
            "2. Prefer reuse of existing projects, lists, and favourites.\n"
            "3. Capture response header correlation-id (not x-correlation-id) from GraphQL.\n"
            "4. Follow plain-English steps; emit AUDIT_RESULT with real UUID after each mutation.\n"
            f"5. Jira: {REFS}."
        )
        payload = {
            "estimate": ESTIMATE,
            "refs": REFS,
            "custom_platforms": PLATFORMS,
            "custom_levels": LEVELS,
            "custom_preconds": custom_preconds,
            "custom_steps_separated": steps,
        }
        preview = testrail_steps_text(item["operation"], item["touchpoint"])
        print(
            f"[{i}/{len(work)}] C{item['case_id']} {item['operation']}({item['touchpoint']}) "
            f"steps={len(steps)} chars={len(preview)}"
        )
        if args.dry_run:
            if i == 1:
                print("--- sample steps ---")
                print(preview[:1200])
                print("---")
            ok += 1
            continue
        url = f"{base}/index.php?/api/v2/update_case/{item['case_id']}"
        r = requests.post(url, auth=auth, headers=headers, data=json.dumps(payload), timeout=60)
        if r.status_code >= 300:
            print(f"  FAIL {r.status_code}: {r.text[:400]}")
            fail += 1
        else:
            ok += 1
        time.sleep(0.25)  # be gentle

    print(f"Done ok={ok} fail={fail}")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
