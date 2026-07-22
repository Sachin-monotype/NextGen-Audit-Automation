"""UI navigation catalog for batch export mutations."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_CATALOG_PATH = Path(__file__).resolve().parent / "data" / "export_ui_catalog.json"


@lru_cache(maxsize=1)
def load_export_ui_catalog() -> dict[str, Any]:
    if not _CATALOG_PATH.is_file():
        return {"exports": {}}
    return json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))


def export_ops() -> list[str]:
    exports = load_export_ui_catalog().get("exports") or {}
    return sorted(exports.keys())


def export_spec(operation: str) -> dict[str, Any] | None:
    exports = load_export_ui_catalog().get("exports") or {}
    spec = exports.get(operation)
    return spec if isinstance(spec, dict) else None


def export_touchpoint(operation: str) -> str:
    spec = export_spec(operation) or {}
    return str(spec.get("touchpoint") or "Discovery/Browse (global)")


def export_flow_defs() -> dict[str, dict[str, list[str]]]:
    """``FLOW_DEFS`` fragment: one touchpoint per export op."""
    out: dict[str, dict[str, list[str]]] = {}
    for op in export_ops():
        touch = export_touchpoint(op)
        out[op] = {touch: [op]}
    return out
