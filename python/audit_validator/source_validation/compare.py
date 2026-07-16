"""Compare enriched JSON values to upstream API responses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models import JsonDict
from .value_match import values_equivalent


@dataclass(frozen=True)
class FieldCheck:
    path: str
    status: str  # PASS | FAIL | SKIP
    expected_source: str
    message: str


def _dig(obj: object, path: str) -> object:
    cur = obj
    for part in path.replace("[]", "").split("."):
        if not part:
            continue
        if cur is None:
            return None
        if isinstance(cur, list):
            if not cur:
                return None
            cur = cur[0]
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _norm(val: object) -> str:
    if val is None:
        return ""
    return str(val).strip()


def check_paths_present(enriched: JsonDict, paths: list[str]) -> list[FieldCheck]:
    out: list[FieldCheck] = []
    for path in paths:
        val = _dig(enriched, path)
        if val in (None, "", []):
            out.append(
                FieldCheck(path, "FAIL", "enriched", f"Missing or empty at `{path}`")
            )
        else:
            out.append(FieldCheck(path, "PASS", "enriched", "Present"))
    return out


def validate_activate_family_discovery(
    enriched: JsonDict,
    *,
    style_hits: list[dict[str, Any]],
    variation_hits: list[dict[str, Any]],
) -> list[FieldCheck]:
    checks: list[FieldCheck] = []
    snap = ((enriched.get("subject") or {}).get("enrichedSnapshot") or {})
    font_details = snap.get("fontDetails") or []
    if not font_details:
        return [FieldCheck("subject.enrichedSnapshot.fontDetails", "FAIL", "Discovery", "Empty fontDetails")]

    family_id = _norm(_dig(font_details[0], "family.id"))
    style_ids = {_norm(s.get("id")) for s in (font_details[0].get("styles") or []) if isinstance(s, dict)}

    discovery_family_ids: set[str] = set()
    for hit in style_hits:
        fam = hit.get("mtc_families_data") or hit.get("family") or {}
        if isinstance(fam, dict) and fam.get("id"):
            discovery_family_ids.add(_norm(fam.get("id")))

    if family_id and discovery_family_ids and family_id not in discovery_family_ids:
        checks.append(
            FieldCheck(
                "subject.enrichedSnapshot.fontDetails[0].family.id",
                "FAIL",
                "Discovery POST /v1/styles",
                f"family.id {family_id} not in discovery hits {sorted(discovery_family_ids)[:3]}",
            )
        )
    elif family_id:
        checks.append(
            FieldCheck(
                "subject.enrichedSnapshot.fontDetails[0].family.id",
                "PASS",
                "Discovery POST /v1/styles",
                f"Matched family {family_id}",
            )
        )

    var_md5s = {_norm(v.get("md5")) for v in variation_hits if isinstance(v, dict) and v.get("md5")}
    enriched_md5s = set()
    for fd in font_details:
        if not isinstance(fd, dict):
            continue
        for var in fd.get("variations") or []:
            if isinstance(var, dict) and var.get("md5"):
                enriched_md5s.add(_norm(var.get("md5")))

    if enriched_md5s and var_md5s:
        missing = enriched_md5s - var_md5s
        if missing:
            checks.append(
                FieldCheck(
                    "subject.enrichedSnapshot.fontDetails[].variations[].md5",
                    "FAIL",
                    "Discovery GET /v1/variations",
                    f"md5 not in discovery: {sorted(missing)[:3]}",
                )
            )
        else:
            checks.append(
                FieldCheck(
                    "subject.enrichedSnapshot.fontDetails[].variations[].md5",
                    "PASS",
                    "Discovery GET /v1/variations",
                    f"All {len(enriched_md5s)} md5(s) found",
                )
            )

    name_en = _norm(_dig(font_details[0], "family.catalog.name_en"))
    for hit in style_hits:
        fam = hit.get("mtc_families_data") or {}
        cat = fam.get("catalog") or fam.get("mtc_catalog_data") or {}
        if _norm(cat.get("name_en")).casefold() == name_en.casefold() and name_en:
            checks.append(
                FieldCheck(
                    "subject.enrichedSnapshot.fontDetails[0].family.catalog.name_en",
                    "PASS",
                    "Discovery",
                    name_en,
                )
            )
            break
    else:
        if name_en:
            checks.append(
                FieldCheck(
                    "subject.enrichedSnapshot.fontDetails[0].family.catalog.name_en",
                    "SKIP",
                    "Discovery",
                    "Could not cross-check name_en in style hits",
                )
            )

    if style_ids:
        hit_style_ids = {_norm(h.get("id") or h.get("style_id")) for h in style_hits}
        if style_ids <= hit_style_ids:
            checks.append(
                FieldCheck(
                    "subject.enrichedSnapshot.fontDetails[0].styles[].id",
                    "PASS",
                    "Discovery",
                    f"styles {sorted(style_ids)}",
                )
            )
        else:
            checks.append(
                FieldCheck(
                    "subject.enrichedSnapshot.fontDetails[0].styles[].id",
                    "FAIL",
                    "Discovery",
                    f"Missing styles {sorted(style_ids - hit_style_ids)}",
                )
            )

    return checks


def validate_create_role_ums(enriched: JsonDict, *, ums_role: dict[str, Any]) -> list[FieldCheck]:
    snap = ((enriched.get("subject") or {}).get("enrichedSnapshot") or {})
    role = snap.get("role") or {}
    checks: list[FieldCheck] = []

    for field in ("id", "displayName", "description"):
        ev = _norm(role.get(field))
        uv = _norm(ums_role.get(field))
        if ev and uv and values_equivalent(uv, ev, field_path=f"subject.enrichedSnapshot.role.{field}"):
            checks.append(FieldCheck(f"subject.enrichedSnapshot.role.{field}", "PASS", "UMS", ev))
        elif ev and uv:
            checks.append(
                FieldCheck(
                    f"subject.enrichedSnapshot.role.{field}",
                    "FAIL",
                    "UMS",
                    f"enriched={ev!r} ums={uv!r}",
                )
            )

    ums_perm_ids = {
        _norm(p.get("id"))
        for p in (ums_role.get("permissions") or [])
        if isinstance(p, dict)
    }
    enr_perm_ids = {
        _norm(p.get("id")) for p in (role.get("permissions") or []) if isinstance(p, dict)
    }
    if enr_perm_ids and ums_perm_ids:
        if enr_perm_ids <= ums_perm_ids:
            checks.append(
                FieldCheck(
                    "subject.enrichedSnapshot.role.permissions[].id",
                    "PASS",
                    "UMS",
                    f"{len(enr_perm_ids)} permission ids",
                )
            )
        else:
            checks.append(
                FieldCheck(
                    "subject.enrichedSnapshot.role.permissions[].id",
                    "FAIL",
                    "UMS",
                    f"extra in enriched: {sorted(enr_perm_ids - ums_perm_ids)[:5]}",
                )
            )
    return checks


def validate_actor_ums_cms(
    enriched: JsonDict,
    *,
    ums_profile: dict[str, Any] | None,
    cms_customer: dict[str, Any] | None,
) -> list[FieldCheck]:
    actor_snap = ((enriched.get("actor") or {}).get("enrichedSnapshot") or {})
    checks: list[FieldCheck] = []
    profile = ((actor_snap.get("user") or {}).get("profile") or {})
    customer = actor_snap.get("customer") or {}

    if ums_profile:
        for field in ("id", "email", "firstName", "lastName"):
            ev = _norm(profile.get(field))
            uv = _norm(ums_profile.get(field))
            if ev and uv and values_equivalent(uv, ev, field_path=f"actor.enrichedSnapshot.user.profile.{field}"):
                checks.append(
                    FieldCheck(f"actor.enrichedSnapshot.user.profile.{field}", "PASS", "UMS", ev)
                )
            elif ev and uv:
                checks.append(
                    FieldCheck(
                        f"actor.enrichedSnapshot.user.profile.{field}",
                        "FAIL",
                        "UMS",
                        f"enriched={ev!r} ums={uv!r}",
                    )
                )

    if cms_customer:
        for field in ("id", "name"):
            ev = _norm(customer.get(field))
            cv = _norm(cms_customer.get(field))
            if ev and cv and values_equivalent(cv, ev, field_path=f"actor.enrichedSnapshot.customer.{field}"):
                checks.append(
                    FieldCheck(f"actor.enrichedSnapshot.customer.{field}", "PASS", "CMS", ev)
                )
            elif ev and cv:
                checks.append(
                    FieldCheck(
                        f"actor.enrichedSnapshot.customer.{field}",
                        "FAIL",
                        "CMS",
                        f"enriched={ev!r} cms={cv!r}",
                    )
                )
        # displayName often differs slightly from name in CMS — informational only
        ev = _norm(customer.get("displayName"))
        cv = _norm(cms_customer.get("displayName") or cms_customer.get("name"))
        if ev and cv:
            status = (
                "PASS"
                if values_equivalent(cv, ev, field_path="actor.enrichedSnapshot.customer.displayName")
                else "SKIP"
            )
            checks.append(
                FieldCheck(
                    "actor.enrichedSnapshot.customer.displayName",
                    status,
                    "CMS",
                    f"enriched={ev!r} cms={cv!r}",
                )
            )
    return checks
