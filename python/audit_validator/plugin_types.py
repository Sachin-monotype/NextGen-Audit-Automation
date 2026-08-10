"""Normalize plugin host apps for Results scenario labels.

Plugin ingress events (``pluginMissingFontUnresolved``, …) are grouped like
GQL touchpoints — e.g. ``pluginMissingFontUnresolved(keynotes)`` — using either
an Excel ``pluginsource`` column or enriched ``subject.plugin``.
"""

from __future__ import annotations

import re

# Canonical scenario suffixes (Results / Generate labels).
PLUGIN_TYPES = (
    "aftereffects",
    "photoshop",
    "incopy",
    "illustrator",
    "pages",
    "numbers",
    "keynotes",
)

_PLUGIN_TYPE_SET = frozenset(PLUGIN_TYPES)

# Raw Excel / subject.plugin values → canonical slug.
_ALIASES: dict[str, str] = {
    "aftereffect": "aftereffects",
    "aftereffects": "aftereffects",
    "after effects": "aftereffects",
    "after_effects": "aftereffects",
    "ae": "aftereffects",
    "photoshop": "photoshop",
    "ps": "photoshop",
    "adobe photoshop": "photoshop",
    "incopy": "incopy",
    "in copy": "incopy",
    "illustrator": "illustrator",
    "ai": "illustrator",
    "adobe illustrator": "illustrator",
    "pages": "pages",
    "numbers": "numbers",
    "keynotes": "keynotes",
    "keynote": "keynotes",
    "apple keynote": "keynotes",
    "apple pages": "pages",
    "apple numbers": "numbers",
}

_GENERIC = frozenset({"plugin", "plugins", "default", ""})


def normalize_plugin_type(raw: str | None) -> str:
    """Return canonical plugin slug, or ``""`` when unknown / generic."""
    s = str(raw or "").strip()
    if not s:
        return ""
    # Strip common prefixes from Connect plugin ids: KEYNOTES, PLUGIN_KEYNOTES, …
    low = re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    low = re.sub(r"^(plugin|adobe|apple)\s+", "", low).strip()
    if low in _GENERIC:
        return ""
    if low in _ALIASES:
        return _ALIASES[low]
    # Token match: "keynotes panel" / "indesign-plugin-id21"
    for token in low.replace("-", " ").split():
        if token in _ALIASES:
            return _ALIASES[token]
        if token in _PLUGIN_TYPE_SET:
            return token
    # Compact form without spaces
    compact = low.replace(" ", "")
    if compact in _ALIASES:
        return _ALIASES[compact]
    if compact in _PLUGIN_TYPE_SET:
        return compact
    # Unknown Connect plugin id (e.g. PANEL_ELECT_SBWIND) — keep a stable slug.
    slug = re.sub(r"[^a-z0-9]+", "_", low).strip("_")
    if slug and slug not in _GENERIC and len(slug) >= 3:
        return slug
    return ""


def is_plugin_type(value: str | None) -> bool:
    return normalize_plugin_type(value) in _PLUGIN_TYPE_SET


def plugin_type_from_subject(subject: dict | None) -> str:
    if not isinstance(subject, dict):
        return ""
    return normalize_plugin_type(subject.get("plugin"))


def resolve_plugin_scenario(
    *,
    excel_scenario: str | None = None,
    pluginsource: str | None = None,
    subject: dict | None = None,
) -> str:
    """Prefer explicit pluginsource, then subject.plugin, then Excel scenario if it is a host type."""
    for candidate in (
        normalize_plugin_type(pluginsource),
        plugin_type_from_subject(subject),
        normalize_plugin_type(excel_scenario),
    ):
        if candidate:
            return candidate
    # Keep non-generic excel scenario (e.g. custom labels) as-is short form.
    sc = str(excel_scenario or "").strip()
    if sc and sc.lower() not in _GENERIC:
        return re.sub(r"[^a-zA-Z0-9_]+", "_", sc).strip("_").lower()
    return ""
