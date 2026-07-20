"""FDC-14091 TestRail case map for Generate-in-UI scenarios.

Source of truth: ``python/audit_validator/data/fdc14091_testrail_map.json``
(synced from TestRail suite 22395 / section 4066542).

GraphQL Generate scenarios are fully mapped (112). CasePilot UI steps are authored
in ``ui_case_recipes.py`` and sent as context (``prefer_steps=context_over_testrail``).

Legacy FDC-00001 ids (C73300131…) remain as aliases for older handoffs.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_DATA = Path(__file__).resolve().parent / "data" / "fdc14091_testrail_map.json"

# Older smoke-pack aliases (still accepted if someone pastes them)
_LEGACY_ALIASES: dict[str, int] = {
    "activateFamily::Discovery/Browse (global)": 73300131,
    "activateFamily::List (FONTLIST)": 73300132,
    "activateFamily::Favourite": 73300133,
    "activateFamily::Project": 73300134,
    "activateFamily::Project > List": 73300135,
    "deactivateFamilies::Discovery/Browse (global)": 73300136,
    "activateStyle::Discovery/Browse (global)": 73300137,
    "createProject": 73300138,
    "createProject::Discovery/Browse (global)": 73300138,
    "addFavoriteFamilies": 73300139,
    "addFavoriteFamilies::Favourite": 73300139,
    "dismissNotification": 73300140,
    "dismissNotification::Discovery/Browse (global)": 73300140,
}


def _norm(s: str) -> str:
    return " ".join((s or "").lower().replace("_", " ").split())


def _short_touch(touch: str) -> str:
    t = _norm(touch).replace("/", " ").replace(">", " ")
    t = " ".join(t.split())
    if "project" in t and "list" in t:
        return "project_list"
    if "favourite" in t or "favorite" in t:
        return "favourite"
    if t == "project" or t.startswith("project "):
        return "project"
    if "list" in t or "fontlist" in t:
        return "list"
    if "discover" in t or "browse" in t or "search" in t or "global" in t or not t:
        return "global"
    return t.replace(" ", "_") or "global"


@lru_cache(maxsize=1)
def _load_map() -> dict[str, Any]:
    if not _DATA.is_file():
        return {"by_key": {}, "cases": [], "label_aliases": {}}
    try:
        data = json.loads(_DATA.read_text(encoding="utf-8"))
    except Exception:
        return {"by_key": {}, "cases": [], "label_aliases": {}}
    by_key = {str(k): int(v) for k, v in (data.get("by_key") or {}).items()}
    label_aliases: dict[str, int] = {}
    for row in data.get("cases") or []:
        if not isinstance(row, dict):
            continue
        op = str(row.get("operation") or "").strip()
        touch = str(row.get("touchpoint") or "").strip()
        cid = int(row.get("case_id") or 0)
        if not op or not cid:
            continue
        short = _short_touch(touch) if touch else ""
        label = f"{op}({short})".lower() if short else op.lower()
        label_aliases[label] = cid
        label_aliases[_norm(f"{op} {touch}")] = cid
    # Prefer FDC-14091 keys; keep legacy only when key missing
    for k, v in _LEGACY_ALIASES.items():
        by_key.setdefault(k, v)
    return {
        "by_key": by_key,
        "cases": data.get("cases") or [],
        "label_aliases": label_aliases,
        "suite": data.get("suite"),
        "jira": data.get("jira") or "FDC-14091",
    }


def reload_map() -> None:
    _load_map.cache_clear()


def fdc14091_case_map() -> dict[str, int]:
    return dict(_load_map()["by_key"])


def case_id_for_selection_item(item: dict[str, Any]) -> int | None:
    """Resolve a Generate catalog selection row to a TestRail case id."""
    data = _load_map()
    by_key: dict[str, int] = data["by_key"]
    labels: dict[str, int] = data["label_aliases"]

    sid = str(item.get("id") or "").strip()
    if sid in by_key:
        return by_key[sid]

    # Prefer explicit per-row test_case_id if already set (caller may override)
    raw = str(item.get("test_case_id") or item.get("testcase_id") or "").strip()
    if raw:
        digits = "".join(ch for ch in raw if ch.isdigit())
        if digits:
            return int(digits)

    label = _norm(str(item.get("label") or ""))
    if label in labels:
        return labels[label]
    compact = label.replace(" ", "")
    if compact in labels:
        return labels[compact]

    op = str(item.get("operation") or "").strip()
    touch = str(item.get("touchpoint") or "").strip()
    if touch:
        key = f"{op}::{touch}"
        if key in by_key:
            return by_key[key]
    if op in by_key:
        return by_key[op]

    # Fuzzy: match any stored key with same op + short touch
    short = _short_touch(touch) if touch else ""
    label_key = f"{op}({short})".lower() if short else op.lower()
    if label_key in labels:
        return labels[label_key]

    for key, cid in by_key.items():
        if not key.startswith(f"{op}::"):
            continue
        if short and _short_touch(key.split("::", 1)[-1]) == short:
            return cid
        if not short:
            return cid
    return None


def map_selection_to_case_ids(selection: list[dict[str, Any]]) -> list[int]:
    """Ordered unique TestRail ids for the selected scenarios."""
    out: list[int] = []
    seen: set[int] = set()
    for item in selection:
        if not isinstance(item, dict):
            continue
        cid = case_id_for_selection_item(item)
        if cid and cid not in seen:
            seen.add(cid)
            out.append(cid)
    return out


def format_case_ids(case_ids: list[int]) -> str:
    return ", ".join(str(c) for c in case_ids)


def public_testrail_map() -> dict[str, Any]:
    """Payload for frontend Generate-in-UI modal."""
    data = _load_map()
    return {
        "jira": data.get("jira") or "FDC-14091",
        "suite": data.get("suite"),
        "count": len(data.get("cases") or []),
        "by_key": data["by_key"],
        "by_label": data["label_aliases"],
        "cases": data.get("cases") or [],
        "testrail_case_url": "https://type.testrail.com/index.php?/cases/view/",
    }
