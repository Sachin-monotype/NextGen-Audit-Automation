"""Track (operation → xCorrelationId) for events we generate.

Why this exists
---------------
Compare used to take the *latest* raw+enriched pair for an operation. On a shared
PP queue someone else can fire the same mutation at the same time, so "latest"
is not necessarily *ours*.

Mitigation: mint ``x-correlation-id`` on every generate (GraphQL header / ingress /
cron envelope), persist ``operation → correlation_id``, and prefer that pair when
staging Mongo samples for Compare.

Important: ``xCorrelationId`` is **per request / per event**, NOT per user.
The same Bearer can fire many activateFamily calls — each gets a new correlation.
Actor identity (globalUserId / gcid) comes from the Bearer claims and stays the
same across those events; correlation does not.
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
    root = project_root or Path.cwd()
    return root / _DEFAULT_REL


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_generation(
    operation: str,
    correlation_id: str,
    *,
    project_root: Path | None = None,
    kind: str = "graphql",
    meta: dict[str, Any] | None = None,
) -> None:
    """Remember that we generated ``operation`` under ``correlation_id``."""
    op = (operation or "").strip()
    cid = (correlation_id or "").strip()
    if not op or not cid:
        return
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
        entry = {
            "operation": op,
            "xCorrelationId": cid,
            "kind": kind,
            "generated_at": _now(),
            **(meta or {}),
        }
        # Keep a short history so we can still find an older owned run.
        history = list(by_op.get(op, {}).get("history") or [])
        history.insert(0, {"xCorrelationId": cid, "generated_at": entry["generated_at"], "kind": kind})
        entry["history"] = history[:20]
        by_op[op] = entry
        data["updated_at"] = _now()
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def get_owned_correlation(
    operation: str,
    *,
    project_root: Path | None = None,
) -> str | None:
    """Latest correlation we minted for ``operation``, or None."""
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


def list_owned(*, project_root: Path | None = None) -> dict[str, Any]:
    path = _path(project_root)
    if not path.is_file():
        return {"by_operation": {}, "updated_at": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"by_operation": {}, "updated_at": None}
