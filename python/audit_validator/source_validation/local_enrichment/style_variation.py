"""Port of resolveStyleVariationData from resolver discovery-resolution.util."""

from __future__ import annotations

from typing import Any


def _as_str(val: object) -> str:
    return str(val or "").strip()


def _family_id_from_style(style: dict[str, Any]) -> str | None:
    fam = style.get("mtc_families_data") or {}
    if isinstance(fam, dict):
        fid = _as_str(fam.get("id"))
        return fid or None
    return None


def _resolve_family_catalog(family_id: str, styles: list[dict[str, Any]]) -> dict[str, Any]:
    for style in styles:
        fam = style.get("mtc_families_data") or {}
        if isinstance(fam, dict) and _as_str(fam.get("id")) == family_id:
            return {
                "id": family_id,
                "name_en": fam.get("name_en"),
                "title_en": fam.get("title_en"),
                "family_url_key": fam.get("family_url_key"),
            }
    return {"id": family_id}


def _foundry_from_style(style: dict[str, Any]) -> dict[str, Any] | None:
    foundry = style.get("mtc_foundries_data")
    return foundry if isinstance(foundry, dict) else None


def resolve_style_variation_data(
    style_hits: list[dict[str, Any]],
    variation_hits: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build fontDetails[] from Discovery style + variation hits (activateFamily pattern)."""
    by_family: dict[str, list[dict[str, Any]]] = {}
    for style in style_hits:
        fid = _family_id_from_style(style)
        if not fid:
            continue
        by_family.setdefault(fid, []).append(style)

    var_by_md5: dict[str, dict[str, Any]] = {}
    for hit in variation_hits:
        md5 = _as_str(hit.get("md5"))
        if md5:
            var_by_md5[md5] = hit

    font_details: list[dict[str, Any]] = []
    for family_id, family_styles in by_family.items():
        family_catalog = _resolve_family_catalog(family_id, family_styles)
        foundry = _foundry_from_style(family_styles[0]) if family_styles else None
        styles_out: list[dict[str, Any]] = []
        for style in family_styles:
            render_md5 = _as_str(style.get("render_md5"))
            variation_doc = var_by_md5.get(render_md5)
            variations: list[dict[str, Any]] = []
            if variation_doc:
                variations.append(
                    {
                        "id": _as_str(variation_doc.get("id") or variation_doc.get("variation_id")),
                        "catalog": variation_doc,
                    }
                )
            styles_out.append(
                {
                    "id": _as_str(style.get("id")),
                    "catalog": style,
                    "variations": variations,
                }
            )
        entry: dict[str, Any] = {
            "family": {
                "id": family_id,
                "catalog": family_catalog,
            },
            "styles": styles_out,
        }
        if foundry:
            entry["family"]["foundry"] = foundry
        font_details.append(entry)
    return font_details
