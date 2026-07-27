"""Restore comparison-latest.json from recent jobs and generate-run reports."""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_SCENARIO_LABEL_RE = re.compile(r"\s*\((?:UI|BE)\)\s*$", re.I)


def parse_iso_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def scenario_display_key(sc: dict[str, Any]) -> str:
    label = str(sc.get("label") or sc.get("operation") or "").strip()
    return _SCENARIO_LABEL_RE.sub("", label).strip()


def operation_matches_stored(stored_keys: set[str] | frozenset[str], operation: str) -> bool:
    op = (operation or "").strip()
    if not op:
        return False
    if op in stored_keys:
        return True
    base = op.split("(", 1)[0].strip()
    for key in stored_keys:
        if key == op:
            return True
        key_base = key.split("(", 1)[0].strip()
        if key_base == base and "(" in op:
            return True
        if key.startswith(f"{op}(") or op.startswith(f"{key}("):
            return True
    return False


def _cutoff(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=max(1, days))


def restore_from_jobs(
    project_root: Path,
    *,
    jobs_path: Path | None = None,
    days: int = 7,
    update_existing: bool = True,
) -> dict[str, Any]:
    """Merge compare-job rows from the last ``days`` into comparison-latest.json."""
    from .comparison_store import _load, _store_path, _summary_for_rows

    jpath = jobs_path or (project_root / "reports" / "jobs-state.json")
    if not jpath.is_file():
        return {"restored": 0, "updated": 0, "merged": 0, "error": "jobs-state.json not found"}

    try:
        jobs_doc = json.loads(jpath.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"restored": 0, "updated": 0, "merged": 0, "error": str(exc)}

    since = _cutoff(days)
    best_by_op: dict[str, tuple[datetime, list[dict[str, Any]], str, str, str]] = {}
    jobs_seen = 0
    for job in jobs_doc.get("jobs") or []:
        if job.get("kind") != "compare" or job.get("status") != "completed":
            continue
        ts = parse_iso_ts(str(job.get("finished_at") or job.get("started_at") or ""))
        if not ts or ts < since:
            continue
        jobs_seen += 1
        rows = (job.get("result") or {}).get("rows") or []
        if not rows:
            continue
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            op = str(row.get("operation") or "")
            if op:
                grouped[op].append(row)
        job_id = str(job.get("id") or "")
        job_kind = str(job.get("kind") or "compare")
        ts_text = ts.isoformat()
        for op, op_rows in grouped.items():
            prev = best_by_op.get(op)
            if prev is None or ts > prev[0]:
                best_by_op[op] = (ts, op_rows, job_id, job_kind, ts_text)

    if not best_by_op:
        return {
            "restored": 0,
            "updated": 0,
            "merged": len(_load(_store_path(project_root))),
            "jobs_in_window": jobs_seen,
            "error": f"No completed compare jobs with rows in last {days} days",
        }

    path = _store_path(project_root)
    data = _load(path)
    before = len(data)
    restored = 0
    updated = 0
    for op, (_ts, op_rows, job_id, job_kind, ts_text) in best_by_op.items():
        if op in data:
            if not update_existing:
                continue
            prev_ts = parse_iso_ts(str(data[op].get("compared_at") or ""))
            if prev_ts and prev_ts >= _ts:
                continue
            updated += 1
        else:
            restored += 1
        data[op] = {
            "operation": op,
            "compared_at": ts_text,
            "job_id": job_id,
            "job_kind": job_kind,
            "summary": _summary_for_rows(op_rows),
            "rows": op_rows,
        }

    if restored or updated:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )

    return {
        "ok": True,
        "restored": restored,
        "updated": updated,
        "merged": len(data),
        "before": before,
        "jobs_in_window": jobs_seen,
        "days": days,
    }


def collect_generate_pass_scenarios(
    project_root: Path,
    *,
    days: int = 7,
) -> dict[str, dict[str, Any]]:
    """PASS generate scenarios with raw/enrich landing in the last ``days``."""
    since = _cutoff(days)
    runs_dir = project_root / "reports" / "generate-runs"
    if not runs_dir.is_dir():
        return {}

    best: dict[str, dict[str, Any]] = {}
    paths = [runs_dir / "last.json", *sorted(runs_dir.glob("generate-run-*.json"))]
    seen_files: set[Path] = set()
    for path in paths:
        if not path.is_file():
            continue
        resolved = path.resolve()
        if resolved in seen_files:
            continue
        seen_files.add(resolved)
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        saved = parse_iso_ts(str(doc.get("saved_at") or doc.get("checked_at") or "")) or mtime
        if saved < since and path.name != "last.json":
            continue
        for sc in doc.get("scenarios") or []:
            if not isinstance(sc, dict):
                continue
            if str(sc.get("status") or "").upper() != "PASS":
                continue
            if not (sc.get("raw") or sc.get("enriched")):
                continue
            key = scenario_display_key(sc)
            if not key:
                continue
            prev = best.get(key)
            prev_ts = parse_iso_ts(str(prev.get("saved_at") or "")) if prev else None
            if prev_ts and saved <= prev_ts:
                continue
            best[key] = {
                **sc,
                "key": key,
                "saved_at": saved.isoformat(),
                "source_file": path.name,
            }
    return best


