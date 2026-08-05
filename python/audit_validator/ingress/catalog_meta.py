"""Ingress catalog metadata (category / service / env) for Results & Compare UI."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_MANIFEST = Path(__file__).resolve().parent.parent / "data" / "ingress_payloads" / "manifest.json"

# Manifest slug → canonical Results/Compare category label.
_MANIFEST_CATEGORY: dict[str, str] = {
    "plugin_events": "Font Sync & activation",
    "desktop_app_preference_page": "Exports & maintenance",
    "font_activations": "Font Sync & activation",
    "login": "User & Access",
}


@lru_cache(maxsize=1)
def ingress_catalog_by_operation() -> dict[str, dict[str, str]]:
    """Map ``source.operation`` → display metadata from ingress_payloads/manifest.json."""
    if not _MANIFEST.is_file():
        return {}
    try:
        meta = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, dict[str, str]] = {}
    for row in meta.get("cases") or []:
        if not isinstance(row, dict) or row.get("skipped"):
            continue
        op = str(row.get("operation") or "").strip()
        if not op:
            continue
        slug = str(row.get("category") or "").strip()
        category = _MANIFEST_CATEGORY.get(slug, slug.replace("_", " ").title())
        service = str(row.get("service") or "").strip()
        # Desktop / plugin ingress samples use platformEnvironment on the envelope.
        environment = "plugin" if service == "Plugin" else "app"
        prev = out.get(op)
        if prev and prev.get("category") == category:
            continue
        out[op] = {
            "category": category,
            "service": service,
            "environment": environment,
            "ingress_category": slug,
            "case_id": str(row.get("case_id") or ""),
            "event_name": str(row.get("event_name") or ""),
        }
    return out


def ingress_meta_for_operation(operation: str) -> dict[str, str]:
    """Lookup ingress catalog row for ``operation`` or its bare base name."""
    op = (operation or "").strip()
    if not op:
        return {}
    catalog = ingress_catalog_by_operation()
    if op in catalog:
        return dict(catalog[op])
    base = op.split("(", 1)[0].strip() if "(" in op else op
    return dict(catalog.get(base) or {})
