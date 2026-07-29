"""Capture ingress xCorrelationId from Monotype Connect service logs after UI runs.

Workflow for app/plugin ingress Generate-in-UI:
  1. Snapshot or truncate today's Connect service log before CasePilot dispatch.
  2. CasePilot performs the desktop/plugin action (no manual log grep).
  3. After the browser run finishes, poll the log (up to ~5 min) for new events.
  4. Extract unique xCorrelationId per expected operation and feed verification.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .connect_log_parser import (
    ConnectLogEvent,
    DEFAULT_LOG_DIR,
    group_by_operation,
    latest_connect_log_file,
    parse_connect_service_log,
)

ProgressFn = Callable[[str], None]

_HARVEST_LOCK = threading.Lock()
_HARVEST_SCHEDULED: set[str] = set()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_touch(touch: str) -> str:
    return " ".join((touch or "").lower().replace("_", " ").split())


def connect_log_capture_enabled() -> bool:
    raw = (os.getenv("CONNECT_LOG_CAPTURE") or "true").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if os.name != "posix" or os.uname().sysname != "Darwin":
        return False
    return True


def connect_log_prepare_mode() -> str:
    """``truncate`` (archive + clear) or ``offset`` (non-destructive byte marker)."""
    mode = (os.getenv("CONNECT_LOG_PREPARE") or "truncate").strip().lower()
    return "offset" if mode == "offset" else "truncate"


def connect_log_settle_sec() -> int:
    return max(30, int(os.getenv("CONNECT_LOG_SETTLE_SEC", "300") or "300"))


def connect_log_poll_sec() -> int:
    return max(5, int(os.getenv("CONNECT_LOG_POLL_SEC", "15") or "15"))


def selection_uses_connect_log_capture(selection: list[dict[str, Any]]) -> bool:
    """True when selection is app/plugin ingress (xCorrelationId lives in Connect logs)."""
    for item in selection:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("id") or "").strip()
        if sid.startswith("ingress:"):
            return True
        touch = _norm_touch(str(item.get("touchpoint") or ""))
        if touch in {"app", "plugin", "desktop app", "desktop ui"}:
            return True
        if "desktop" in touch and "ui" in touch:
            return True
    return False


def expected_operations_from_selection(selection: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in selection:
        if not isinstance(item, dict):
            continue
        op = str(item.get("operation") or "").strip()
        if not op or op in seen:
            continue
        seen.add(op)
        out.append(op)
    return out


@dataclass
class ConnectLogBaseline:
    path: str
    byte_offset: int = 0
    file_size: int = 0
    mtime: float = 0.0
    captured_at: str = ""
    mode: str = "offset"
    archive_path: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "byte_offset": self.byte_offset,
            "file_size": self.file_size,
            "mtime": self.mtime,
            "captured_at": self.captured_at or _now(),
            "mode": self.mode,
            "archive_path": self.archive_path,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ConnectLogBaseline | None:
        if not isinstance(data, dict) or not data.get("path"):
            return None
        return cls(
            path=str(data["path"]),
            byte_offset=int(data.get("byte_offset") or 0),
            file_size=int(data.get("file_size") or 0),
            mtime=float(data.get("mtime") or 0.0),
            captured_at=str(data.get("captured_at") or ""),
            mode=str(data.get("mode") or "offset"),
            archive_path=str(data["archive_path"]) if data.get("archive_path") else None,
        )


@dataclass
class ConnectLogHarvestResult:
    events: list[ConnectLogEvent] = field(default_factory=list)
    correlations: list[dict[str, Any]] = field(default_factory=list)
    groups: dict[str, list[str]] = field(default_factory=dict)
    waited_sec: float = 0.0
    polls: int = 0
    status: str = "pending"
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "waited_sec": round(self.waited_sec, 1),
            "polls": self.polls,
            "event_count": len(self.events),
            "correlation_count": len(self.correlations),
            "groups": self.groups,
            "correlations": self.correlations,
            "error": self.error,
            "harvested_at": _now(),
        }


def _resolve_log_path(log_dir: Path | str | None = None) -> Path:
    root = Path(log_dir or DEFAULT_LOG_DIR).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    latest = latest_connect_log_file(root)
    if latest and latest.is_file():
        return latest
    stamp = datetime.now().strftime("%Y%m%d")
    return root / f"file-{stamp}.log"


def prepare_connect_log_baseline(
    *,
    log_dir: Path | str | None = None,
    mode: str | None = None,
    tag: str = "",
) -> ConnectLogBaseline:
    """Mark or clear the Connect log before a UI trigger run."""
    path = _resolve_log_path(log_dir)
    if not path.is_file():
        path.touch()
    prepare = (mode or connect_log_prepare_mode()).strip().lower()
    archive_path: str | None = None
    if prepare == "truncate" and path.stat().st_size > 0:
        suffix = f".pre-{tag or 'audit'}-{datetime.now().strftime('%H%M%S')}.log"
        archive = path.with_name(path.name + suffix)
        shutil.copy2(path, archive)
        archive_path = str(archive)
        with path.open("w", encoding="utf-8"):
            pass
        byte_offset = 0
    else:
        stat = path.stat()
        byte_offset = stat.st_size
    stat = path.stat()
    return ConnectLogBaseline(
        path=str(path),
        byte_offset=byte_offset,
        file_size=stat.st_size,
        mtime=stat.st_mtime,
        captured_at=_now(),
        mode="truncate" if prepare == "truncate" else "offset",
        archive_path=archive_path,
    )


def parse_events_since_baseline(
    baseline: ConnectLogBaseline,
    *,
    operations: set[str] | None = None,
) -> list[ConnectLogEvent]:
    """Parse ingress audit events appended after the baseline offset."""
    path = Path(baseline.path).expanduser()
    if not path.is_file():
        return []
    size = path.stat().st_size
    start = max(0, int(baseline.byte_offset))
    if size <= start:
        return []
    with path.open("rb") as fh:
        fh.seek(start)
        chunk = fh.read(size - start)
    if not chunk:
        return []
    text = chunk.decode("utf-8", errors="replace")
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False, encoding="utf-8") as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)
    try:
        return parse_connect_service_log(tmp_path, operations=operations)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _latest_event_for_operation(events: list[ConnectLogEvent], operation: str) -> ConnectLogEvent | None:
    matches = [e for e in events if e.operation == operation]
    if not matches:
        return None

    def _sort_key(ev: ConnectLogEvent) -> str:
        return ev.occurred_at or ev.log_timestamp or ""

    return sorted(matches, key=_sort_key)[-1]


def correlations_for_selection(
    events: list[ConnectLogEvent],
    selection: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Map harvested log events to selection rows (latest cid per operation)."""
    groups = group_by_operation(events)
    out: list[dict[str, Any]] = []
    seen_cids: set[str] = set()
    for item in selection:
        if not isinstance(item, dict):
            continue
        op = str(item.get("operation") or "").strip()
        if not op:
            continue
        touch = str(item.get("touchpoint") or "").strip()
        ev = _latest_event_for_operation(events, op)
        cid = ev.x_correlation_id if ev else ""
        if not cid:
            group = groups.get(op)
            if group and group.correlation_ids:
                cid = group.correlation_ids[-1]
        if not cid or cid.lower() in seen_cids:
            continue
        seen_cids.add(cid.lower())
        out.append(
            {
                "correlation_id": cid,
                "operation": op,
                "touchpoint": touch,
                "source": "connect_service_log",
                "recorded_at": _now(),
                "log_line": ev.log_line if ev else None,
                "occurred_at": ev.occurred_at if ev else "",
            }
        )
    return out


