"""Resolve Discovery/Typesense field values matched to enriched fontDetails context."""

from __future__ import annotations

import json
import re
from typing import Any

from ..models import JsonDict

_INDEX_RE = re.compile(r"\[(\d+)\]")


def _as_str(val: object) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    return s or None


def _path_indices(enriched_path: str) -> dict[str, int | None]:
    """Extract fontDetails / styles / variations / trailing array indexes from path."""
    def _idx(label: str) -> int | None:
        m = re.search(rf"{label}\[(\d+)\]", enriched_path)
        return int(m.group(1)) if m else None

    trailing = enriched_path.rsplit(".", 1)[-1]
    arr_idx = None
    m = _INDEX_RE.search(trailing)
    if m:
        arr_idx = int(m.group(1))
    return {
        "font_details": _idx("fontDetails"),
        "styles": _idx("styles"),
        "variations": _idx("variations"),
        "array_index": arr_idx,
    }


def font_context(enriched: JsonDict, enriched_path: str = "") -> dict[str, str | None]:
    """Style / family / variation ids for the fontDetails slot referenced by *enriched_path*."""
    idx = _path_indices(enriched_path) if enriched_path else {
        "font_details": 0,
        "styles": 0,
        "variations": 0,
        "array_index": None,
    }
    fd_i = idx["font_details"] if idx["font_details"] is not None else 0
    st_i = idx["styles"] if idx["styles"] is not None else 0
    var_i = idx["variations"] if idx["variations"] is not None else 0

    snap = ((enriched.get("subject") or {}).get("enrichedSnapshot") or {})
    details = snap.get("fontDetails") or []
    family_id = None
    style_id = None
    variation_md5 = None

    if details and isinstance(details, list) and len(details) > fd_i:
        fd0 = details[fd_i]
        if isinstance(fd0, dict):
            fam = fd0.get("family") or {}
            if isinstance(fam, dict):
                family_id = _as_str(fam.get("id"))
            elif _as_str(fam):
                family_id = _as_str(fam)
            styles = fd0.get("styles") or []
            if styles and isinstance(styles, list) and len(styles) > st_i:
                st = styles[st_i]
                if isinstance(st, dict):
                    style_id = _as_str(st.get("id"))
                    variations = st.get("variations") or []
                    if variations and isinstance(variations, list) and len(variations) > var_i:
                        v0 = variations[var_i]
                        if isinstance(v0, dict):
                            cat = v0.get("catalog") if isinstance(v0.get("catalog"), dict) else v0
                            variation_md5 = _as_str((cat or {}).get("md5") or v0.get("md5"))

    # When resolver omitted fontDetails, derive style/family ids from raw envelope metadata.
    if not style_id or not family_id:
        subject = enriched.get("subject") or {}
        for sid in subject.get("styleIds") or []:
            if not style_id:
                style_id = _as_str(sid)
        meta = subject.get("metadata") or {}
        inp = meta.get("input") or {}
        for item in inp.get("styles") or []:
            if isinstance(item, dict) and not style_id:
                style_id = _as_str(item.get("styleId") or item.get("id"))
        result = meta.get("result") or {}
        for node in (result.get("styles") or {}).get("nodes") or []:
            if isinstance(node, dict) and not style_id:
                style_id = _as_str(node.get("id"))
        for fid in inp.get("familyIds") or []:
            if not family_id:
                family_id = _as_str(fid)

    return {
        "family_id": family_id,
        "style_id": style_id,
        "variation_md5": variation_md5,
    }


def _style_hit_for(
    style_hits: list[dict[str, Any]],
    *,
    style_id: str | None,
    family_id: str | None,
) -> dict[str, Any] | None:
    if style_id:
        for hit in style_hits:
            if _as_str(hit.get("id")) == style_id:
                return hit
    if family_id:
        for hit in style_hits:
            fam = hit.get("mtc_families_data") or {}
            if isinstance(fam, dict) and _as_str(fam.get("id")) == family_id:
                return hit
    return None


def _variation_hit_for(
    variation_hits: list[dict[str, Any]],
    *,
    variation_md5: str | None,
    style_id: str | None,
) -> dict[str, Any] | None:
    if variation_md5:
        for hit in variation_hits:
            if _as_str(hit.get("md5")) == variation_md5:
                return hit
    if style_id:
        for hit in variation_hits:
            if _as_str(hit.get("style_id")) == style_id:
                return hit
    return None


def _leaf_key(enriched_path: str, discovery_key: str) -> str:
    if discovery_key:
        return discovery_key.replace("[]", "").strip()
    leaf = enriched_path.split(".")[-1]
    return _INDEX_RE.sub("", leaf)


def _array_element(val: object, index: int | None) -> object:
    if index is None:
        return val
    if isinstance(val, list) and 0 <= index < len(val):
        return val[index]
    return val


def _visual_property(style_hit: dict[str, Any], sub_key: str) -> object:
    vp = style_hit.get("visual_properties")
    if isinstance(vp, dict) and sub_key in vp:
        return vp[sub_key]
    return None


