"""FDC-00001 TestRail case map for Generate-in-UI scenarios.

Imported cases (TestRail):
  C73300131 … C73300140
"""

from __future__ import annotations

from typing import Any

# Canonical scenario_id / operation+touchpoint → TestRail case id
FDC_00001_CASES: dict[str, int] = {
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

# Short labels used in the UI catalog
_LABEL_ALIASES: dict[str, int] = {
    "activatefamily(global)": 73300131,
    "activatefamily(list)": 73300132,
    "activatefamily(favourite)": 73300133,
    "activatefamily(project)": 73300134,
    "activatefamily(project_list)": 73300135,
    "deactivatefamilies(global)": 73300136,
    "activatestyle(global)": 73300137,
    "createproject(global)": 73300138,
    "createproject": 73300138,
    "addfavoritefamilies": 73300139,
    "addfavoritefamilies(favourite)": 73300139,
    "dismissnotification": 73300140,
    "dismissnotification(global)": 73300140,
}


def _norm(s: str) -> str:
    return " ".join((s or "").lower().replace("_", " ").split())


def case_id_for_selection_item(item: dict[str, Any]) -> int | None:
    """Resolve a Generate catalog selection row to a TestRail case id."""
    sid = str(item.get("id") or "").strip()
    if sid in FDC_00001_CASES:
        return FDC_00001_CASES[sid]
    label = _norm(str(item.get("label") or ""))
    if label in _LABEL_ALIASES:
        return _LABEL_ALIASES[label]
    op = str(item.get("operation") or "").strip()
    touch = str(item.get("touchpoint") or "").strip()
    if touch:
        key = f"{op}::{touch}"
        if key in FDC_00001_CASES:
            return FDC_00001_CASES[key]
    if op in FDC_00001_CASES:
        return FDC_00001_CASES[op]
    # Fuzzy touchpoint aliases
    t = _norm(touch)
    if op == "activateFamily":
        if "list" in t and "project" in t:
            return 73300135
        if "favourite" in t or "favorite" in t:
            return 73300133
        if "project" in t:
            return 73300134
        if "list" in t:
            return 73300132
        if "discovery" in t or "global" in t or "browse" in t or not t:
            return 73300131
    if op == "deactivateFamilies" and ("discovery" in t or "global" in t or not t):
        return 73300136
    if op == "activateStyle" and ("discovery" in t or "global" in t or not t):
        return 73300137
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
