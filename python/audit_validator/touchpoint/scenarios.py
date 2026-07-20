"""Touchpoint scenario catalog for Generate.

Composite selection ids: ``{operation}::{touchpoint}``
Example: ``activateFamily::List (FONTLIST)``

Display / export names use a short touchpoint suffix, e.g.
``activateFamily(global)``, ``activateFamily(favourite)``, ``activateFamily(list)``.

Source of truth for step sequences: ``FLOW_DEFS`` in payloads.py
(aligned with UI Navigation sheet + mtconnect-ui list scopes).
"""

from __future__ import annotations

from typing import Any

from audit_validator.touchpoint.assertions import normalize_touchpoint
from audit_validator.touchpoint.modules import module_for
from audit_validator.touchpoint.payloads import FLOW_DEFS

SCENARIO_SEP = "::"

# Canonical FLOW_DEFS key for the Discovery/Browse (global) path
_GLOBAL_TOUCH = "Discovery/Browse (global)"

# Sheet / UI aliases that are the same path as Discovery/Browse (global)
_GLOBAL_ALIASES = frozenset(
    {
        "search/ family / discovery",
        "search / family / discovery",
        "search/family/discovery",
        "discovery/browse (global)",
        "discovery",
        "global",
    }
)

_SHORT_TOUCH = {
    "discovery": "global",
    "favourite": "favourite",
    "list": "list",
    "project": "project",
    "project_list": "project_list",
}


def scenario_id(operation: str, touchpoint: str) -> str:
    return f"{operation}{SCENARIO_SEP}{touchpoint}"


def short_touchpoint(touch: str | None) -> str:
    """Compact touchpoint label for UI / CSV (global, favourite, list, …)."""
    if not touch:
        return ""
    kind = normalize_touchpoint(touch)
    return _SHORT_TOUCH.get(kind, kind.replace(" ", "_") or "global")


def scenario_display_name(
    operation: str,
    touchpoint: str | None = None,
    *,
    ui: bool = False,
) -> str:
    """e.g. ``activateFamily(global)`` or ``activateFamily(global)(ui)`` for UI triggers."""
    short = short_touchpoint(touchpoint)
    base = f"{operation}({short})" if short else operation
    if ui and not base.endswith("(ui)"):
        return f"{base}(ui)"
    return base


def is_placeholder_scenario(operation: str | None, touchpoint: str | None = None) -> bool:
    """True when CasePilot left angle-bracket template tokens (e.g. ``<op>``, ``<touch>``)."""
    for part in (operation, touchpoint):
        if not part:
            continue
        s = str(part).strip()
        if not s:
            continue
        if "<" in s or ">" in s:
            return True
        if s.lower() in {"op", "touch", "operation", "touchpoint", "uuid", "value"}:
            return True
    return False


def canonicalize_touchpoint(touch: str | None) -> str | None:
    """Collapse Search/Family/Discovery → Discovery/Browse (global)."""
    if not touch:
        return touch
    key = " ".join(touch.lower().replace("/", " ").split())
    compact = touch.strip().lower()
    if compact in _GLOBAL_ALIASES or key in {
        "search family discovery",
        "discovery browse global",
        "global",
        "discovery",
    }:
        return _GLOBAL_TOUCH
    if normalize_touchpoint(touch) == "discovery":
        return _GLOBAL_TOUCH
    return touch


def parse_selection_id(item_id: str) -> tuple[str, str | None]:
    """Return (operation, touchpoint|None). Ingress/cron ids pass through untouched."""
    raw = (item_id or "").strip()
    if not raw or raw.startswith("ingress:") or raw.startswith("cron:"):
        return raw, None
    if SCENARIO_SEP in raw:
        op, touch = raw.split(SCENARIO_SEP, 1)
        return op.strip(), canonicalize_touchpoint(touch.strip() or None)
    return raw, None


def _scenario_dict(op: str, touch: str, steps: list[str]) -> dict[str, Any]:
    return {
        "id": scenario_id(op, touch),
        "operation": op,
        "touchpoint": touch,
        "steps": list(steps),
        "module": module_for(op),
        "kind": "graphql",
        "label": scenario_display_name(op, touch),
        "short_touchpoint": short_touchpoint(touch),
    }


def list_scenarios() -> list[dict[str, Any]]:
    """Flat catalog of GraphQL touchpoint scenarios for the Generate UI."""
    out: list[dict[str, Any]] = []
    for op, touches in FLOW_DEFS.items():
        for touch, steps in touches.items():
            # Skip any leftover Search alias if present in FLOW_DEFS
            if canonicalize_touchpoint(touch) == _GLOBAL_TOUCH and touch != _GLOBAL_TOUCH:
                continue
            out.append(_scenario_dict(op, touch, list(steps)))
    out.sort(key=lambda r: (r["operation"].lower(), r["touchpoint"].lower()))
    return out


def scenarios_for_operation(operation: str) -> list[dict[str, Any]]:
    return [s for s in list_scenarios() if s["operation"] == operation]


def expand_selection_to_scenarios(selection: list[str]) -> list[dict[str, Any]]:
    """Map UI selection ids → scenario dicts.

    - ``activateFamily::List (FONTLIST)`` → that one scenario
    - ``activateFamily::Search/ Family / Discovery`` → Discovery/Browse (global)
    - ``activateFamily`` (bare) → all touchpoints for that op
    - unknown ops with no FLOW_DEFS → single synthetic Discovery scenario
    """
    catalog = {s["id"]: s for s in list_scenarios()}
    by_op: dict[str, list[dict[str, Any]]] = {}
    for s in catalog.values():
        by_op.setdefault(s["operation"], []).append(s)

    chosen: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in selection:
        op, touch = parse_selection_id(item)
        if not op or op.startswith("ingress:") or op.startswith("cron:"):
            continue
        if touch:
            touch = canonicalize_touchpoint(touch) or touch
            sid = scenario_id(op, touch)
            if sid in catalog and sid not in seen:
                chosen.append(catalog[sid])
                seen.add(sid)
            elif sid not in seen:
                chosen.append(_scenario_dict(op, touch, [op]))
                seen.add(sid)
            continue
        # Bare operation → all known touchpoints, or Discovery-only fallback
        if op in by_op:
            for s in by_op[op]:
                if s["id"] not in seen:
                    chosen.append(s)
                    seen.add(s["id"])
        else:
            sid = scenario_id(op, _GLOBAL_TOUCH)
            if sid not in seen:
                chosen.append(_scenario_dict(op, _GLOBAL_TOUCH, [op]))
                seen.add(sid)
    return chosen
