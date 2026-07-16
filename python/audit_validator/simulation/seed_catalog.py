"""Resolve font family/style seeds from the NextGen search API (environment-aware)."""

from __future__ import annotations

import logging
import random
import uuid
from dataclasses import dataclass, replace
from typing import Any

import requests

from ..auth import resolve_nextgen_bearer_token
from .config import GraphQLSimulationConfig, SeedConfig

log = logging.getLogger(__name__)

_DEFAULT_ROLE_PERMISSION_GROUPS = (
    "SEARCH_AND_DISCOVER_FONTS",
    "ACTIVATE_PROJECT_FONTS",
    "ACTIVATE_LIST_FONTS",
)


@dataclass(frozen=True)
class CatalogFont:
    family_id: str
    family_name: str
    style_id: str
    style_name: str
    md5: str


def role_permission_groups() -> list[str]:
    return list(_DEFAULT_ROLE_PERMISSION_GROUPS)


def _parse_grouped_hits(data: dict[str, Any]) -> list[CatalogFont]:
    results = data.get("results") or {}
    grouped = results.get("grouped_hits") or []
    fonts: list[CatalogFont] = []

    for group in grouped:
        family_id = ""
        group_key = group.get("group_key") or []
        if group_key:
            family_id = str(group_key[0])
        for hit in group.get("hits") or []:
            doc = (hit or {}).get("document") or {}
            fam = doc.get("mtc_families_data") or {}
            family_id = str(fam.get("id") or family_id or "")
            style_id = str(doc.get("id") or doc.get("font_pim_style_id") or "")
            style_name = str(
                doc.get("font_name")
                or fam.get("name_en")
                or fam.get("title_en")
                or "Style"
            )
            md5_raw = doc.get("render_md5") or (doc.get("md5") or [None])[0]
            md5 = str(md5_raw or "")
            if family_id and style_id:
                fonts.append(
                    CatalogFont(
                        family_id=family_id,
                        family_name=str(fam.get("name_en") or fam.get("title_en") or ""),
                        style_id=style_id,
                        style_name=style_name,
                        md5=md5,
                    )
                )
    return fonts


def fetch_catalog_fonts(
    cfg: GraphQLSimulationConfig,
    *,
    per_page: int = 24,
    page: int | None = None,
) -> list[CatalogFont]:
    """POST /api/search/v1/search — same contract as NextGen discover fonts."""
    token = (cfg.nextgen_bearer_token or cfg.bearer_token or "").strip()
    if not token:
        token = resolve_nextgen_bearer_token() or ""
    if not token:
        log.warning("Dynamic seeds skipped — no bearer token for search API")
        return []

    origin = cfg.nextgen_origin.rstrip("/")
    url = f"{origin}/api/search/v1/search"
    headers = {
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
        "accept": "application/json, text/plain, */*",
        "accept-language": cfg.accept_language,
        "origin": origin,
        "referer": cfg.nextgen_referer,
        "x-correlation-id": str(uuid.uuid4()),
    }
    body = {
        "query": "",
        "per_page": per_page,
        "page": page or random.randint(1, 20),
        "sort_by": "a-z",
        "include_facets": False,
        "facet_filters": {},
    }
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=45)
        resp.raise_for_status()
        fonts = _parse_grouped_hits(resp.json())
        log.info(
            "Search catalog — page=%s fonts=%d (from %s)",
            body["page"],
            len(fonts),
            url,
        )
        return fonts
    except Exception as exc:
        log.warning("Search catalog fetch failed: %s", exc)
        return []


def _pick_distinct(fonts: list[CatalogFont], count: int) -> list[CatalogFont]:
    by_family: dict[str, list[CatalogFont]] = {}
    for f in fonts:
        by_family.setdefault(f.family_id, []).append(f)
    family_ids = list(by_family.keys())
    random.shuffle(family_ids)
    picked: list[CatalogFont] = []
    for fid in family_ids:
        styles = by_family[fid]
        random.shuffle(styles)
        picked.append(styles[0])
        if len(picked) >= count:
            break
    return picked


def _bulk_style_entries(fonts: list[CatalogFont]) -> tuple[dict, ...]:
    out: list[dict] = []
    for f in fonts[:2]:
        out.append({"id": f.style_id, "metadata": {"styleName": f.style_name}})
    return tuple(out)


def apply_dynamic_seeds(cfg: GraphQLSimulationConfig) -> GraphQLSimulationConfig:
    """
    Overlay .env seeds with catalog IDs from the active environment.

    Falls back to existing SeedConfig when search returns nothing.
    """
    import os

    if os.getenv("DYNAMIC_SEEDS", "true").strip().lower() in {"0", "false", "no"}:
        return cfg

    fonts = fetch_catalog_fonts(cfg)
    if len(fonts) < 2:
        log.warning("Dynamic seeds — insufficient catalog hits; keeping .env seeds")
        return cfg

    # Prefer catalog fonts that exist in this tenant's inventory (getFamilies).
    try:
        from ..live_seeds import family_activation_state

        verified = [f for f in fonts if family_activation_state(f.family_id) is not None]
        if len(verified) >= 2:
            fonts = verified
        elif verified:
            fonts = verified + [f for f in fonts if f not in verified]
    except Exception as exc:
        log.debug("Dynamic seeds inventory filter skipped: %s", exc)

    primary, secondary = _pick_distinct(fonts, 2)
    same_family_styles = [f for f in fonts if f.family_id == primary.family_id][:2]
    if len(same_family_styles) < 2:
        same_family_styles = [primary, secondary]

    bulk = _bulk_style_entries(same_family_styles)
    variation = primary if primary.md5 else secondary

    seed = replace(
        cfg.seed,
        family_id=primary.family_id,
        deactivate_family_id=secondary.family_id,
        favorite_family_id=primary.family_id,
        style_id=primary.style_id,
        deactivate_style_id=secondary.style_id,
        favorite_style_id=primary.style_id,
        variation_style_id=variation.style_id,
        variation_md5=variation.md5 or cfg.seed.variation_md5,
        deactivate_variation_md5=variation.md5 or cfg.seed.deactivate_variation_md5,
        bulk_activate_styles=bulk or cfg.seed.bulk_activate_styles,
        bulk_favourite_styles=bulk or cfg.seed.bulk_favourite_styles,
        family_ids=tuple({primary.family_id, secondary.family_id}),
    )
    log.info(
        "Dynamic seeds — activate family=%s style=%s | deactivate family=%s style=%s",
        seed.family_id,
        seed.style_id,
        seed.deactivate_family_id,
        seed.deactivate_style_id,
    )
    return replace(cfg, seed=seed)
