"""Validate desktop UI triggers against ConnectService logs (today only)."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .log_extractor import (
    IngressLogEvent,
    extract_ingress_events_from_logs,
    latest_for_operation,
    snapshot_log_offsets,
)
from .navigation import DesktopEvent


@dataclass
class EventValidation:
    event_name: str
    operation: str
    category: str
    navigation: str
    trigger_hint: str
    ui_status: str
    log_status: str
    validation_status: str
    x_correlation_id: str = ""
    occurred_at: str = ""
    log_file: str = ""
    remarks: str = ""
    raw_payload: dict[str, Any] | None = None
    mongo_raw: bool = False
    mongo_enriched: bool = False


@dataclass
class ValidationSummary:
    pass_count: int = 0
    fail_count: int = 0
    skip_count: int = 0
    manual_count: int = 0


@dataclass
class DesktopValidationReport:
    checked_at: str
    log_dir: str
    today_only: bool
    events: list[EventValidation] = field(default_factory=list)
    summary: ValidationSummary = field(default_factory=ValidationSummary)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def wait_for_log_event(
    log_dir: Path,
    operation: str,
    *,
    start_offsets: dict[str, int],
    timeout_sec: float = 30.0,
    poll_sec: float = 1.5,
    today_only: bool = True,
) -> IngressLogEvent | None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        events = extract_ingress_events_from_logs(
            log_dir,
            operations={operation},
            today_only=today_only,
            start_offsets=start_offsets,
        )
        hit = latest_for_operation(events, operation)
        if hit:
            return hit
        time.sleep(poll_sec)
    return None


def validate_desktop_events(
    events: list[DesktopEvent],
    log_dir: Path,
    *,
    triggered_operations: set[str] | None = None,
    start_offsets: dict[str, int] | None = None,
    today_only: bool = True,
    db: Any = None,
    wait_sec: float = 0.0,
) -> DesktopValidationReport:
    """Match UI-triggered operations to CurlDebug log entries from today."""
    if wait_sec > 0:
        time.sleep(wait_sec)

    log_events = extract_ingress_events_from_logs(
        log_dir,
        today_only=today_only,
        start_offsets=start_offsets,
    )
    by_op: dict[str, IngressLogEvent] = {}
    for ev in log_events:
        by_op[ev.operation] = ev

    triggered = triggered_operations or set()
    rows: list[EventValidation] = []
    summary = ValidationSummary()

    for event in events:
        nav = " > ".join(event.navigation) if event.navigation else event.trigger_hint
        ui_ok = event.operation in triggered if triggered else None
        log_hit = by_op.get(event.operation)

        if not event.automatable:
            ui_status = "MANUAL"
            summary.manual_count += 1
        elif ui_ok is True:
            ui_status = "TRIGGERED"
        elif ui_ok is False:
            ui_status = "NOT_RUN"
        else:
            ui_status = "N/A"

        if log_hit:
            log_status = "FOUND"
            cid = log_hit.x_correlation_id
            mongo_raw = mongo_enriched = False
            if db is not None and cid:
                try:
                    raw_doc, enr_doc = db.latest_pair(
                        event.operation, require_pair=False, correlation_id=cid
                    )
                    mongo_raw = bool(raw_doc)
                    mongo_enriched = bool(enr_doc)
                except Exception:  # noqa: BLE001
                    pass
            if mongo_raw and mongo_enriched:
                val_status = "PASS"
                summary.pass_count += 1
            elif cid:
                val_status = "PASS" if not db else "PARTIAL"
                if val_status == "PASS":
                    summary.pass_count += 1
                else:
                    summary.fail_count += 1
            else:
                val_status = "FAIL"
                summary.fail_count += 1
            rows.append(
                EventValidation(
                    event_name=event.event_name,
                    operation=event.operation,
                    category=event.category,
                    navigation=nav,
                    trigger_hint=event.trigger_hint,
                    ui_status=ui_status,
                    log_status=log_status,
                    validation_status=val_status,
                    x_correlation_id=cid,
                    occurred_at=log_hit.occurred_at,
                    log_file=log_hit.log_file,
                    remarks=event.remarks,
                    raw_payload=log_hit.payload,
                    mongo_raw=mongo_raw,
                    mongo_enriched=mongo_enriched,
                )
            )
        else:
            if not event.automatable:
                val_status = "SKIP"
                summary.skip_count += 1
            else:
                val_status = "FAIL"
                summary.fail_count += 1
            rows.append(
                EventValidation(
                    event_name=event.event_name,
                    operation=event.operation,
                    category=event.category,
                    navigation=nav,
                    trigger_hint=event.trigger_hint,
                    ui_status=ui_status,
                    log_status="MISSING",
                    validation_status=val_status,
                    remarks=event.remarks or "No CurlDebug entry in today's logs",
                )
            )

    return DesktopValidationReport(
        checked_at=_now_iso(),
        log_dir=str(log_dir),
        today_only=today_only,
        events=rows,
        summary=summary,
    )


def capture_baseline_offsets(log_dir: Path) -> dict[str, int]:
    return snapshot_log_offsets(log_dir)