def wait_for_connect_log_events(
    baseline: ConnectLogBaseline,
    selection: list[dict[str, Any]],
    *,
    timeout_sec: int | None = None,
    poll_sec: int | None = None,
    on_progress: ProgressFn | None = None,
) -> ConnectLogHarvestResult:
    """Poll Connect log until expected operations appear or timeout."""
    expected_ops = expected_operations_from_selection(selection)
    op_filter = set(expected_ops) if expected_ops else None
    timeout = timeout_sec if timeout_sec is not None else connect_log_settle_sec()
    interval = poll_sec if poll_sec is not None else connect_log_poll_sec()
    started = time.monotonic()
    deadline = started + timeout
    result = ConnectLogHarvestResult()
    last_sizes: list[int] = []
    polls = 0

    def _log(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    _log(
        f"▸ Connect log harvest started · expected={expected_ops or 'any'} · "
        f"timeout={timeout}s · poll={interval}s"
    )

    while time.monotonic() < deadline:
        polls += 1
        events = parse_events_since_baseline(baseline, operations=op_filter)
        groups = group_by_operation(events)
        found_ops = {op for op in expected_ops if op in groups and groups[op].correlation_ids}
        size = Path(baseline.path).stat().st_size if Path(baseline.path).is_file() else 0
        last_sizes.append(size)
        if len(last_sizes) > 4:
            last_sizes = last_sizes[-4:]
        stable = len(last_sizes) >= 2 and last_sizes[-1] == last_sizes[-2]

        if expected_ops and len(found_ops) >= len(expected_ops):
            result.events = events
            result.groups = {op: list(g.correlation_ids) for op, g in groups.items()}
            result.correlations = correlations_for_selection(events, selection)
            result.waited_sec = time.monotonic() - started
            result.polls = polls
            result.status = "complete"
            _log(
                f"✓ Connect log harvest complete · {len(result.correlations)} cid(s) · "
                f"{result.waited_sec:.0f}s · polls={polls}"
            )
            return result

        if events and stable and not expected_ops:
            result.events = events
            result.groups = {op: list(g.correlation_ids) for op, g in groups.items()}
            result.correlations = correlations_for_selection(events, selection)
            result.waited_sec = time.monotonic() - started
            result.polls = polls
            result.status = "complete"
            _log(f"✓ Connect log harvest complete (no op filter) · events={len(events)}")
            return result

        if events and stable and expected_ops and found_ops:
            missing = [op for op in expected_ops if op not in found_ops]
            result.events = events
            result.groups = {op: list(g.correlation_ids) for op, g in groups.items()}
            result.correlations = correlations_for_selection(events, selection)
            result.waited_sec = time.monotonic() - started
            result.polls = polls
            result.status = "partial"
            _log(
                f"⚠ Connect log harvest partial · found={sorted(found_ops)} · "
                f"missing={missing} · file stable — stopping early"
            )
            return result

        if polls == 1 or polls % 4 == 0:
            _log(
                f"… waiting for Connect log · found={len(events)} event(s) · "
                f"ops={sorted(found_ops) if found_ops else '—'} · "
                f"elapsed={time.monotonic() - started:.0f}s"
            )
        time.sleep(interval)

    events = parse_events_since_baseline(baseline, operations=op_filter)
    groups = group_by_operation(events)
    result.events = events
    result.groups = {op: list(g.correlation_ids) for op, g in groups.items()}
    result.correlations = correlations_for_selection(events, selection)
    result.waited_sec = time.monotonic() - started
    result.polls = polls
    if result.correlations:
        result.status = "partial"
        _log(
            f"⚠ Connect log harvest timed out after {timeout}s — "
            f"captured {len(result.correlations)} cid(s) anyway"
        )
    else:
        result.status = "timeout"
        _log(f"✖ Connect log harvest timed out after {timeout}s — no xCorrelationId found")
    return result


def schedule_connect_log_harvest(project_root: Path, job_id: str) -> bool:
    """Background poll + merge harvested cids into a UI trigger job."""
    with _HARVEST_LOCK:
        if job_id in _HARVEST_SCHEDULED:
            return False
        _HARVEST_SCHEDULED.add(job_id)

    def _worker() -> None:
        try:
            from audit_validator.ui_trigger import (
                apply_extracted_results,
                finalize_ui_trigger_verification,
                get_ui_trigger_job,
                _append_log,
                _write_job,
            )

            job = get_ui_trigger_job(project_root, job_id)
            if not job:
                return
            cap = dict(job.get("connect_log_capture") or {})
            baseline = ConnectLogBaseline.from_dict(cap)
            if not baseline:
                return
            selection = [s for s in (job.get("selection") or []) if isinstance(s, dict)]

            def _progress(msg: str) -> None:
                j = get_ui_trigger_job(project_root, job_id)
                if not j:
                    return
                _append_log(j, msg)
                _write_job(project_root, j)

            harvest = wait_for_connect_log_events(
                baseline,
                selection,
                on_progress=_progress,
            )
            job = get_ui_trigger_job(project_root, job_id) or job
            cap = dict(job.get("connect_log_capture") or {})
            cap["harvest"] = harvest.as_dict()
            cap["status"] = harvest.status
            job["connect_log_capture"] = cap

            if harvest.correlations:
                job = apply_extracted_results(project_root, job, harvest.correlations)
                _append_log(
                    job,
                    f"✓ Connect log → {len(harvest.correlations)} xCorrelationId(s) registered for verify",
                )
                job["verification"] = {
                    **(job.get("verification") or {}),
                    "ready": True,
                    "auto_verify_pending": True,
                    "connect_log_harvest": harvest.status,
                }
                job = _write_job(project_root, job)
                if (job.get("verification") or {}).get("auto_verify", True):
                    finalize_ui_trigger_verification(project_root, job_id)
            else:
                _append_log(
                    job,
                    "⚠ Connect log harvest found no xCorrelationId — paste manually or re-run",
                )
                job["verification"] = {
                    **(job.get("verification") or {}),
                    "ready": False,
                    "connect_log_harvest": harvest.status,
                }
                _write_job(project_root, job)
        finally:
            with _HARVEST_LOCK:
                _HARVEST_SCHEDULED.discard(job_id)

    threading.Thread(target=_worker, daemon=True, name=f"connect-log-{job_id[:8]}").start()
    return True
