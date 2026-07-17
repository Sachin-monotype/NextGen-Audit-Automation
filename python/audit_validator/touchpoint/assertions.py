"""Touchpoint-aware assertions on raw ``subject.metadata.input``.

Enrich snapshot for family activation is largely touchpoint-invariant; analytics
differences live in the raw input (listIds / listType / projectId / …).
Shapes mirror UI Navigation sheet + ActivateFamilyInput routing in mtconnect-api.
"""

from __future__ import annotations

from typing import Any


def normalize_touchpoint(touch: str) -> str:
    t = " ".join((touch or "").lower().replace("/", " ").replace(">", " ").split())
    if "search" in t and "discover" in t:
        return "discovery"
    if "discover" in t or "browse" in t and "list" not in t and "project" not in t:
        return "discovery"
    if "favourite" in t or "favorite" in t:
        return "favourite"
    if "project" in t and "list" in t:
        return "project_list"
    if t.strip() == "project" or t.startswith("project "):
        return "project"
    if "list" in t or "fontlist" in t:
        return "list"
    return t or "discovery"


def expected_activate_family_input_keys(touch: str) -> dict[str, Any]:
    """Return required keys / value predicates for activateFamily raw input."""
    kind = normalize_touchpoint(touch)
    base = {"familyIds": list, "activationType": str}
    if kind == "discovery":
        return {**base, "_forbid": ("listIds", "listType", "projectId")}
    if kind == "favourite":
        return {**base, "listType": "FAVORITE", "_forbid": ("listIds",)}
    if kind == "list":
        return {**base, "listIds": list, "listType": "FONTLIST"}
    if kind == "project":
        return {
            **base,
            "listIds": list,
            "listType": "FONTPROJECT",
            "projectId": str,
        }
    if kind == "project_list":
        return {
            **base,
            "listIds": list,
            "listType": "FONTLIST",
            "projectId": str,
        }
    return base


def assert_raw_input_matches_touchpoint(
    operation: str,
    touchpoint: str,
    raw_input: dict[str, Any] | None,
) -> list[str]:
    """Return list of assertion failure messages (empty = pass)."""
    errs: list[str] = []
    inp = raw_input or {}
    if operation != "activateFamily":
        # Extend per-op as we harden other FLOW_DEFS
        return errs

    spec = expected_activate_family_input_keys(touchpoint)
    forbid = set(spec.pop("_forbid", ()))
    for key, expect in spec.items():
        if key not in inp:
            errs.append(f"missing metadata.input.{key} for touchpoint={touchpoint!r}")
            continue
        val = inp[key]
        if expect is list and not isinstance(val, list):
            errs.append(f"metadata.input.{key} expected list, got {type(val).__name__}")
        elif expect is str and not isinstance(val, str):
            errs.append(f"metadata.input.{key} expected str, got {type(val).__name__}")
        elif isinstance(expect, str) and val != expect:
            errs.append(f"metadata.input.{key}={val!r} expected {expect!r}")
    for key in forbid:
        if key in inp and inp[key] not in (None, [], ""):
            errs.append(
                f"metadata.input.{key}={inp[key]!r} must be absent for touchpoint={touchpoint!r}"
            )
    # Project listIds should be project_<uuid>
    kind = normalize_touchpoint(touchpoint)
    if kind == "project":
        lids = inp.get("listIds") or []
        if lids and not str(lids[0]).startswith("project_"):
            errs.append(f"project touchpoint listIds should be project_<id>, got {lids!r}")
    return errs


def extract_raw_metadata_input(raw_doc: dict[str, Any]) -> dict[str, Any]:
    """Pull subject.metadata.input from a Mongo raw envelope (several shapes)."""
    msg = raw_doc.get("message") if isinstance(raw_doc.get("message"), dict) else raw_doc
    if not isinstance(msg, dict):
        return {}
    subject = msg.get("subject") or {}
    meta = subject.get("metadata") or msg.get("metadata") or {}
    inp = meta.get("input")
    return inp if isinstance(inp, dict) else {}
