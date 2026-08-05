#!/usr/bin/env python3
"""Regenerate ``desktop_navigation.json`` from ingress manifest + ui_navigation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

from audit_validator.curl_builder import load_ui_navigation  # noqa: E402

MANIFEST = ROOT / "python" / "audit_validator" / "data" / "ingress_payloads" / "manifest.json"
OUT = ROOT / "python" / "audit_validator" / "data" / "desktop_navigation.json"

# Spreadsheet trigger hints (column B) keyed by event_name
_SHEET_HINTS: dict[str, str] = {
    "app.settings.plugin.install_all.enabled": "enable plugin",
    "app.settings.plugin.install_all.disabled": "disable plugin",
    "app.settings.plugin.app.enabled": "n/a",
    "app.settings.plugin.app.[App_name]_enabled": "enable specific app",
    "app.settings.performance_mode.changed": "change mode when enabled",
    "app.settings.auto_performance.enabled": "enable performance",
    "app.settings.auto_performance.disabled": "disable performance",
    "app.settings.activation_mode.changed": "No ui idea",
    "app.logs.exported": "helpandsupport > download system log",
    "app.network.refreshed": "helpandsupport > refresh status",
    "app.language.changed": "preferences > change language",
    "app.feedback.submitted": "about > share feedback",
    "app.cache.cleared": "N/A",
    "fontSyncSuccess": "Fontbridge",
    "font.activation.type.switched": "activate family",
    "font.temp_activated": "",
    "font.localfont.activated": "plugin",
    "font.localfont.deactivated": "plugin",
    "userLoginInitiatedApp": "login",
    "userLoginFailureApp": "No ui idea",
    "userLogoutApp": "log out",
    "userSwitchworkspaceApp": "need account app",
    "identityLinked": "on successful login",
}

_MANUAL_EVENTS = frozenset(
    {
        "app.settings.plugin.app.enabled",
        "app.cache.cleared",
        "fontSyncSuccess",
        "userLoginFailureApp",
        "identityLinked",
    }
)


def _load_existing_steps() -> dict[str, list[dict]]:
    if not OUT.is_file():
        return {}
    data = json.loads(OUT.read_text(encoding="utf-8"))
    out: dict[str, list[dict]] = {}
    for row in data.get("events") or []:
        op = str(row.get("operation") or "")
        steps = row.get("steps")
        if op and isinstance(steps, list):
            out[op] = steps
    return out


def build() -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    nav = load_ui_navigation()
    preserved_steps = _load_existing_steps()
    events: list[dict] = []

    manifest_ops = {str(c.get("event_name")): c for c in manifest.get("cases") or [] if not c.get("skipped")}

    for event_name, hint in _SHEET_HINTS.items():
        row = manifest_ops.get(event_name)
        if row:
            operation = str(row.get("operation") or "")
            case_id = str(row.get("case_id") or "")
            category = str(row.get("category") or "")
        else:
            operation = {
                "fontSyncSuccess": "fontSyncSuccess",
                "font.temp_activated": "fontTempActivated",
                "userLoginInitiatedApp": "userLoginInitiatedApp",
                "app.network.refreshed": "appNetworkRefreshed",
            }.get(event_name, "")
            case_id = event_name.replace(".", "_")
            category = "login" if "login" in event_name.lower() or event_name in {"identityLinked", "userSwitchworkspaceApp", "userLogoutApp"} else "desktop_app_preference_page"

        ui = nav.get(operation) or {}
        navigation = list(ui.get("navigation") or [])
        automatable = event_name not in _MANUAL_EVENTS and bool(operation)
        events.append(
            {
                "case_id": case_id,
                "event_name": event_name,
                "operation": operation,
                "category": category,
                "navigation": navigation,
                "trigger_hint": hint,
                "automatable": automatable,
                "remarks": str(ui.get("remarks") or ""),
                "steps": preserved_steps.get(operation, []),
            }
        )

    return {
        "source": "Desktop UI Navigation spreadsheet + ingress manifest + ui_navigation.json",
        "target_url": "https://mt-audit-log-resolver-service-preprod.monotype-pp.com/v1/audit-events",
        "events": events,
    }


def main() -> int:
    data = build()
    OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    auto = sum(1 for e in data["events"] if e.get("automatable"))
    print(f"Wrote {len(data['events'])} event(s) ({auto} automatable) → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
