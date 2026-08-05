"""Orchestrate desktop UI triggers, log extraction, and validation."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..project_root import find_project_root
from ..report_paths import desktop_ui_results_json, desktop_ui_validation_xlsx
from .config import default_log_dir
from .excel_report import write_desktop_validation_xlsx
from .navigation import DesktopEvent, load_desktop_events
from .ui_runner import UiRunResult, run_desktop_ui_steps
from .validator import DesktopValidationReport, capture_baseline_offsets, validate_desktop_events

log = logging.getLogger(__name__)


@dataclass
class DesktopRunResult:
    ui: UiRunResult
    validation: DesktopValidationReport
    json_path: Path | None = None
    xlsx_path: Path | None = None


def run_desktop_ui_automation(
    *,
    project_root: Path | None = None,
    log_dir: Path | None = None,
    operations: set[str] | None = None,
    validate_only: bool = False,
    connect_only: bool = False,
    include_manual: bool = False,
    today_only: bool = True,
    settle_sec: float = 2.0,
    post_settle_sec: float = 5.0,
    db: Any = None,
    progress: Callable[[str], None] | None = None,
) -> DesktopRunResult:
    root = project_root or find_project_root()
    logs = log_dir or default_log_dir()
    events = load_desktop_events(
        operations=operations,
        automatable_only=not validate_only and not include_manual,
    )

    def _progress(msg: str) -> None:
        if progress:
            progress(msg)
        else:
            log.info(msg)

    offsets = capture_baseline_offsets(logs)
    ui_result = UiRunResult()

    if validate_only:
        _progress("Validate-only mode — parsing today's ConnectService logs")
    else:
        _progress(f"Running desktop UI automation for {len(events)} event(s)")
        ui_result = run_desktop_ui_steps(
            events,
            connect_only=connect_only,
            settle_sec=settle_sec,
            progress=progress,
        )

    validation = validate_desktop_events(
        events,
        logs,
        triggered_operations=set(ui_result.triggered_operations),
        start_offsets=offsets,
        today_only=today_only,
        db=db,
        wait_sec=post_settle_sec if not validate_only else 0.0,
    )

    json_path = desktop_ui_results_json(root)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ui": {
            "triggered_operations": ui_result.triggered_operations,
            "errors": ui_result.errors,
            "steps": [
                {
                    "operation": s.event_operation,
                    "description": s.step_description,
                    "status": s.status,
                    "error": s.error,
                }
                for s in ui_result.step_results
            ],
        },
        "validation": {
            "checked_at": validation.checked_at,
            "log_dir": validation.log_dir,
            "today_only": validation.today_only,
            "summary": {
                "pass": validation.summary.pass_count,
                "fail": validation.summary.fail_count,
                "skip": validation.summary.skip_count,
                "manual": validation.summary.manual_count,
            },
            "events": [
                {
                    "event_name": e.event_name,
                    "operation": e.operation,
                    "validation_status": e.validation_status,
                    "xCorrelationId": e.x_correlation_id,
                    "log_file": e.log_file,
                    "ui_status": e.ui_status,
                    "log_status": e.log_status,
                }
                for e in validation.events
            ],
        },
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    xlsx_path = write_desktop_validation_xlsx(validation, desktop_ui_validation_xlsx(root))

    _progress(
        f"Done — PASS={validation.summary.pass_count} FAIL={validation.summary.fail_count} "
        f"→ {xlsx_path}"
    )

    return DesktopRunResult(
        ui=ui_result,
        validation=validation,
        json_path=json_path,
        xlsx_path=xlsx_path,
    )
