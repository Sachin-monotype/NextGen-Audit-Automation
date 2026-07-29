"""Parse Monotype Connect ConnectService logs for ingress audit events.

Connect logs emit ``[CurlDebug] curl -X POST …/audit-events`` lines with a JSON
array payload. Each event carries ``xCorrelationId`` in the **body** (pair raw ↔
enriched in Mongo). The HTTP header ``x-correlation-id`` is a different trace id.

Usage (CLI): ``local/scripts/parse_connect_service_log.py <log-file>``
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

# Log line starts with MM-DD HH:MM:SS (continuation lines have no timestamp).
_LOG_LINE_RE = re.compile(r"^\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.")

# Loose event extractor when JSON parse fails on truncated curl payloads.
_EVENT_SNIPPET_RE = re.compile(
    r'"xCorrelationId"\s*:\s*"(?P<cid>[0-9a-fA-F-]{36})"'
    r'.*?"operation"\s*:\s*"(?P<op>[^"]+)"'
    r'.*?"occurredAt"\s*:\s*"(?P<at>[^"]+)"',
    re.DOTALL,
)

DEFAULT_LOG_DIR = Path.home() / "Library/Logs/Monotype/Monotype Connect/ConnectService/service"


@dataclass
class ConnectLogEvent:
    x_correlation_id: str
    operation: str
    occurred_at: str = ""
    event_id: str = ""
    service: str = ""
    operation_state: str = ""
    log_line: int = 0
    log_timestamp: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "xCorrelationId": self.x_correlation_id,
            "operation": self.operation,
            "occurredAt": self.occurred_at,
            "eventId": self.event_id,
            "service": self.service,
            "operationState": self.operation_state,
            "log_line": self.log_line,
            "log_timestamp": self.log_timestamp,
        }


@dataclass
class OperationCorrelationGroup:
    operation: str
    correlation_ids: list[str] = field(default_factory=list)
    events: list[ConnectLogEvent] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "unique_count": len(self.correlation_ids),
            "correlation_ids": list(self.correlation_ids),
            "events": [e.as_dict() for e in self.events],
        }


def _log_timestamp(line: str) -> str:
    m = re.match(r"^(\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)", line)
    return m.group(1) if m else ""


def _iter_log_records(path: Path) -> Iterator[tuple[int, str]]:
    """Yield (line_number, record_text) merging continuation lines."""
    buf = ""
    start_line = 0
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line_no, line in enumerate(fh, 1):
            if _LOG_LINE_RE.match(line):
                if buf:
                    yield start_line, buf
                buf = line.rstrip("\n")
                start_line = line_no
            else:
                buf += line.rstrip("\n")
        if buf:
            yield start_line, buf


def _extract_json_array_after_d(record: str) -> str | None:
    """Pull the JSON array from a curl ``-d '…'`` fragment."""
    for marker in (" -d '", ' -d "'):
        idx = record.find(marker)
        if idx < 0:
            continue
        start = idx + len(marker)
        # Prefer last ']' in record (payload may be only part of a multi-line curl).
        end = record.rfind("]")
        if end > start:
            return record[start : end + 1]
    # Continuation fragment: starts mid-array/object
    if '"xCorrelationId"' in record:
        bracket = record.find("[")
        end = record.rfind("]")
        if bracket >= 0 and end > bracket:
            return record[bracket : end + 1]
        if record.lstrip().startswith("{"):
            return "[" + record + "]"
    return None


def _events_from_json_array(raw: str, *, line_no: int, log_ts: str) -> list[ConnectLogEvent]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return _events_from_regex(raw, line_no=line_no, log_ts=log_ts)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    out: list[ConnectLogEvent] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        src = item.get("source") if isinstance(item.get("source"), dict) else {}
        cid = str(item.get("xCorrelationId") or "").strip()
        op = str(src.get("operation") or "").strip()
        if not cid or not op:
            continue
        out.append(
            ConnectLogEvent(
                x_correlation_id=cid,
                operation=op,
                occurred_at=str(item.get("occurredAt") or ""),
                event_id=str(item.get("eventId") or ""),
                service=str(src.get("service") or ""),
                operation_state=str(src.get("operationState") or ""),
                log_line=line_no,
                log_timestamp=log_ts,
            )
        )
    return out


def _events_from_regex(text: str, *, line_no: int, log_ts: str) -> list[ConnectLogEvent]:
    out: list[ConnectLogEvent] = []
    for m in _EVENT_SNIPPET_RE.finditer(text):
        out.append(
            ConnectLogEvent(
                x_correlation_id=m.group("cid"),
                operation=m.group("op"),
                occurred_at=m.group("at"),
                log_line=line_no,
                log_timestamp=log_ts,
            )
        )
    return out


def parse_connect_service_log(
    path: Path | str,
    *,
    operations: set[str] | None = None,
    since: str | None = None,
) -> list[ConnectLogEvent]:
    """Parse a ConnectService log file and return all ingress audit events found."""
    path = Path(path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(path)
    since_dt: datetime | None = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            since_dt = None

    events: list[ConnectLogEvent] = []
    for line_no, record in _iter_log_records(path):
        if "audit-events" not in record and '"xCorrelationId"' not in record:
            continue
        if "[CurlDebug]" not in record and '"xCorrelationId"' not in record:
            continue
        log_ts = _log_timestamp(record)
        raw = _extract_json_array_after_d(record)
        found: list[ConnectLogEvent] = []
        if raw:
            found = _events_from_json_array(raw, line_no=line_no, log_ts=log_ts)
        if not found and '"xCorrelationId"' in record:
            found = _events_from_regex(record, line_no=line_no, log_ts=log_ts)
        for ev in found:
            if operations and ev.operation not in operations:
                continue
            if since_dt and ev.occurred_at:
                try:
                    at = datetime.fromisoformat(ev.occurred_at.replace("Z", "+00:00"))
                    if at < since_dt:
                        continue
                except ValueError:
                    pass
            events.append(ev)
    return events


def group_by_operation(events: list[ConnectLogEvent]) -> dict[str, OperationCorrelationGroup]:
    """Group events by operation; preserve unique cids in first-seen order."""
    groups: dict[str, OperationCorrelationGroup] = {}
    seen: dict[str, set[str]] = defaultdict(set)
    for ev in events:
        g = groups.get(ev.operation)
        if not g:
            g = OperationCorrelationGroup(operation=ev.operation)
            groups[ev.operation] = g
        g.events.append(ev)
        key = ev.x_correlation_id.lower()
        if key not in seen[ev.operation]:
            seen[ev.operation].add(key)
            g.correlation_ids.append(ev.x_correlation_id)
    return groups


def summarize_connect_log(
    path: Path | str,
    *,
    operations: set[str] | None = None,
    since: str | None = None,
) -> dict[str, Any]:
    """Summary: unique xCorrelationId per operation from a Connect service log."""
    events = parse_connect_service_log(path, operations=operations, since=since)
    groups = group_by_operation(events)
    return {
        "log_file": str(Path(path).expanduser()),
        "total_events": len(events),
        "operation_count": len(groups),
        "operations": {
            op: g.as_dict() for op, g in sorted(groups.items(), key=lambda x: x[0].lower())
        },
    }


def verify_correlations_in_mongo(
    groups: dict[str, OperationCorrelationGroup],
    db: Any = None,
) -> dict[str, Any]:
    """Check each unique xCorrelationId against Mongo raw + enriched."""
    from audit_validator.ingress.mongo_lookup import lookup_pair_by_correlation

    out: dict[str, Any] = {}
    for op, group in sorted(groups.items(), key=lambda x: x[0].lower()):
        rows: list[dict[str, Any]] = []
        for cid in group.correlation_ids:
            raw_doc = None
            enr_doc = None
            try:
                if db is not None:
                    raw_doc, enr_doc = db.latest_pair(op, require_pair=False, correlation_id=cid)
                else:
                    raw_doc, enr_doc = lookup_pair_by_correlation(op, cid, db=db)
            except Exception as exc:  # noqa: BLE001
                rows.append(
                    {
                        "xCorrelationId": cid,
                        "raw": False,
                        "enriched": False,
                        "status": "ERROR",
                        "error": str(exc),
                    }
                )
                continue
            raw_ok = bool(raw_doc)
            enr_ok = bool(enr_doc)
            status = "PASS" if raw_ok and enr_ok else ("PARTIAL" if raw_ok or enr_ok else "MISSING")
            rows.append(
                {
                    "xCorrelationId": cid,
                    "raw": raw_ok,
                    "enriched": enr_ok,
                    "status": status,
                }
            )
        out[op] = {
            "operation": op,
            "checked": len(rows),
            "pass": sum(1 for r in rows if r["status"] == "PASS"),
            "correlations": rows,
        }
    return out


def latest_connect_log_file(log_dir: Path | str | None = None) -> Path | None:
    """Newest ``file-*.log`` under the Connect service log directory."""
    root = Path(log_dir or DEFAULT_LOG_DIR).expanduser()
    if not root.is_dir():
        return None
    files = sorted(root.glob("file-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None
