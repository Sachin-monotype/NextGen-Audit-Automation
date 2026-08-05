"""Extract ingress audit events from Monotype Connect ConnectService logs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

from .config import TARGET_URL, today_local

_CURL_DEBUG = "[CurlDebug]"
_JSON_ARRAY_RE = re.compile(r"(\[\s*\{[\s\S]*?\}\s*\])")
_ISO_LINE_PREFIX = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)"
)


@dataclass(frozen=True)
class IngressLogEvent:
    operation: str
    x_correlation_id: str
    event_name: str
    log_file: str
    line_no: int
    occurred_at: str
    raw_curl: str
    payload: dict[str, Any] = field(repr=False)


def _parse_line_timestamp(line: str) -> datetime | None:
    m = _ISO_LINE_PREFIX.match(line.strip())
    if not m:
        return None
    ts = m.group("ts").replace(" ", "T")
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _line_is_today(line: str, *, today: date) -> bool:
    ts = _parse_line_timestamp(line)
    if ts is None:
        return True
    return ts.date() == today


def _extract_payload_from_curl(curl: str) -> dict[str, Any] | None:
    m = _JSON_ARRAY_RE.search(curl)
    if not m:
        return None
    try:
        arr = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    if isinstance(arr, list) and arr and isinstance(arr[0], dict):
        return arr[0]
    if isinstance(arr, dict):
        return arr
    return None


def _operation_from_payload(payload: dict[str, Any]) -> str:
    src = payload.get("source") or {}
    if isinstance(src, dict):
        return str(src.get("operation") or "").strip()
    return ""


def iter_log_files(log_dir: Path, *, today_only: bool = True) -> list[Path]:
    if not log_dir.is_dir():
        return []
    files = [f for f in log_dir.iterdir() if f.is_file()]
    if today_only:
        today = today_local()
        files = [f for f in files if datetime.fromtimestamp(f.stat().st_mtime).date() == today]
    files.sort(key=lambda f: f.stat().st_mtime)
    return files


def iter_curl_debug_lines(
    log_file: Path,
    *,
    today_only: bool = True,
    start_offset: int = 0,
) -> Iterator[tuple[int, str]]:
    today = today_local()
    with log_file.open("r", encoding="utf-8", errors="ignore") as fh:
        if start_offset:
            fh.seek(start_offset)
        for line_no, line in enumerate(fh, start=1):
            if _CURL_DEBUG not in line:
                continue
            if TARGET_URL not in line:
                continue
            if today_only and not _line_is_today(line, today=today):
                continue
            curl = line.split(_CURL_DEBUG, 1)[1].strip()
            yield line_no, curl


def extract_ingress_events_from_logs(
    log_dir: Path,
    *,
    operations: set[str] | None = None,
    today_only: bool = True,
    latest_file_only: bool = False,
    start_offsets: dict[str, int] | None = None,
) -> list[IngressLogEvent]:
    """Parse ConnectService logs for ingress curl debug entries."""
    start_offsets = start_offsets or {}
    log_files = iter_log_files(log_dir, today_only=today_only)
    if latest_file_only and log_files:
        log_files = [log_files[-1]]
    if not log_files:
        return []

    found: list[IngressLogEvent] = []
    seen: set[tuple[str, str]] = set()

    for log_file in log_files:
        offset = start_offsets.get(log_file.name, 0)
        for line_no, curl in iter_curl_debug_lines(
            log_file, today_only=today_only, start_offset=offset
        ):
            payload = _extract_payload_from_curl(curl)
            if not payload:
                continue
            op = _operation_from_payload(payload)
            cid = str(payload.get("xCorrelationId") or "").strip()
            if not op or not cid:
                continue
            if operations and op not in operations:
                continue
            key = (op, cid)
            if key in seen:
                continue
            seen.add(key)
            ts = str(payload.get("occurredAt") or "")
            found.append(
                IngressLogEvent(
                    operation=op,
                    x_correlation_id=cid,
                    event_name=op,
                    log_file=log_file.name,
                    line_no=line_no,
                    occurred_at=ts,
                    raw_curl=curl,
                    payload=payload,
                )
            )
    return found


def snapshot_log_offsets(log_dir: Path) -> dict[str, int]:
    """Byte offsets for each log file — read only new lines after UI triggers."""
    out: dict[str, int] = {}
    if not log_dir.is_dir():
        return out
    for path in log_dir.iterdir():
        if path.is_file():
            try:
                out[path.name] = path.stat().st_size
            except OSError:
                continue
    return out


def filter_operation(events: list[IngressLogEvent], operation: str) -> list[IngressLogEvent]:
    op = operation.strip()
    return [e for e in events if e.operation == op]


def latest_for_operation(events: list[IngressLogEvent], operation: str) -> IngressLogEvent | None:
    matches = filter_operation(events, operation)
    return matches[-1] if matches else None
