"""Touchpoint scenario catalog for Generate.

Composite selection ids: ``{operation}::{touchpoint}``
Example: ``activateFamily::List (FONTLIST)``

Source of truth for step sequences: ``FLOW_DEFS`` in payloads.py
(aligned with UI Navigation sheet + mtconnect-ui list scopes).
"""

from __future__ import annotations

from typing import Any

from audit_validator.touchpoint.modules import module_for
from audit_validator.touchpoint.payloads import FLOW_DEFS

SCENARIO_SEP = "::"


def scenario_id(operation: str, touchpoint: str) -> str:
    return f"{operation}{SCENARIO_SEP}{touchpoint}"


def parse_selection_id(item_id: str) -> tuple[str, str | None]:
    """Return (operation, touchpoint|None). Ingress/cron ids pass through untouched."""
    raw = (item_id or "").strip()
    if not raw or raw.startswith("ingress:") or raw.startswith("cron:"):
        return raw, None
    if SCENARIO_SEP in raw:
        op, touch = raw.split(SCENARIO_SEP, 1)
        return op.strip(), touch.strip() or None
    return raw, None


def list_scenarios() -> list[dict[str, Any]]:
    """Flat catalog of GraphQL touchpoint scenarios for the Generate UI."""
    out: list[dict[str, Any]] = []
    for op, touches in FLOW_DEFS.items():
        for touch, steps in touches.items():
            out.append(
                {
                    "id": scenario_id(op, touch),
                    "operation": op,
                    "touchpoint": touch,
                    "steps": list(steps),
                    "module": module_for(op),
                    "kind": "graphql",
                    "label": f"{op} · {touch}",
                }
            )
    out.sort(key=lambda r: (r["operation"].lower(), r["touchpoint"].lower()))
    return out


def scenarios_for_operation(operation: str) -> list[dict[str, Any]]:
    return [s for s in list_scenarios() if s["operation"] == operation]


def expand_selection_to_scenarios(selection: list[str]) -> list[dict[str, Any]]:
    """Map UI selection ids → scenario dicts.

    - ``activateFamily::List (FONTLIST)`` → that one scenario
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
            sid = scenario_id(op, touch)
            if sid in catalog and sid not in seen:
                chosen.append(catalog[sid])
                seen.add(sid)
            elif sid not in seen:
                chosen.append(
                    {
                        "id": sid,
                        "operation": op,
                        "touchpoint": touch,
                        "steps": [op],
                        "module": module_for(op),
                        "kind": "graphql",
                        "label": f"{op} · {touch}",
                    }
                )
                seen.add(sid)
            continue
        # Bare operation → all known touchpoints, or Discovery-only fallback
        if op in by_op:
            for s in by_op[op]:
                if s["id"] not in seen:
                    chosen.append(s)
                    seen.add(s["id"])
        else:
            sid = scenario_id(op, "Discovery/Browse (global)")
            if sid not in seen:
                chosen.append(
                    {
                        "id": sid,
                        "operation": op,
                        "touchpoint": "Discovery/Browse (global)",
                        "steps": [op],
                        "module": module_for(op),
                        "kind": "graphql",
                        "label": f"{op} · Discovery/Browse (global)",
                    }
                )
                seen.add(sid)
    return chosen
