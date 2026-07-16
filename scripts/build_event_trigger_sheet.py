#!/usr/bin/env python3
"""Build docs/mappings/event_trigger_sheet.csv — Event / UI nav / trigger curls.

Columns match the QA spreadsheet template:
  Event | TouchPoint (UI navigation) | step 1 | step 2 | step 3

Multi-step sequences (e.g. activateFamily from List) are declared in
``python/audit_validator/data/trigger_sequences.json``. Curls come from
``curl_builder`` (+ captured raw when present under payload/raw).
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from audit_validator.curl_builder import build_curl, load_ui_navigation  # noqa: E402
from audit_validator.operation_sources import operation_source_report  # noqa: E402

SEQ_FILE = ROOT / "python" / "audit_validator" / "data" / "trigger_sequences.json"
OUT_CSV = ROOT / "docs" / "mappings" / "event_trigger_sheet.csv"
RAW_DIR = ROOT / "payload" / "raw"


def _load_sequences() -> dict:
    if SEQ_FILE.is_file():
        return json.loads(SEQ_FILE.read_text(encoding="utf-8"))
    return {}


def _raw_for(op: str) -> dict | None:
    path = RAW_DIR / f"{op}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _curl_for(op: str) -> str:
    try:
        return build_curl(op, _raw_for(op)).curl
    except Exception as exc:  # noqa: BLE001
        return f"# curl unavailable for {op}: {exc}"


def main() -> int:
    nav = load_ui_navigation()
    sequences = _load_sequences()
    report = operation_source_report()
    catalog_ops = sorted(
        {c["operation"] for c in report.get("catalog") or [] if c.get("operation")}
    )

    # One row per (event, touchpoint). Touchpoints from ui_navigation; fall back
    # to a single "default" row when navigation is empty.
    rows: list[dict[str, str]] = []
    for op in catalog_ops:
        entry = nav.get(op) or {}
        touchpoints = list(entry.get("navigation") or []) or ["(see curl — touchpoint TBD)"]
        seq_by_touch = sequences.get(op) or {}
        for touch in touchpoints:
            # Match sequence by keyword in navigation path (List / project / …)
            steps = ["curl to trigger event"]
            for key, seq in seq_by_touch.items():
                if key.lower() in touch.lower():
                    steps = list(seq)
                    break
            else:
                # Default sequence: just the event itself
                if "default" in seq_by_touch:
                    steps = list(seq_by_touch["default"])
                else:
                    steps = [op]

            curl_steps = [_curl_for(s) if not str(s).startswith("curl ") else str(s) for s in steps]
            # Pad to 3 step columns
            while len(curl_steps) < 3:
                curl_steps.append("")
            rows.append(
                {
                    "Event": op,
                    "TouchPoint": touch,
                    "step 1": curl_steps[0],
                    "step 2": curl_steps[1],
                    "step 3": curl_steps[2],
                    "section": str(entry.get("section") or ""),
                    "kind": next(
                        (
                            c["kind"]
                            for c in report.get("catalog") or []
                            if c.get("operation") == op
                        ),
                        "",
                    ),
                }
            )

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["Event", "TouchPoint", "step 1", "step 2", "step 3", "section", "kind"],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows → {OUT_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
