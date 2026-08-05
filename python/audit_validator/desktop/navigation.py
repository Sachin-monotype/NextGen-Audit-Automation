"""Desktop event navigation definitions (spreadsheet + ui_navigation + selectors)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "desktop_navigation.json"


@dataclass(frozen=True)
class UiStep:
    action: str
    selector: str
    xpath: str
    description: str
    value: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> UiStep:
        return cls(
            action=str(raw.get("action") or "click").strip().lower(),
            selector=str(raw.get("selector") or "").strip(),
            xpath=str(raw.get("xpath") or "").strip(),
            description=str(raw.get("description") or "").strip(),
            value=str(raw.get("value") or "").strip(),
        )


@dataclass(frozen=True)
class DesktopEvent:
    event_name: str
    operation: str
    category: str
    navigation: list[str]
    trigger_hint: str
    steps: list[UiStep] = field(default_factory=list)
    automatable: bool = True
    remarks: str = ""
    case_id: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DesktopEvent:
        steps = [UiStep.from_dict(s) for s in (raw.get("steps") or []) if isinstance(s, dict)]
        nav = [str(n).strip() for n in (raw.get("navigation") or []) if str(n).strip()]
        return cls(
            event_name=str(raw.get("event_name") or "").strip(),
            operation=str(raw.get("operation") or "").strip(),
            category=str(raw.get("category") or "").strip(),
            navigation=nav,
            trigger_hint=str(raw.get("trigger_hint") or raw.get("trigger") or "").strip(),
            steps=steps,
            automatable=bool(raw.get("automatable", True)),
            remarks=str(raw.get("remarks") or "").strip(),
            case_id=str(raw.get("case_id") or "").strip(),
        )


def load_desktop_events(
    path: Path | None = None,
    *,
    operations: set[str] | None = None,
    automatable_only: bool = False,
) -> list[DesktopEvent]:
    data_path = path or _DATA_FILE
    if not data_path.is_file():
        return []
    data = json.loads(data_path.read_text(encoding="utf-8"))
    rows = data.get("events") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return []
    out: list[DesktopEvent] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ev = DesktopEvent.from_dict(row)
        if not ev.operation:
            continue
        if operations and ev.operation not in operations:
            continue
        if automatable_only and not ev.automatable:
            continue
        out.append(ev)
    return out


def event_by_operation(events: list[DesktopEvent]) -> dict[str, DesktopEvent]:
    return {e.operation: e for e in events}