def lookup_discovery_value(
    enriched_path: str,
    enriched: JsonDict,
    *,
    style_hits: list[dict[str, Any]],
    variation_hits: list[dict[str, Any]],
    discovery_key: str = "",
) -> object:
    ctx = font_context(enriched, enriched_path)
    path = enriched_path
    key = _leaf_key(path, discovery_key)
    indices = _path_indices(path)
    arr_idx = indices["array_index"]

    # Variation catalog fields
    if "variations" in path and ("catalog" in path or key in {
        "md5", "font_psname", "font_filename", "font_format", "font_weight", "glyph_count",
        "id", "variation_name",
    }):
        hit = _variation_hit_for(
            variation_hits,
            variation_md5=ctx["variation_md5"],
            style_id=ctx["style_id"],
        )
        if not hit:
            return None
        if key == "md5":
            return hit.get("md5")
        if path.endswith(".id") and "catalog" not in path:
            return hit.get("id") or hit.get("variation_id")
        return hit.get(key)

    style_hit = _style_hit_for(
        style_hits, style_id=ctx["style_id"], family_id=ctx["family_id"]
    )
    if not style_hit:
        return None

    # Family-level from style document
    if "family.id" in path or path.endswith(".family.id"):
        fam = style_hit.get("mtc_families_data") or {}
        return fam.get("id") if isinstance(fam, dict) else None
    if "family.catalog" in path or ".catalog.name_en" in path:
        fam = style_hit.get("mtc_families_data") or {}
        if not isinstance(fam, dict):
            return None
        if key == "name_en" or "name_en" in path:
            return fam.get("name_en")
        if key == "title_en" or "title_en" in path:
            return fam.get("title_en")
        if key == "id":
            return fam.get("id")
        if key == "family_url_key":
            return fam.get("family_url_key")
    if "foundry" in path:
        foundry = style_hit.get("mtc_foundries_data") or {}
        if isinstance(foundry, dict):
            return foundry.get(key) if key else foundry.get("name_en")
    if path.endswith(".styles[0].id") or (
        ".styles" in path and key == "id" and "catalog" not in path and "variations" not in path
    ):
        return style_hit.get("id")

    # Variation document id (not style id) — outside catalog block
    if "variations" in path and path.endswith(".id") and "catalog" not in path:
        hit = _variation_hit_for(
            variation_hits,
            variation_md5=ctx["variation_md5"],
            style_id=ctx["style_id"],
        )
        if hit:
            return hit.get("id") or hit.get("variation_id")

    # subject.id — family id from style catalog
    if path.startswith("subject.id"):
        fam = style_hit.get("mtc_families_data") or {}
        return fam.get("id") if isinstance(fam, dict) else None

    # visual_properties sub-fields (contrast, height, slant, weight, width)
    if "visual_properties" in path:
        sub = path.rsplit(".", 1)[-1]
        if sub != "visual_properties":
            val = _visual_property(style_hit, sub)
            if val is not None:
                return val
        return style_hit.get("visual_properties")

    # Indexed array catalog fields on style document
    if key in {"font_nids", "font_ps_names"}:
        return _array_element(style_hit.get(key), arr_idx)

    if key == "font_ps_names":
        return _array_element(style_hit.get("font_ps_names"), arr_idx)
    if key == "font_nids":
        return _array_element(style_hit.get("font_nids"), arr_idx)
    if key == "font_name":
        return style_hit.get("font_name")
    if key == "font_url_key":
        return style_hit.get("font_url_key")
    if key == "font_pim_style_id":
        return style_hit.get("font_pim_style_id")
    if key == "render_md5":
        return style_hit.get("render_md5")
    if key == "md5":
        return style_hit.get("md5") or style_hit.get("render_md5")

    # Style catalog fields on enriched map to style document columns
    if ".styles[" in path and ".catalog." in path:
        cat_key = path.rsplit(".", 1)[-1]
        cat_key = _INDEX_RE.sub("", cat_key)
        catalog_aliases = {
            "font_name": "font_name",
            "font_nids": "font_nids",
            "font_ps_names": "font_ps_names",
            "font_pim_style_id": "font_pim_style_id",
            "font_url_key": "font_url_key",
            "is_default": "is_default",
            "is_custom": "is_custom",
            "is_imported_font": "is_imported_font",
            "is_var": "is_var",
            "glyph_count": "glyph_count",
            "font_weight": "font_weight",
            "font_format": "font_format",
            "font_filename": "font_filename",
            "font_psname": "font_psname",
            "variation_name": "variation_name",
        }
        alias = catalog_aliases.get(cat_key, cat_key)
        if "variations" in path:
            hit = _variation_hit_for(
                variation_hits,
                variation_md5=ctx["variation_md5"],
                style_id=ctx["style_id"],
            )
            if hit:
                return hit.get(alias)
        raw = style_hit.get(alias)
        if alias in {"font_nids", "font_ps_names"}:
            return _array_element(raw, arr_idx)
        return raw

    if key in style_hit:
        raw = style_hit.get(key)
        if key in {"font_nids", "font_ps_names"}:
            return _array_element(raw, arr_idx)
        return raw

    return style_hit.get(key)


def normalize_compare(val: object) -> str:
    if val is None:
        return ""
    if isinstance(val, (dict, list)):
        return json.dumps(val, sort_keys=True, ensure_ascii=False)
    return str(val).strip()
