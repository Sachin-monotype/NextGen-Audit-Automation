"""Track (operation → xCorrelationId) for events we generate.

Why this exists
---------------
Compare used to take the *latest* raw+enriched pair for an operation. On a shared
PP queue someone else can fire the same mutation at the same time, so "latest"
is not necessarily *ours*.

Mitigation: mint ``x-correlation-id`` on every generate (GraphQL header / ingress /
cron envelope), persist ``operation → correlation_id``, and prefer that pair when
staging Mongo samples for Compare.

Cron / ingress payloads that share the same ``source.operation`` (e.g. five LMS
windows) are tracked under ``by_case`` (``cron:lmsopen``) so each case keeps its
own correlation and staging file.

Important: ``xCorrelationId`` is **per request / per event**, NOT per user.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_DEFAULT_REL = Path("reports") / "generated-correlations.json"


def _path(project_root: Path | None = None) -> Path:
    if project_root is not None:
        root = project_root
    else:
        from .project_root import find_project_root

        root = find_project_root()
    return root / _DEFAULT_REL


def merge_legacy_correlation_store(*, project_root: Path | None = None) -> int:
    """Merge ``backend/reports/generated-correlations.json`` into the canonical store."""
    from .project_root import find_project_root

    root = project_root or find_project_root()
    canonical = _path(root)
    legacy = root / "backend" / "reports" / "generated-correlations.json"
    if not legacy.is_file():
        return 0
    try:
        legacy_data = json.loads(legacy.read_text(encoding="utf-8"))
    except Exception:
        return 0
    legacy_ops = legacy_data.get("by_operation") or {}
    if not isinstance(legacy_ops, dict) or not legacy_ops:
        return 0

    canonical.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        data: dict[str, Any] = {}
        if canonical.is_file():
            try:
                data = json.loads(canonical.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        by_op = data.setdefault("by_operation", {})
        merged = 0
        for op, entry in legacy_ops.items():
            if not isinstance(entry, dict):
                continue
            cur = by_op.get(op) if isinstance(by_op.get(op), dict) else {}
            cur_ts = str(cur.get("generated_at") or "")
            new_ts = str(entry.get("generated_at") or "")
            if not cur or (new_ts and new_ts >= cur_ts):
                by_op[op] = entry
                merged += 1
        if merged:
            data["updated_at"] = _now()
            data["legacy_merge_from"] = str(legacy)
            canonical.write_text(
                json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
    return merged


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_store(path: Path, data: dict[str, Any]) -> None:
    data["updated_at"] = _now()
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def record_generation(
    operation: str,
    correlation_id: str,
    *,
    project_root: Path | None = None,
    kind: str = "graphql",
    meta: dict[str, Any] | None = None,
    case_key: str | None = None,
) -> None:
    """Remember that we generated ``operation`` under ``correlation_id``.

    When ``case_key`` is set (``cron:lmsopen``, ``ingress:fontBridge``), the case
    entry is authoritative for staging — ``by_operation`` is still updated for
    backward compatibility.
    """
    op = (operation or "").strip()
    cid = (correlation_id or "").strip()
    if not op or not cid:
        return
    ck = (case_key or "").strip() or None
    if not ck:
        case_id = str((meta or {}).get("case_id") or "").strip()
        if case_id and kind in {"cron", "ingress"}:
            from .case_keys import cron_case_key, ingress_case_key

            ck = cron_case_key(case_id) if kind == "cron" else ingress_case_key(case_id)

    path = _path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        data: dict[str, Any] = {}
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        by_op = data.setdefault("by_operation", {})
        by_case = data.setdefault("by_case", {})
        by_corr = data.setdefault("by_correlation", {})

        entry = {
            "operation": op,
            "xCorrelationId": cid,
            "kind": kind,
            "generated_at": _now(),
            **(meta or {}),
        }
        if ck:
            entry["case_key"] = ck

        history = list(by_op.get(op, {}).get("history") or [])
        history.insert(0, {"xCorrelationId": cid, "generated_at": entry["generated_at"], "kind": kind})
        entry["history"] = history[:20]
        by_op[op] = entry

        if ck:
            case_history = list(by_case.get(ck, {}).get("history") or [])
            case_history.insert(
                0, {"xCorrelationId": cid, "generated_at": entry["generated_at"], "kind": kind}
            )
            entry_case = {**entry, "history": case_history[:20]}
            by_case[ck] = entry_case

        by_corr[cid] = {
            "operation": op,
            "case_key": ck,
            "kind": kind,
            "generated_at": entry["generated_at"],
            **(meta or {}),
        }
        _write_store(path, data)


def get_owned_correlation(
    operation: str,
    *,
    project_root: Path | None = None,
    case_key: str | None = None,
) -> str | None:
    """Latest correlation we minted for ``operation`` or ``case_key``."""
    ck = (case_key or "").strip()
    if ck:
        cid = _get_case_correlation(ck, project_root=project_root)
        if cid:
            return cid
    op = (operation or "").strip()
    if not op:
        return None
    path = _path(project_root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    entry = (data.get("by_operation") or {}).get(op) or {}
    cid = str(entry.get("xCorrelationId") or "").strip()
    return cid or None


def _get_case_correlation(case_key: str, *, project_root: Path | None = None) -> str | None:
    path = _path(project_root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    entry = (data.get("by_case") or {}).get(case_key) or {}
    cid = str(entry.get("xCorrelationId") or "").strip()
    return cid or None


def lookup_by_correlation(
    correlation_id: str,
    *,
    project_root: Path | None = None,
) -> dict[str, Any] | None:
    """Resolve case_key + operation for a minted correlation id (payload file naming)."""
    cid = (correlation_id or "").strip()
    if not cid:
        return None
    path = _path(project_root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    entry = (data.get("by_correlation") or {}).get(cid)
    return entry if isinstance(entry, dict) else None


def list_owned(*, project_root: Path | None = None) -> dict[str, Any]:
    path = _path(project_root)
    if not path.is_file():
        return {"by_operation": {}, "by_case": {}, "by_correlation": {}, "updated_at": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"by_operation": {}, "by_case": {}, "by_correlation": {}, "updated_at": None}
