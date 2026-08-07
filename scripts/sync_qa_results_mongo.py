"""Seed / sync QA Results into Atlas.

- Live ``QA Result``: upsert from local comparison-latest-qa.json
- Immutable ``QA_Original``: insert once only (skipped if already populated)

Usage:
  PYTHONPATH=python:backend backend/.venv/bin/python scripts/sync_qa_results_mongo.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    sys.path.insert(0, str(ROOT / "python"))
    sys.path.insert(0, str(ROOT / "backend"))

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")

    from app.qa_results_store import ping, seed_qa_original_once, sync_qa_local_store

    status = ping()
    print("ping:", json.dumps(status, indent=2))
    if not status.get("ok"):
        return 1
    live = sync_qa_local_store(ROOT)
    print("live QA Result sync:", json.dumps(live, indent=2))
    original = seed_qa_original_once()
    print("QA_Original seed:", json.dumps(original, indent=2))
    print("ping after:", json.dumps(ping(), indent=2))
    return 0 if live.get("ok") and original.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
