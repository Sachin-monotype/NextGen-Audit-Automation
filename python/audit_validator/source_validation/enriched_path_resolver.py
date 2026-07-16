"""Resolve values from enriched audit JSON using Confluence-aligned paths + fallbacks."""

from __future__ import annotations

import re
from typing import Any

from ..models import JsonDict

# Registry / Excel paths that predate resolver snapshot layout (see enrichment_remark_patterns.json).
_PATH_ALIASES: tuple[tuple[str, str], ...] = (
    (".fontDetails[0].foundry.", ".fontDetails[0].family.foundry."),
    (".fontDetails[0].variations[0].md5", ".fontDetails[0].styles[0].variations[0].catalog.md5"),
    (".fontDetails[0].styles[].id", ".fontDetails[0].styles[0].id"),
    (".fontDetails[0].family.catalog.name_en", ".fontDetails[0].family.catalog.name_en"),  # identity
    ("permissions[].id", "permissions[0].id"),
    ("teams[]", "teams[0]"),
)


def normalize_enriched_path(path: str) -> str:
    out = (path or "").strip()
    for old, new in _PATH_ALIASES:
        if old in out:
            out = out.replace(old, new)
    return out


def _has_value(val: object) -> bool:
    if val is None:
        return False
    if val == "" or val == [] or val == {}:
        return False
    return True


def dig_once(obj: object, path: str) -> object:
    """Walk a dotted path with optional [0] / [] array segments."""
    path = normalize_enriched_path(path)
    cur: object = obj
    for raw_part in path.split("."):
        if cur is None:
            return None
        part = raw_part.replace("[]", "[0]")
        m = re.fullmatch(r"(\w+)\[(\d+)\]", part)
        if m:
            key, idx = m.group(1), int(m.group(2))
            if isinstance(cur, dict):
                cur = cur.get(key)
            if isinstance(cur, list):
                cur = cur[idx] if len(cur) > idx else None
            else:
                return None
            continue
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _fallback_paths(enriched: JsonDict, path: str) -> list[str]:
    """Alternate locations where resolver stores the same semantic field."""
    alts: list[str] = []
    norm = normalize_enriched_path(path)

    if norm.startswith("actor.enrichedSnapshot.user.profile."):
        field = norm.rsplit(".", 1)[-1]
        alts.extend([
            f"subject.enrichedSnapshot.profile.{field}",
            f"subject.enrichedSnapshot.user.profile.{field}",
        ])
        if field == "id":
            alts.append("actor.globalUserId")

    if norm.startswith("actor.enrichedSnapshot.customer."):
        field = norm.rsplit(".", 1)[-1]
        if field == "id":
            alts.append("actor.globalCustomerId")
        if field in {"name", "displayName"}:
            alts.append(f"actor.enrichedSnapshot.customer.displayName")

    # fontDetails: catalog fields sometimes live under styles[0].catalog not family.catalog
    if ".family.catalog." in norm:
        alts.append(norm.replace(".family.catalog.", ".styles[0].catalog."))

    if norm.endswith(".styles[0].variations[0].catalog.md5"):
        base = norm.rsplit(".", 1)[0]
        alts.append(f"{base}.md5")
        alts.append(norm.replace(".catalog.md5", ".catalog.render_md5"))

    return alts


def dig_enriched(enriched: JsonDict, path: str) -> object:
    """Read enriched value; try canonical path then Confluence-aligned fallbacks."""
    if not path:
        return None
    for candidate in [normalize_enriched_path(path), *_fallback_paths(enriched, path)]:
        val = dig_once(enriched, candidate)
        if _has_value(val):
            return val
    return dig_once(enriched, path)


def actor_snapshot(enriched: JsonDict) -> dict:
    return ((enriched.get("actor") or {}).get("enrichedSnapshot") or {}) if isinstance(enriched.get("actor"), dict) else {}


def subject_snapshot(enriched: JsonDict) -> dict:
    return ((enriched.get("subject") or {}).get("enrichedSnapshot") or {}) if isinstance(enriched.get("subject"), dict) else {}


def snapshot_present(enriched: JsonDict, layer: str) -> bool:
    if layer == "actor":
        return bool(actor_snapshot(enriched))
    if layer == "subject":
        return bool(subject_snapshot(enriched))
    return True
