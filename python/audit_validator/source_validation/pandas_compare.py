"""Fast bulk comparison via pandas (temp CSV/pickle)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .comparison_rows import ComparisonRow
from .discovery_resolver import normalize_compare


def rows_to_records(rows: list[ComparisonRow]) -> list[dict[str, Any]]:
    return [
        {
            "operation": r.operation,
            "field": r.field or r.field_path,
            "node_subnode": " / ".join(p for p in (r.node, r.sub_node) if p),
            "field_path": r.field_path,
            "source": r.source_system,
            "value_in_enriched_json": r.value_in_enriched,
            "value_in_source_json": r.value_in_source,
            "status": r.match_status,
            "remark": r.notes,
        }
        for r in rows
    ]


def export_comparison_frame(
    rows: list[ComparisonRow],
    *,
    out_dir: Path,
    prefix: str = "comparison",
) -> dict[str, Path]:
    """Write CSV + pickle for pandas workflows; return paths."""
    import pandas as pd

    out_dir.mkdir(parents=True, exist_ok=True)
    records = rows_to_records(rows)
    df = pd.DataFrame.from_records(records)
    csv_path = out_dir / f"{prefix}.csv"
    pkl_path = out_dir / f"{prefix}.pkl"
    df.to_csv(csv_path, index=False)
    df.to_pickle(pkl_path)
    return {"csv": csv_path, "pickle": pkl_path, "dataframe": df}


def compare_enriched_snapshots(
    *,
    expected: dict[str, Any],
    actual: dict[str, Any],
    prefix: str = "",
) -> list[dict[str, str]]:
    """Deep-compare two enriched snapshot dicts; return mismatch rows."""
    mismatches: list[dict[str, str]] = []

    def walk(exp: object, act: object, path: str) -> None:
        if isinstance(exp, dict) and isinstance(act, dict):
            keys = set(exp.keys()) | set(act.keys())
            for k in sorted(keys):
                walk(exp.get(k), act.get(k), f"{path}.{k}" if path else k)
            return
        if isinstance(exp, list) and isinstance(act, list):
            if len(exp) != len(act):
                mismatches.append(
                    {
                        "path": path,
                        "expected": json.dumps(exp, default=str)[:200],
                        "actual": json.dumps(act, default=str)[:200],
                    }
                )
                return
            for i, (e, a) in enumerate(zip(exp, act)):
                walk(e, a, f"{path}[{i}]")
            return
        ev = normalize_compare(exp)
        av = normalize_compare(act)
        if ev != av:
            mismatches.append({"path": path, "expected": ev[:200], "actual": av[:200]})

    walk(expected, actual, prefix)
    return mismatches


def summarize_dataframe(df: Any) -> dict[str, int]:
    if df is None or df.empty:
        return {"total": 0, "pass": 0, "fail": 0, "skip": 0, "na": 0}
    counts = df["status"].value_counts().to_dict()
    return {
        "total": int(len(df)),
        "pass": int(counts.get("PASS", 0)),
        "fail": int(counts.get("FAIL", 0)),
        "skip": int(counts.get("SKIP", 0)),
        "na": int(counts.get("N/A", 0)),
    }
