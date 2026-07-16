"""Persist the latest comparison result per operation (survives server restarts)."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

_lock = threading.Lock()


def _store_path(project_root: Path) -> Path:
    return project_root / "reports" / "comparison-latest.json"


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _summary_for_rows(op_rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "passed": sum(1 for r in op_rows if r.get("match_status") == "PASS"),
        "failed": sum(1 for r in op_rows if r.get("match_status") == "FAIL"),
        "skipped": sum(1 for r in op_rows if r.get("match_status") == "SKIP"),
        "na": sum(1 for r in op_rows if r.get("match_status") == "N/A"),
    }


def save_operation_result(
    project_root: Path,
    operation: str,
    *,
    rows: list[dict[str, Any]],
    job_id: str,
    job_kind: str,
    compared_at: str,
    summary: dict[str, Any] | None = None,
) -> None:
    """Overwrite the stored latest comparison for one operation."""
    op_rows = [r for r in rows if r.get("operation") == operation]
    if not op_rows:
        return
    save_batch_results(
        project_root,
        operation_rows={operation: op_rows},
        job_id=job_id,
        job_kind=job_kind,
        compared_at=compared_at,
        summaries={operation: summary} if summary else None,
    )


def save_batch_results(
    project_root: Path,
    *,
    rows: list[dict[str, Any]] | None = None,
    operation_rows: dict[str, list[dict[str, Any]]] | None = None,
    job_id: str,
    job_kind: str,
    compared_at: str,
    summaries: dict[str, dict[str, Any] | None] | None = None,
) -> None:
    """Write many operations in one read/write of comparison-latest.json.

    Passing ``rows`` (flat list) or ``operation_rows`` (already grouped) is fine.
    Avoids the old O(n²) rewrite that reloaded a multi‑MB file per operation.
    """
    grouped: dict[str, list[dict[str, Any]]] = dict(operation_rows or {})
    if rows:
        for r in rows:
            op = str(r.get("operation") or "")
            if not op:
                continue
            grouped.setdefault(op, []).append(r)
    if not grouped:
        return

    path = _store_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        data = _load(path)
        for op, op_rows in grouped.items():
            if not op_rows:
                continue
            data[op] = {
                "operation": op,
                "compared_at": compared_at,
                "job_id": job_id,
                "job_kind": job_kind,
                "summary": (summaries or {}).get(op) or _summary_for_rows(op_rows),
                "rows": op_rows,
            }
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )


def list_latest(project_root: Path) -> dict[str, Any]:
    """All operations with a stored latest comparison, newest first."""
    path = _store_path(project_root)
    data = _load(path)
    items = list(data.values())
    items.sort(key=lambda x: str(x.get("compared_at") or ""), reverse=True)
    merged_rows: list[dict[str, Any]] = []
    for item in sorted(data.keys()):
        merged_rows.extend(data[item].get("rows") or [])
    return {
        "operations": sorted(data.keys()),
        "items": items,
        "rows": merged_rows,
        "count": len(data),
    }


def get_latest_operation(project_root: Path, operation: str) -> dict[str, Any] | None:
    data = _load(_store_path(project_root))
    return data.get(operation)
