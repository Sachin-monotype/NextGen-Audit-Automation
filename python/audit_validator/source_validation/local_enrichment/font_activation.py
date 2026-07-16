"""Mirror buildActivateFamilyEnrichedSnapshot from font-activation-resolution.service.ts."""

from __future__ import annotations

from ...models import JsonDict
from .style_variation import resolve_style_variation_data
from .types import DiscoveryLike


def build_activate_family_snapshot(
    *,
    family_ids: list[str],
    correlation_id: str,
    discovery: DiscoveryLike,
) -> JsonDict:
    style_hits = discovery.fetch_styles_by_family_ids(
        family_ids, correlation_id=correlation_id
    )
    variation_hits = discovery.fetch_variations_by_family_ids(
        family_ids, correlation_id=correlation_id
    )
    font_details = resolve_style_variation_data(style_hits, variation_hits)
    return {
        "source": "mt-connect-middleware-discovery",
        "fontDetails": font_details,
    }


def family_ids_from_envelope(envelope: JsonDict) -> list[str]:
    subject = envelope.get("subject") or {}
    meta = subject.get("metadata") or {}
    inp = meta.get("input") or {}
    ids = inp.get("familyIds") or subject.get("id") or []
    out: list[str] = []
    for x in ids if isinstance(ids, list) else [ids]:
        s = str(x or "").strip()
        if s.isdigit():
            out.append(s)
    return list(dict.fromkeys(out))