def compare_missing_from_generate_runs(
    project_root: Path,
    *,
    days: int = 7,
) -> dict[str, Any]:
    """Run source validation for recent PASS generate scenarios missing from the store."""
    from .comparison_store import _load, _store_path, save_batch_results

    from audit_validator.source_validation.runner import run_source_validation

    scenarios = collect_generate_pass_scenarios(project_root, days=days)
    stored_keys = set(_load(_store_path(project_root)).keys())
    missing = {
        key: meta
        for key, meta in scenarios.items()
        if not operation_matches_stored(stored_keys, key)
    }
    if not missing:
        return {"compared": 0, "candidates": len(scenarios), "missing": 0, "ok": True}

    enrich_dir = project_root / "payload" / "enrich"
    raw_dir = project_root / "payload" / "raw"
    enrich_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    ops: list[str] = []
    for key, meta in missing.items():
        enriched = meta.get("enriched_event")
        if not isinstance(enriched, dict) or not enriched:
            continue
        enrich_dir.joinpath(f"{key}.json").write_text(
            json.dumps(enriched, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        raw = meta.get("raw_event")
        if isinstance(raw, dict) and raw:
            raw_dir.joinpath(f"{key}.json").write_text(
                json.dumps(raw, indent=2, ensure_ascii=False, default=str) + "\n",
                encoding="utf-8",
            )
        ops.append(key)

    if not ops:
        return {
            "compared": 0,
            "candidates": len(scenarios),
            "missing": len(missing),
            "error": "No enriched_event JSON to stage",
        }

    log.info("Restoring %s comparison(s) from generate runs", len(ops))
    report = run_source_validation(
        project_root=project_root,
        operations=ops,
        iteration=1,
        sample_source="fresh",
    )
    rows = [
        {
            "operation": r.operation,
            "field": r.field,
            "field_path": r.field_path,
            "node": r.node,
            "sub_node": r.sub_node,
            "layer": r.layer,
            "source_system": r.source_system,
            "source_api": r.source_api,
            "value_in_source": r.value_in_source,
            "value_in_enriched": r.value_in_enriched,
            "match_status": r.match_status,
            "notes": r.notes,
        }
        for r in report.comparison_rows
    ]
    compared_at = datetime.now(timezone.utc).isoformat()
    save_batch_results(
        project_root,
        rows=rows,
        job_id="restore-generate-runs",
        job_kind="restore",
        compared_at=compared_at,
    )
    touched = len({r["operation"] for r in rows if r.get("operation")})
    return {
        "ok": True,
        "compared": touched,
        "candidates": len(scenarios),
        "missing": len(missing),
        "passed": report.passed,
        "failed": report.failed,
        "skipped": report.skipped,
    }


def restore_recent_comparisons(
    project_root: Path,
    *,
    days: int = 7,
    compare_missing: bool = False,
) -> dict[str, Any]:
    """Restore unique comparison results from the last ``days`` (compare jobs only by default)."""
    job_stats = restore_from_jobs(project_root, days=days, update_existing=True)
    gen_stats: dict[str, Any] = {"compared": 0, "candidates": 0, "missing": 0}
    if compare_missing:
        try:
            gen_stats = compare_missing_from_generate_runs(project_root, days=days)
        except Exception as exc:
            log.warning("Generate-run compare restore failed: %s", exc)
            gen_stats = {"compared": 0, "error": str(exc)}
    from .comparison_store import list_latest

    final = list_latest(project_root)
    return {
        "ok": True,
        "days": days,
        "jobs": job_stats,
        "generate": gen_stats,
        "total_operations": final.get("count", 0),
    }


def stored_operation_index(project_root: Path) -> dict[str, str]:
    """Map stored operation → compared_at ISO timestamp."""
    from .comparison_store import _load, _store_path

    data = _load(_store_path(project_root))
    return {
        str(op): str(item.get("compared_at") or "")
        for op, item in data.items()
        if isinstance(item, dict)
    }
