#!/usr/bin/env python3
"""Push NEW export TestRail cases from fdc14091_export_testrail_cases.json only.

Does NOT read or update fdc14091_testrail_map.json.

Usage:
  PYTHONPATH=python python3 scripts/push_export_testrail_cases.py --dry-run
  PYTHONPATH=python python3 scripts/push_export_testrail_cases.py
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
PACK = REPO / "python" / "audit_validator" / "data" / "fdc14091_export_testrail_cases.json"
QA_ENV = REPO.parent / "qa_agent" / ".env"
LOCAL_ENV = REPO / ".env"

PLATFORMS = 2
LEVELS = 2


def _load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    for p in (QA_ENV, LOCAL_ENV):
        if p.is_file():
            for k, v in dotenv_values(p).items():
                if v and k not in out:
                    out[k] = v
    for k in ("TESTRAIL_URL", "TESTRAIL_USERNAME", "TESTRAIL_PASSWORD", "TESTRAIL_API_KEY"):
        if os.getenv(k):
            out[k] = os.environ[k]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if not PACK.is_file():
        print(f"Missing pack file: {PACK}. Run scripts/build_export_testrail_pack.py first.", file=sys.stderr)
        return 1

    pack = json.loads(PACK.read_text(encoding="utf-8"))
    section_id = int(pack.get("suite_section") or 4066542)
    cases = pack.get("cases") or []
    if args.limit:
        cases = cases[: args.limit]

    env = _load_env()
    base = (env.get("TESTRAIL_URL") or "").rstrip("/")
    user = env.get("TESTRAIL_USERNAME") or ""
    pwd = env.get("TESTRAIL_API_KEY") or env.get("TESTRAIL_PASSWORD") or ""
    if not base or not user or not pwd:
        print("Missing TESTRAIL_URL / USERNAME / API_KEY", file=sys.stderr)
        return 1

    auth = (user, pwd)
    headers = {"Content-Type": "application/json"}
    created: list[dict] = []

    print(f"Creating {len(cases)} NEW export cases in section {section_id} (dry_run={args.dry_run})")
    for i, item in enumerate(cases, 1):
        title = item.get("title") or item.get("operation")
        payload = {
            "title": title,
            "estimate": item.get("estimate") or "15m",
            "refs": item.get("refs") or "FDC-14091",
            "custom_platforms": PLATFORMS,
            "custom_levels": LEVELS,
            "custom_preconds": item.get("custom_preconds") or pack.get("preconditions_template"),
            "custom_steps_separated": item.get("custom_steps_separated") or [],
        }
        print(f"[{i}/{len(cases)}] {title} steps={len(payload['custom_steps_separated'])}")
        if args.dry_run:
            continue
        resp = requests.post(
            f"{base}/index.php?/api/v2/add_case/{section_id}",
            auth=auth,
            headers=headers,
            json=payload,
            timeout=60,
        )
        if resp.status_code >= 400:
            print(f"  FAIL {resp.status_code}: {resp.text[:400]}", file=sys.stderr)
            continue
        data = resp.json()
        case_id = data.get("id")
        created.append({"case_id": case_id, "title": title, "operation": item.get("operation"), "key": item.get("key")})
        print(f"  → C{case_id}")
        time.sleep(0.35)

    if created and not args.dry_run:
        out = REPO / "reports" / "export_testrail_created.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"created": created}, indent=2) + "\n", encoding="utf-8")
        print(f"Saved created case ids → {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
