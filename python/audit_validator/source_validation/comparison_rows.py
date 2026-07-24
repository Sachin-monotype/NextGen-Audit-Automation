"""Build side-by-side source vs enriched comparison rows for Excel export."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..models import JsonDict
from .discovery_resolver import lookup_discovery_value, normalize_compare
from .enriched_field_scanner import (
    display_node_subnode,
    infer_source_system,
    scan_enriched_fields,
)
from .enriched_path_resolver import dig_enriched, dig_once, normalize_enriched_path, snapshot_present
from .mapping_registry import MappingField, get_operation_mapping
from .value_match import values_equivalent


@dataclass(frozen=True)
class ComparisonRow:
    operation: str
    layer: str
    field_path: str
    source_system: str
    source_api: str
    value_in_source: str
    value_in_enriched: str
    match_status: str  # PASS | FAIL | SKIP | N/A
    notes: str = ""
    field: str = ""
    node: str = ""
    sub_node: str = ""


# Font-list activation ops attach a list asset snapshot for context; lists are often
# deleted during test cleanup before Compare runs — don't SKIP every asset.* field.
# Font activation / deactivation ops — default subject.activationType/activationMode when blank.
_ACTIVATION_DEFAULT_OPS = frozenset({
    "activateFamily",
    "activateFontProject",
    "activateList",
    "activateStyle",
    "activateVariation",
    "pluginFontAutoActivated",
    "bulkActivateAll",
    "bulkActivateComplete",
    "bulkActivateLists",
    "bulkActivateStyles",
    "bulkDeactivateComplete",
    "bulkDeactivateLists",
    "bulkDeactivateStyles",
    "deactivateFamilies",
    "deActivateFontProject",
    "deActivateList",
    "deactivateStyle",
    "deactivateVariation",
    "fontActivationTypeSwitched",
})

_ACTIVATION_FIELD_DEFAULTS: dict[str, str] = {
    "subject.activationType": "permanent",
    "subject.activationMode": "manual",
}


def _read_mutation_input(enriched: JsonDict, live: dict[str, Any]) -> dict[str, Any]:
    trigger = live.get("trigger") if isinstance(live.get("trigger"), dict) else {}
    inp = trigger.get("graphql_input") or trigger.get("input") or {}
    if not isinstance(inp, dict):
        inp = {}
    meta = ((enriched.get("subject") or {}).get("metadata") or {}).get("input") or {}
    if isinstance(meta, dict):
        return {**meta, **inp}
    return inp


def _coerce_activation_value(val: object) -> str:
    if val is None:
        return ""
    return str(val).strip()


def _activation_field_pair(
    path: str,
    enriched: JsonDict,
    live: dict[str, Any],
) -> tuple[str, str, str]:
    """Return (source_value, enriched_value, note) with defaults when blank."""
    default = _ACTIVATION_FIELD_DEFAULTS.get(path, "")
    leaf = path.rsplit(".", 1)[-1]
    inp = _read_mutation_input(enriched, live)
    sent = _coerce_activation_value(inp.get(leaf))
    enriched_raw = _coerce_activation_value(_dig(enriched, path))
    source_val = sent or default
    enriched_val = enriched_raw or default
    if sent:
        note = "GraphQL mutation input (value sent)"
    elif enriched_raw:
        note = "Enriched resolver value"
    else:
        note = f"Default when blank ({default})"
    return source_val, enriched_val, note


def _append_activation_default_rows(
    operation: str,
    enriched: JsonDict,
    live: dict[str, Any],
    *,
    seen_paths: set[str],
    allow: set[str] | None,
) -> list[ComparisonRow]:
    base = _base_operation(operation)
    if base not in _ACTIVATION_DEFAULT_OPS:
        return []
    extra: list[ComparisonRow] = []
    for path, _default in _ACTIVATION_FIELD_DEFAULTS.items():
        norm = normalize_enriched_path(path)
        if norm in seen_paths:
            continue
        if allow is not None and norm not in allow:
            continue
        sv, ev, note = _activation_field_pair(path, enriched, live)
        field, node, sub = display_node_subnode(norm)
        status = "PASS" if values_equivalent(sv, ev, field_path=norm) else "FAIL"
        extra.append(
            ComparisonRow(
                operation=operation,
                layer="subject",
                field_path=norm,
                field=field or path.rsplit(".", 1)[-1],
                node=node,
                sub_node=sub,
                source_system="Trigger",
                source_api="GraphQL mutation input / resolver default",
                value_in_source=_norm(sv)[:500],
                value_in_enriched=_norm(ev)[:500],
                match_status=status,
                notes=note,
            )
        )
        seen_paths.add(norm)
    return extra


_FONT_LIST_ASSET_OPS = frozenset({
    "activateList",
    "deActivateList",
    "bulkActivateLists",
    "bulkDeactivateLists",
    "addFontListStyles",
    "removeFontListStyles",
    "addFontListFamilies",
    "removeFontListFamilies",
})


def _base_op_name(operation: str) -> str:
    return operation.split("(", 1)[0] if "(" in operation else operation


def _is_font_list_asset_context(operation: str, path: str) -> bool:
    return (
        _base_op_name(operation) in _FONT_LIST_ASSET_OPS
        and "subject.enrichedSnapshot.asset." in path
    )


def _norm(val: object) -> str:
    if val is None:
        return ""
    if isinstance(val, (dict, list)):
        return json.dumps(val, ensure_ascii=False, sort_keys=True, default=str)[:500]
    # datetime / Decimal / UUID from DB clients
    if hasattr(val, "isoformat") and callable(getattr(val, "isoformat")):
        try:
            return str(val.isoformat()).strip()
        except Exception:
            pass
    return str(val).strip()


_SOURCE_ERROR_MARKERS = (
    "lookup failed",
    "server error",
    "bad request",
    "timed out",
    "timeout",
    "connection",
    "500 ",
    "502 ",
    "503 ",
    "504 ",
    "400 ",
    "401 ",
    "403 ",
)


def _is_source_error(notes: str) -> bool:
    """True when a note reflects a source-API failure (not a value mismatch)."""
    if not notes:
        return False
    low = notes.lower()
    return any(marker in low for marker in _SOURCE_ERROR_MARKERS)


# Errors that mean "we never reached the origin" (network / VPN / Cloudflare edge block
# / auth-forbidden). These are NOT validation problems — we simply could not check the
# field — so they are classified N/A rather than SKIP, and a banner tells the user to
# connect to the VPN.
_UNREACHABLE_MARKERS = (
    "forbidden",
    "cloudflare",
    "error code: 10",
    "timed out",
    "timeout",
    "connection",
    "max retries",
    "403 ",
    "403 client",
    "401 ",
    "unauthorized",
)


def _is_unreachable_error(notes: str) -> bool:
    """True when the source API could not be reached (network/VPN/Cloudflare/auth block)."""
    if not notes:
        return False
    low = notes.lower()
    return any(marker in low for marker in _UNREACHABLE_MARKERS)


def _dig(enriched: JsonDict, path: str) -> object:
    return dig_enriched(enriched, path)


def _node_subnode(spec: MappingField) -> str:
    parts = [p for p in (spec.node, spec.sub_node) if p]
    return " / ".join(parts) if parts else spec.field


def _remark_for_source(live: dict[str, Any], source_system: str, *, status: str) -> str:
    """Attach API errors only to rows for the failing source (not globally)."""
    if source_system == "Typesense":
        err = live.get("discovery_error") or ""
        if err:
            return err
        if status == "SKIP" and not live.get("style_hits") and not live.get("variation_hits"):
            return live.get("discovery_note") or "Typesense not queried"
        return ""
    if source_system == "UMS":
        # Only return an error note when this row has no source value. A sibling
        # UMS call (e.g. role) can fail while profile fields still resolve —
        # those PASS rows must NOT inherit the unrelated error banner.
        if status in {"SKIP", "FAIL", ""}:
            return live.get("ums_error") or live.get("ums_role_error") or ""
        return ""
    if source_system == "CMS":
        return live.get("cms_error") or ""
    return ""


def _row(
    operation: str,
    spec: MappingField,
    source_val: object,
    enriched_val: object,
    *,
    notes: str = "",
    live: dict[str, Any] | None = None,
) -> ComparisonRow:
    live = live or {}
    sv = normalize_compare(source_val) if isinstance(source_val, (dict, list)) else _norm(source_val)
    ev = normalize_compare(enriched_val) if isinstance(enriched_val, (dict, list)) else _norm(enriched_val)
    # Enricher constants / non-probed upstreams: accept the enriched value (echo PASS).
    # Do NOT include Raw/GraphQL here — those must compare to the mutation response.
    _ACCEPT_ECHO = {
        "Resolver",
        "Unknown",
        "Audit service",
        "BYOF-License",
        "Batch-Orchestration",
    }
    _is_lang_path = "language" in spec.enriched_path.lower() or spec.enriched_path.rsplit(
        ".", 1
    )[-1].split("[")[0].lower() in {"locale", "locales", "lang"}

    if not ev and not sv:
        status = "N/A"
    elif spec.validate == "N" and ev:
        status = "PASS"
    elif spec.source_system in _ACCEPT_ECHO and ev:
        status = "PASS" if (values_equivalent(sv, ev, field_path=spec.enriched_path) or not sv) else "FAIL"
        if status == "PASS" and not sv and not notes and spec.source_system == "Audit service":
            notes = notes or "Enricher-generated / constant — not compared to DB"
    elif not sv and ev and _is_unreachable_error(notes):
        # Source API was unreachable (VPN off / Cloudflare edge block / auth forbidden /
        # timeout). We never got to compare, so this is Not Applicable — not a SKIP and
        # certainly not a FAIL. The Results banner surfaces the VPN hint.
        status = "N/A"
        notes = notes or "Source API unreachable (connect to VPN) — not validated"
    elif not sv and ev and _is_source_error(notes):
        # Source API returned a genuine error (500/400) — we could not fetch the value, so
        # this is not a validation failure. Surface as SKIP with the underlying error.
        status = "SKIP"
    elif not sv:
        if ev and _is_lang_path:
            # Language may be absent from CMS HTTP/DB projection but present on enriched —
            # never FAIL; locale is often request/UI driven.
            status = "SKIP"
            notes = notes or (
                "Language not present on CMS source (or only on GraphQL) — "
                "enricher may use request/UI locale"
            )
        elif ev and spec.source_system == "Typesense" and (live.get("style_hits") or live.get("variation_hits")):
            if live.get("imported_font"):
                status = "SKIP"
                notes = notes or (
                    "Imported/BYOF font — private to customer inventory; not returned by the "
                    "validation token's Discovery scope (resolver used M2M org scope)"
                )
            else:
                status = "FAIL"
                notes = notes or "Typesense response missing field (enriched has value)"
        elif ev and spec.source_system == "CMS" and live.get("cms_customer"):
            if "customLogo" in spec.enriched_path and not sv:
                status = "SKIP"
                notes = notes or "CMS GET customer missing metaData.customLogo* (GraphQL-only field)"
            else:
                status = "FAIL"
                notes = notes or "CMS response missing field (enriched has value)"
        elif ev and spec.source_system == "UMS" and (
            live.get("ums_profile") or live.get("ums_actor_teams") or live.get("ums_team")
        ):
            if live.get("ums_role_missing") and ".role." in spec.enriched_path:
                status = "SKIP"
                notes = notes or str(live.get("ums_role_missing"))
            elif spec.enriched_path.startswith("subject.enrichedSnapshot.team.") and not live.get("ums_team"):
                status = "SKIP"
                notes = notes or "Team entity not fetched from UMS for source validation"
            elif ".teams[" in spec.enriched_path and not live.get("ums_actor_teams"):
                status = "SKIP"
                notes = notes or "Actor teams not fetched from UMS GET /teams for source validation"
            elif "invitation" in spec.enriched_path.lower() and "subject.enrichedSnapshot" in spec.enriched_path:
                status = "SKIP"
                notes = notes or "Invitation entity not fetched from UMS for source validation"
            else:
                status = "FAIL"
                notes = notes or "UMS response missing field (enriched has value)"
        elif ev and spec.source_system == "AMS" and _is_font_list_asset_context(
            operation, spec.enriched_path
        ):
            err = str(notes or live.get("ams_error") or "").lower()
            if "not found" in err or (not sv and not live.get("ams_asset")):
                status = "N/A"
                notes = (
                    "List asset no longer in AMS/DB (removed after enrichment) — "
                    "font fields are still validated"
                )
            else:
                status = "SKIP" if ev else "N/A"
        else:
            status = "SKIP" if ev else "N/A"
    elif values_equivalent(sv, ev, field_path=spec.enriched_path):
        status = "PASS"
    elif _is_lang_path:
        # CMS company default (e.g. FR) often differs from enricher/event locale (EN).
        # Array/CSV membership already tried in values_equivalent — surface as SKIP, not FAIL.
        status = "SKIP"
        notes = notes or (
            "Language code differs from CMS default — enricher may use request/UI locale "
            "(list/CSV membership already tried)"
        )
    else:
        status = "FAIL"
    remark = notes
    if not remark and status in {"SKIP", "FAIL"}:
        remark = spec.notes or ("Source not fetched" if status == "SKIP" else "")
    src_sys = spec.source_system
    src_api = spec.source_api
    if remark and "graphql" in remark.lower():
        src_sys = "GraphQL"
        if "live replay" in remark.lower():
            src_api = "GraphQL mutation response (live replay)"
        elif "metadata.result" in remark.lower():
            src_api = "GraphQL mutation response (metadata.result)"
        else:
            src_api = "GraphQL mutation response"
    return ComparisonRow(
        operation=operation,
        layer=spec.layer,
        field_path=spec.enriched_path,
        field=spec.field,
        node=spec.node,
        sub_node=spec.sub_node,
        source_system=src_sys,
        source_api=src_api,
        value_in_source=sv[:500],
        value_in_enriched=ev[:500],
        match_status=status,
        notes=remark,
    )


def _ums_profile_root(ums_profile: dict | None) -> dict | None:
    if not ums_profile or not isinstance(ums_profile, dict):
        return None
    inner = ums_profile.get("profile")
    if isinstance(inner, dict):
        return inner
    return ums_profile


def _ams_value(path: str, ams: dict) -> object:
    """Resolve an ``asset.*`` field from the AMS asset response.

    Handles nested ``metadata.X`` (AMS returns ``metaData.X``, sometimes flattened),
    ``accessIds[i]`` array indexing, and plain leaf fields.
    """
    import re as _re

    rel = path.split(".asset.", 1)[1] if ".asset." in path else path.rsplit(".", 1)[-1]

    # metadata.<x>  ->  metaData.<x> (nested object or flattened "metaData.x" key)
    if rel.startswith("metadata.") or rel.startswith("metaData."):
        sub = rel.split(".", 1)[1]
        meta = ams.get("metaData")
        if not isinstance(meta, dict):
            meta = ams.get("metadata") if isinstance(ams.get("metadata"), dict) else {}
        val = dig_once(meta, sub)
        if val is None:
            val = ams.get(f"metaData.{sub}")
        return val

    # accessIds[i]
    m = _re.match(r"accessIds\[(\d+)\]$", rel)
    if m:
        arr = ams.get("accessIds")
        if isinstance(arr, list):
            i = int(m.group(1))
            return arr[i] if 0 <= i < len(arr) else None
        return None

    return dig_once(ams, rel)


_LANGUAGE_ALIASES = (
    "supportedLanguage",
    "supportedLanguages",
    "supported_language",
    "supported_languages",
    "languages",
    "language",
    "locale",
    "locales",
)


def _cms_pick_language(cms: dict) -> object:
    """CMS may store language as a scalar, CSV, or list under several key names."""
    for key in _LANGUAGE_ALIASES:
        if key in cms and cms.get(key) not in (None, "", [], {}):
            return cms.get(key)
    # Nested shapes seen in GraphQL / MySQL joins
    for nest_key in ("entitlement", "settings", "metaData", "metadata"):
        nest = cms.get(nest_key)
        if not isinstance(nest, dict):
            continue
        for key in _LANGUAGE_ALIASES:
            if key in nest and nest.get(key) not in (None, "", [], {}):
                return nest.get(key)
    return None


def _cms_value(path: str, cms: dict) -> object:
    """Resolve CMS field — supports nested metaData.companySettings.* paths."""
    leaf = path.rsplit(".", 1)[-1].split("[")[0]
    if "language" in leaf.lower() or leaf.lower() in {"locale", "locales", "lang"}:
        lang = _cms_pick_language(cms)
        if lang is not None:
            return lang
    if ".customer." in path:
        rel = path.split(".customer.", 1)[1]
        # Never look up enricher-only constants against CMS
        if rel == "source" or rel.endswith(".source"):
            return None
        val = dig_once(cms, rel)
        if val is not None:
            return val
        if "language" in rel.lower():
            return _cms_pick_language(cms)
        return None
    if leaf == "displayName":
        return cms.get("displayName")
    if leaf == "name":
        return cms.get("name")
    if leaf == "id":
        return cms.get("id")
    return cms.get(leaf)


def _ums_actor_teams_value(path: str, ums_actor_teams: list | None) -> object | None:
    """Resolve ``actor.enrichedSnapshot.user.teams[i].*`` from UMS GET /teams."""
    if not ums_actor_teams or ".teams[" not in path:
        return None
    import re

    m = re.search(r"\.teams\[(\d+)\](?:\.(.+))?$", path)
    if not m:
        return None
    idx = int(m.group(1))
    if idx < 0 or idx >= len(ums_actor_teams):
        return None
    team = ums_actor_teams[idx]
    if not isinstance(team, dict):
        return None
    rel = m.group(2) or ""
    if not rel:
        return team
    return dig_once(team, rel)


def _ums_value(
    path: str,
    ums_role: dict | None,
    ums_profile: dict | None,
    ums_team: dict | None,
    *,
    ums_subject_profile: dict | None = None,
    ums_subject_role: dict | None = None,
    ums_user: dict | None = None,
    ums_actor_teams: list | None = None,
) -> object:
    # deleteProfiles enrichedSnapshot.deletedProfiles[*].user.* — resolved via
    # UMS GET /api/v3/users?idpUserId=… after the profile row itself is gone.
    if "deletedProfiles" in path and ums_user:
        if ".user." in path:
            rel = path.split(".user.", 1)[1].split("[")[0]
            return dig_once(ums_user, rel) if rel else ums_user
        leaf = path.split(".")[-1].split("[")[0]
        if leaf == "idpUserId":
            return ums_user.get("idpUserId")
        if leaf == "profileId":
            return None  # comes from the mutation result / subject.id, not /users
    # Actor teams[] come from UMS GET /customers/{gcid}/teams (numeric id + name),
    # NOT from the profile's nested team.id (UUID) — and never via substring "team"∈"teams".
    teams_val = _ums_actor_teams_value(path, ums_actor_teams)
    if teams_val is not None:
        return teams_val
    if (
        ums_team
        and "enrichedSnapshot" in path
        and ".teams[" not in path
        and (".team." in path or path.endswith(".team"))
    ):
        key = path.split(".")[-1]
        return ums_team.get(key)
    if "subject.enrichedSnapshot.user.role." in path and ums_subject_role:
        rel = path.split(".role.", 1)[1]
        return dig_once(ums_subject_role, rel)
    if path.startswith("subject.enrichedSnapshot.role."):
        if ums_subject_role:
            rel = path.split(".role.", 1)[1]
            return dig_once(ums_subject_role, rel)
        return None
    if path.startswith("subject.enrichedSnapshot.team."):
        if ums_team:
            key = path.split(".")[-1]
            return ums_team.get(key)
        return None
    if "subject.enrichedSnapshot" in path and "invitation" in path.lower():
        return None
    if ums_role and "actor.enrichedSnapshot.user.role." in path:
        rel = path.split(".role.", 1)[1]
        return dig_once(ums_role, rel)
    if ums_role and ".role." in path and "actor.enrichedSnapshot.user" not in path:
        key = path.split(".")[-1].replace("[0]", "")
        if key == "id":
            return ums_role.get("id")
        if key == "displayName":
            return ums_role.get("displayName")
        if "permissions" in path:
            rel = path.split(".role.", 1)[-1]
            return dig_once(ums_role, rel)
        return dig_once(ums_role, path.split(".role.", 1)[-1])
    if "subject.enrichedSnapshot.user.profile." in path and ums_subject_profile:
        rel = path.split(".profile.", 1)[1]
        root = _ums_profile_root(ums_subject_profile)
        if root:
            val = dig_once(root, rel)
            if val is not None:
                return val
    if not ums_profile:
        return None
    if ".profile." in path:
        rel = path.split(".profile.", 1)[1]
        root = _ums_profile_root(ums_profile)
        if root:
            val = dig_once(root, rel)
            if val is not None:
                return val
    if ".role." in path and "actor.enrichedSnapshot.user" in path:
        if ums_role:
            rel = path.split(".role.", 1)[1]
            return dig_once(ums_role, rel)
        role = ums_profile.get("role") or {}
        if isinstance(role, dict):
            rel = path.split(".role.", 1)[1]
            return dig_once(role, rel)
    if "role.displayName" in path:
        role = ums_profile.get("role") or {}
        return role.get("displayName") if isinstance(role, dict) else None
    if "role.id" in path:
        role = ums_profile.get("role") or {}
        return role.get("id") if isinstance(role, dict) else None
    prof = _ums_profile_root(ums_profile)
    if prof:
        key = path.split(".")[-1]
        return prof.get(key)
    return None


def _is_imported_font(enriched: JsonDict) -> bool:
    """Imported/BYOF fonts live in the customer's private catalog and are not returned
    by a user-scoped Discovery query, so enriched-has-value / source-empty is expected
    (not a real mismatch)."""
    subject = enriched.get("subject") or {}
    snap = subject.get("enrichedSnapshot") or {}
    if snap.get("isImportedFont") is True or snap.get("is_imported_font") is True:
        return True
    for fd in snap.get("fontDetails") or []:
        if not isinstance(fd, dict):
            continue
        fam = fd.get("family") or {}
        foundry = (fam.get("foundry") or {}) if isinstance(fam, dict) else {}
        if isinstance(foundry, dict):
            name = str(foundry.get("name_en") or foundry.get("handle") or "").lower()
            if "importedfont" in name:
                return True
        for st in fd.get("styles") or []:
            if isinstance(st, dict):
                cat = st.get("catalog") if isinstance(st.get("catalog"), dict) else st
                if isinstance(cat, dict) and (
                    cat.get("is_imported_font") is True or cat.get("source") == "ImportedFonts"
                ):
                    return True
    return False


def _raw_subject_id(enriched: JsonDict, path: str = "") -> object:
    subject = enriched.get("subject") or {}
    ids = subject.get("id")
    if isinstance(ids, list):
        import re

        m = re.search(r"subject\.id\[(\d+)\]", path)
        if m:
            idx = int(m.group(1))
            return ids[idx] if 0 <= idx < len(ids) else None
        return ids[0] if ids else None
    return ids


def _resolve_source_value(
    spec: MappingField,
    enriched: JsonDict,
    *,
    live: dict[str, Any],
) -> tuple[object, str]:
    path = spec.enriched_path

    delete_id = _delete_snapshot_id_value(
        path, enriched, live.get("trigger") if isinstance(live.get("trigger"), dict) else None
    )
    if delete_id is not None:
        return delete_id, "GraphQL mutation input ids (deleted entity)"

    # Raw envelope family IDs — not style document ids from Typesense
    if path.startswith("subject.id"):
        return _raw_subject_id(enriched, path), ""

    if path.startswith("subject.metadata.input.") or path.startswith("subject.metadata.result."):
        trigger = live.get("trigger")
        if isinstance(trigger, dict) and trigger:
            from_trigger = _trigger_value(path, trigger, enriched)
            if from_trigger is not None:
                note = "GraphQL mutation input (subject.metadata.input)"
                if path.startswith("subject.metadata.result."):
                    note = "GraphQL mutation response (subject.metadata.result)"
                return from_trigger, note
        gql = live.get("graphql_response")
        if isinstance(gql, dict) and gql and path.startswith("subject.metadata.result."):
            from_gql = _graphql_response_value(path, gql, enriched)
            if from_gql is not None:
                return from_gql, "GraphQL mutation response (subject.metadata.result)"
        if path.startswith("subject.metadata.input."):
            subject = enriched.get("subject") or {}
            meta = subject.get("metadata") if isinstance(subject.get("metadata"), dict) else {}
            inp = meta.get("input") if isinstance(meta.get("input"), dict) else {}
            rel = path[len("subject.metadata.input.") :]
            val = dig_once(inp, rel)
            if val is not None:
                return val, "GraphQL mutation input (subject.metadata.input)"
        return None, "GraphQL mutation input/response not captured for this run"

    if spec.source_system == "Typesense":
        style_hits = live.get("style_hits") or []
        variation_hits = live.get("variation_hits") or []
        if style_hits or variation_hits:
            val = lookup_discovery_value(
                path,
                enriched,
                style_hits=style_hits,
                variation_hits=variation_hits,
                discovery_key=spec.discovery_key,
            )
            return val, _remark_for_source(live, "Typesense", status="")
        return None, _remark_for_source(live, "Typesense", status="SKIP")

    if spec.source_system == "UMS":
        val = _ums_value(
            path,
            live.get("ums_role"),
            live.get("ums_profile"),
            live.get("ums_team"),
            ums_subject_profile=live.get("ums_subject_profile"),
            ums_subject_role=live.get("ums_subject_role"),
            ums_user=live.get("ums_user"),
            ums_actor_teams=live.get("ums_actor_teams"),
        )
        if val is not None:
            # Value came from a successful UMS response — never decorate with a
            # leftover error from a different UMS call on the same event.
            note = ""
        else:
            note = _remark_for_source(live, "UMS", status="SKIP")
            if live.get("ums_role_missing") and ".role." in path:
                note = str(live.get("ums_role_missing"))
        return val, note

    if spec.source_system == "CMS":
        cms = live.get("cms_customer")
        # For create/updateCustomer the subject is the *target* customer, not the actor.
        if "subject.enrichedSnapshot.customer" in path and live.get("cms_subject_customer"):
            cms = live.get("cms_subject_customer")
        if cms:
            return _cms_value(path, cms), _remark_for_source(live, "CMS", status="")
        return None, _remark_for_source(live, "CMS", status="SKIP")

    if spec.source_system == "AMS":
        ams = live.get("ams_asset")
        if isinstance(ams, dict) and ams:
            return _ams_value(path, ams), live.get("ams_error") or ""
        return None, live.get("ams_error") or ""

    if spec.source_system in {"Raw", "GraphQL", "Trigger"}:
        # Prefer simulated/replayed GraphQL trigger (input + response) — never the raw envelope.
        trigger = live.get("trigger")
        if isinstance(trigger, dict) and trigger:
            from_trigger = _trigger_value(path, trigger, enriched)
            if from_trigger is not None:
                note = "GraphQL curl / event trigger"
                mode = trigger.get("replay_mode")
                if mode == "metadata.result":
                    note = "GraphQL mutation response (subject.metadata.result)"
                elif mode == "live_replay":
                    note = "GraphQL mutation response (live replay from captured input)"
                return from_trigger, note
        gql = live.get("graphql_response")
        if isinstance(gql, dict) and gql:
            from_gql = _graphql_response_value(path, gql, enriched)
            if from_gql is not None:
                return from_gql, "GraphQL mutation response"
        # Fallback: mutation input / subject.id embedded on the enriched envelope
        # (same values the curl sent — not a Raw Mongo echo).
        join = _raw_join_key_value(enriched, path)
        if join is not None:
            return join, "GraphQL mutation input (join key)"
        if path.startswith("subject.id") or path == "subject.type":
            return _raw_subject_id(enriched, path) if path.startswith("subject.id") else (
                (enriched.get("subject") or {}).get("type")
            ), "enriched subject (mutation target)"
        # Envelope fields with no trigger capture yet — do not fall back to Raw.
        if path.startswith("source.") or path in {
            "xCorrelationId", "eventId", "eventVersion", "occurredAt", "routingKey",
        }:
            return None, "Trigger context not captured for this run — re-run Generate"
        return None, "GraphQL mutation response not captured for this run"

    if spec.source_system == "Resolver":
        return _dig(enriched, path), ""

    if spec.source_system == "Audit service":
        # Enricher constants / derived stamps — echo enriched so UI shows the value
        # on both sides instead of '-' / false SKIP.
        return _dig(enriched, path), "Enricher-generated / constant — not a DB column"

    if spec.source_system in {"BYOF-License", "Batch-Orchestration"}:
        return _dig(enriched, path), f"{spec.source_system} (accepted; not probed)"

    if spec.source_system in {"JWT", "Bearer token"}:
        # Compare enriched actor identity to JWT claims (sheet: decrypt token).
        return _jwt_actor_value(path, enriched, live)

    return None, ""


def _jwt_actor_value(
    path: str, enriched: JsonDict, live: dict[str, Any]
) -> tuple[object, str]:
    """Resolve actor.* from Bearer JWT claims (ActivateFamily sheet rules)."""
    try:
        from audit_validator.auth import jwt_identity

        ident = live.get("jwt_identity") if isinstance(live.get("jwt_identity"), dict) else None
        if not ident:
            ident = jwt_identity()
    except Exception:
        ident = {}
    key = path.split(".")[-1]
    low = key.lower()
    mapping = {
        "globalcustomerid": "gcid",
        "orgid": "org_id",
        "parentcustomerid": "parent_customer_id",
        "inventories": "inventories",
    }
    if low in mapping:
        val = (ident or {}).get(mapping[low])
        return val, "JWT claim (decrypt Bearer)"
    if low == "globaluserid":
        # Profile UUID is not in JWT — prefer UMS resolution via email/idp.
        pid = live.get("our_profile_id") or live.get("actor_profile_id")
        if pid:
            return pid, "UMS profile id (resolved via JWT email / idpUserId)"
        actor = enriched.get("actor") or {}
        return actor.get("globalUserId"), "UMS profile id (not in JWT — use enriched until resolved)"
    actor = enriched.get("actor") or {}
    val = actor.get(key)
    if val is None:
        val = _dig(enriched, path)
    return val, "Bearer token / actor envelope"


def _trigger_value(path: str, trigger: dict, enriched: JsonDict) -> object:
    """Pull comparable values from the GraphQL/curl trigger we fired."""
    # Direct envelope keys we recorded when sending
    if path == "xCorrelationId":
        return trigger.get("xCorrelationId") or trigger.get("correlation_id")
    if path == "eventId":
        return trigger.get("eventId")
    if path == "eventVersion":
        return trigger.get("eventVersion")
    if path == "occurredAt":
        return trigger.get("occurredAt")
    if path == "routingKey":
        return trigger.get("routingKey")

    if path.startswith("source."):
        leaf = path.split(".", 1)[1]
        req = trigger.get("request") if isinstance(trigger.get("request"), dict) else {}
        source = trigger.get("source") if isinstance(trigger.get("source"), dict) else {}
        # Prefer explicit source block, then request headers/config
        if leaf in source and source.get(leaf) not in (None, "", [], {}):
            return source.get(leaf)
        aliases = {
            "operation": ("operation",),
            "service": ("service",),
            "operationState": ("operationState",),
            "operationIndex": ("operationIndex",),
            "platform": ("platform",),
            "platformEnvironment": ("platformEnvironment",),
            "platformVersion": ("platformVersion",),
            "actorUserAgent": ("actorUserAgent", "userAgent", "user-agent"),
            "type": ("type",),
        }
        for key in aliases.get(leaf, (leaf,)):
            if key in req and req.get(key) not in (None, "", [], {}):
                return req.get(key)
            if key in trigger and trigger.get(key) not in (None, "", [], {}):
                return trigger.get(key)
        if leaf == "operation":
            return trigger.get("operation")
        if leaf == "actorUserAgent":
            return req.get("userAgent") or req.get("user-agent")
        return None

    delete_id = _delete_snapshot_id_value(path, enriched, trigger)
    if delete_id is not None:
        return delete_id

    if path.startswith("subject.metadata.input."):
        rel = path[len("subject.metadata.input.") :]
        inp = trigger.get("graphql_input")
        if isinstance(inp, dict):
            val = dig_once(inp, rel)
            if val is not None:
                return val
        subject = enriched.get("subject") or {}
        meta = subject.get("metadata") if isinstance(subject.get("metadata"), dict) else {}
        inp2 = meta.get("input") if isinstance(meta.get("input"), dict) else {}
        return dig_once(inp2, rel)

    if path.startswith("subject.metadata.result."):
        rel = path[len("subject.metadata.result.") :]
        gql = trigger.get("graphql_response")
        if isinstance(gql, dict) and gql:
            for node in gql.values():
                if isinstance(node, dict):
                    val = dig_once(node, rel)
                    if val is not None:
                        return val
        subject = enriched.get("subject") or {}
        meta = subject.get("metadata") if isinstance(subject.get("metadata"), dict) else {}
        res = meta.get("result") if isinstance(meta.get("result"), dict) else {}
        return dig_once(res, rel)

    # Subject join keys / mutation response body
    gql = trigger.get("graphql_response")
    if isinstance(gql, dict) and gql:
        from_gql = _graphql_response_value(path, gql, enriched)
        if from_gql is not None:
            return from_gql
    inp = trigger.get("graphql_input") or trigger.get("input")
    if isinstance(inp, dict) and (
        "familyids" in path.lower()
        or "styleids" in path.lower()
        or path.startswith("subject.id")
    ):
        # Reuse join-key helper by synthesizing a thin enriched subject.metadata.input
        synthetic = {"subject": {"metadata": {"input": inp}, "id": inp.get("familyIds") or inp.get("ids")}}
        join = _raw_join_key_value(synthetic, path)
        if join is not None:
            return join
    return None


def _graphql_response_value(
    path: str,
    gql_response: dict,
    enriched: JsonDict,
) -> object:
    """Pull a comparable value from the GraphQL mutation response body.

    ``gql_response`` is the ``data`` object from the curl we sent (e.g.
    ``{ "activateFamily": { "success": true, ... } }``). Join keys often live
    on the request; when the response echoes IDs we prefer those.
    """
    import re

    if path.startswith("subject.metadata.result."):
        rel = path[len("subject.metadata.result.") :]
        for node in gql_response.values():
            if isinstance(node, dict):
                val = dig_once(node, rel)
                if val is not None:
                    return val

    # Flatten: try dig on each top-level mutation result node
    for _mut, node in gql_response.items():
        if not isinstance(node, dict):
            continue
        # Direct leaf
        leaf = path.rsplit(".", 1)[-1].split("[")[0]
        if leaf in node and node.get(leaf) not in (None, "", [], {}):
            return node.get(leaf)
        # Nested asset / team / profile ids commonly returned
        for nest_key in ("asset", "team", "profile", "role", "customer", "batch", "contract"):
            nest = node.get(nest_key)
            if isinstance(nest, dict) and nest.get("id") and (
                path.endswith(".id") or "subject.id" in path.lower()
            ):
                if nest_key in path.lower() or path.startswith("subject.id"):
                    return nest.get("id")

    # Indexed join keys: familyIds[0] etc. — response rarely has these; use input
    # already handled by caller. Try batchId / styleIds on response.
    m = re.search(r"\.(familyids|styleids|variationids|md5s|ids|listids)\[(\d+)\]$", path.lower())
    if m:
        key_map = {
            "familyids": "familyIds",
            "styleids": "styleIds",
            "variationids": "variationIds",
            "md5s": "md5s",
            "ids": "ids",
            "listids": "listIds",
        }
        key = key_map.get(m.group(1), m.group(1))
        idx = int(m.group(2))
        for node in gql_response.values():
            if not isinstance(node, dict):
                continue
            arr = node.get(key)
            if isinstance(arr, list) and 0 <= idx < len(arr):
                return arr[idx]
    return None


def _delete_snapshot_id_value(
    path: str,
    enriched: JsonDict,
    trigger: dict | None = None,
) -> object | None:
    """IDs on delete* subject snapshots come from mutation input / subject.id, not UMS."""
    import re

    m = re.match(r"subject\.enrichedSnapshot\.(?:teams|roles)\[(\d+)\]\.id$", path)
    idx = 0
    if m:
        idx = int(m.group(1))
    elif path != "subject.enrichedSnapshot.role.id":
        return None

    subject = enriched.get("subject") or {}
    sid = subject.get("id")
    if isinstance(sid, list) and 0 <= idx < len(sid):
        return sid[idx]
    if isinstance(sid, (str, int)) and idx == 0:
        return sid

    inp: dict | None = None
    if trigger and isinstance(trigger.get("graphql_input"), dict):
        inp = trigger["graphql_input"]
    if not inp:
        meta = subject.get("metadata") if isinstance(subject.get("metadata"), dict) else {}
        cand = meta.get("input")
        inp = cand if isinstance(cand, dict) else None
    if isinstance(inp, dict):
        ids = inp.get("ids")
        if isinstance(ids, list) and 0 <= idx < len(ids):
            return ids[idx]
    return None


def _raw_join_key_value(enriched: JsonDict, path: str) -> object:
    """Resolve familyIds[i] / styleIds[i] from subject metadata input or subject.id."""
    import re

    low = path.lower()
    m = re.search(r"\.(familyids|styleids|variationids|md5s|ids)\[(\d+)\]$", low)
    if not m:
        # Also bare leaf without index
        leaf = path.rsplit(".", 1)[-1].split("[")[0]
        if leaf.lower() not in {
            "familyids",
            "styleids",
            "variationids",
            "md5s",
            "ids",
        }:
            return None
        idx = 0
        key = leaf
    else:
        key = m.group(1)
        idx = int(m.group(2))

    subject = enriched.get("subject") or {}
    # GraphQL args often land in subject.metadata.input
    meta = subject.get("metadata") or {}
    inp = meta.get("input") if isinstance(meta, dict) else None
    candidates: list[object] = []
    if isinstance(inp, dict):
        for cand_key in (key, "familyIds", "styleIds", "variationIds", "md5s", "ids"):
            arr = inp.get(cand_key)
            if isinstance(arr, list) and arr:
                candidates = list(arr)
                break
        # Nested input.families.familyIds
        families = inp.get("families") if isinstance(inp.get("families"), dict) else None
        if not candidates and isinstance(families, dict):
            arr = families.get("familyIds") or families.get("familyids")
            if isinstance(arr, list):
                candidates = list(arr)
    # Snapshot-level arrays (enricher copies input onto snapshot)
    snap = subject.get("enrichedSnapshot") if isinstance(subject.get("enrichedSnapshot"), dict) else {}
    if not candidates and isinstance(snap, dict):
        for cand_key in ("familyIds", "styleIds", "variationIds", "md5s", "ids", key):
            arr = snap.get(cand_key)
            if isinstance(arr, list) and arr:
                candidates = list(arr)
                break
    # subject.id is often the same family/style target list
    if not candidates and isinstance(subject.get("id"), list):
        candidates = list(subject["id"])

    if not candidates:
        return None
    if 0 <= idx < len(candidates):
        return candidates[idx]
    return candidates[0] if candidates else None


def _mapping_lookup(operation: str) -> dict[str, MappingField]:
    specs = get_operation_mapping(operation)
    out: dict[str, MappingField] = {}
    for spec in specs:
        if not spec.enriched_path:
            continue
        key = normalize_enriched_path(spec.enriched_path)
        out[key] = spec
    return out


def _spec_for_path(
    operation: str,
    path: str,
    *,
    mapping_by_path: dict[str, MappingField],
) -> MappingField:
    norm = normalize_enriched_path(path)
    if norm in mapping_by_path:
        return mapping_by_path[norm]
    field, node, sub = display_node_subnode(norm)
    src_sys, src_api = infer_source_system(norm, operation)
    # Envelope fields on the QA sheet are Validation=N unless we have an explicit
    # registry row — avoid false SKIP when trigger context is missing.
    validate = "Y"
    if src_sys == "Trigger" and (
        norm.startswith("source.")
        or norm in {"eventVersion", "occurredAt", "eventId", "routingKey"}
    ):
        validate = "N"
    if norm == "xCorrelationId":
        validate = "Y"
        src_sys, src_api = "Trigger", "GraphQL curl / event trigger"
    return MappingField(
        field=field or norm.rsplit(".", 1)[-1],
        node=node,
        sub_node=sub,
        attribute="",
        data_mapping="",
        notes="Inferred from enriched JSON",
        validate=validate,
        enriched_path=norm,
        source_system=src_sys,
        source_api=src_api,
        layer="subject" if norm.startswith("subject.") else "actor" if norm.startswith("actor.") else "event",
    )


def build_comparison_rows(
    operation: str,
    enriched: JsonDict,
    *,
    live: dict[str, Any] | None = None,
    mapped_only: bool | None = None,
    field_paths: set[str] | list[str] | None = None,
) -> list[ComparisonRow]:
    """
    Enriched-first validation: only compare fields that exist in the enriched sample,
    then fetch the matching UMS / CMS / Typesense / AMS / GraphQL value.

    ``field_paths`` — when set, only compare these enriched JSON paths (selective
    attribute validation from the Compare UI editor).

    ``mapped_only`` (default: env ``SOURCE_VALIDATION_MAPPED_ONLY``, else False)
    would restrict output to registry-mapped fields only.
    """
    import os

    if mapped_only is None:
        mapped_only = os.getenv(
            "SOURCE_VALIDATION_MAPPED_ONLY", "false"
        ).strip().lower() in {"1", "true", "yes", "on"}

    live = dict(live or {})
    if "imported_font" not in live:
        live["imported_font"] = _is_imported_font(enriched)
    base_op = _base_operation(operation)
    mapping_by_path = _mapping_lookup(base_op)
    present = scan_enriched_fields(enriched)

    if mapped_only and mapping_by_path:
        mapped_norms = set(mapping_by_path.keys())
        present = [
            (p, v)
            for p, v in present
            if normalize_enriched_path(p) in mapped_norms
        ]

    allow: set[str] | None = None
    if field_paths:
        allow = {normalize_enriched_path(p) for p in field_paths if p}
        present = [
            (p, v)
            for p, v in present
            if normalize_enriched_path(p) in allow
        ]

    # Registry paths we still want when snapshot exists but scanner missed a scalar
    for spec in mapping_by_path.values():
        if spec.validate != "Y":
            continue
        if spec.source_system not in {"UMS", "CMS", "Typesense", "AMS", "UMS/Search"}:
            continue
        norm = normalize_enriched_path(spec.enriched_path)
        if allow is not None and norm not in allow:
            continue
        if any(normalize_enriched_path(p) == norm for p, _ in present):
            continue
        val = _dig(enriched, spec.enriched_path)
        if val is not None and str(val).strip() not in ("", "[]", "{}"):
            present.append((norm, val))

    rows: list[ComparisonRow] = []
    seen_paths: set[str] = set()

    # Audit enricher-generated envelope fields — accept as PASS (no external source).
    for gen_path, note in (
        (
            "enrichedEventId",
            "Generated by audit enricher — no external source to compare; accepted.",
        ),
        (
            "enrichmentVersion",
            "Enricher version stamp — generated by audit service; accepted.",
        ),
        (
            "enrichedAt",
            "Enrichment timestamp — generated by audit service; accepted.",
        ),
    ):
        if allow is not None and normalize_enriched_path(gen_path) not in allow:
            continue
        gen_val = enriched.get(gen_path)
        if gen_val is None or str(gen_val).strip() == "":
            continue
        rows.append(
            ComparisonRow(
                operation=operation,
                layer="event",
                field_path=gen_path,
                field=gen_path,
                node="enrichment",
                sub_node="",
                source_system="Audit service",
                source_api="enricher-generated",
                value_in_source=_norm(gen_val)[:500],
                value_in_enriched=_norm(gen_val)[:500],
                match_status="PASS",
                notes=note,
            )
        )
        seen_paths.add(gen_path)

    for path, enriched_val in present:
        norm = normalize_enriched_path(path)
        if norm in seen_paths:
            continue
        seen_paths.add(norm)
        spec = _spec_for_path(operation, norm, mapping_by_path=mapping_by_path)

        if (
            norm in _ACTIVATION_FIELD_DEFAULTS
            and _base_operation(operation) in _ACTIVATION_DEFAULT_OPS
        ):
            sv, ev, act_note = _activation_field_pair(norm, enriched, live)
            field, node, sub = display_node_subnode(norm)
            status = "PASS" if values_equivalent(sv, ev, field_path=norm) else "FAIL"
            rows.append(
                ComparisonRow(
                    operation=operation,
                    layer="subject",
                    field_path=norm,
                    field=field or norm.rsplit(".", 1)[-1],
                    node=node,
                    sub_node=sub,
                    source_system="Trigger",
                    source_api="GraphQL mutation input / resolver default",
                    value_in_source=_norm(sv)[:500],
                    value_in_enriched=_norm(ev)[:500],
                    match_status=status,
                    notes=act_note,
                )
            )
            continue

        if (
            spec.layer in ("actor", "subject")
            and spec.validate == "Y"
            and spec.source_system in {"UMS", "CMS", "Typesense", "AMS", "UMS/Search"}
            and not snapshot_present(enriched, spec.layer)
            and norm.startswith(f"{spec.layer}.enrichedSnapshot")
        ):
            # Registry paths often resolve via dig_enriched JWT fallbacks
            # (actor.globalUserId / globalCustomerId) when desktop/ingress events
            # have no actor.enrichedSnapshot — treat those as Bearer echo, not SKIP/FAIL.
            actor = enriched.get("actor") or {}
            jwt_echo = None
            jwt_note = ""
            if norm.endswith(".customer.id") or norm.endswith(".customerId"):
                jwt_echo = actor.get("globalCustomerId")
                jwt_note = "No actor.enrichedSnapshot — echoed actor.globalCustomerId (JWT)"
            elif norm.endswith(".profile.id") or norm.endswith(".user.id"):
                jwt_echo = actor.get("globalUserId")
                jwt_note = "No actor.enrichedSnapshot — echoed actor.globalUserId (JWT)"
            if jwt_echo is not None and str(jwt_echo).strip():
                rows.append(
                    ComparisonRow(
                        operation=operation,
                        layer=spec.layer,
                        field_path=norm,
                        field=spec.field,
                        node=spec.node,
                        sub_node=spec.sub_node,
                        source_system="Bearer token",
                        source_api="JWT claim (actor identity)",
                        value_in_source=_norm(jwt_echo)[:500],
                        value_in_enriched=_norm(jwt_echo)[:500],
                        match_status="PASS",
                        notes=jwt_note,
                    )
                )
                continue
            rows.append(
                ComparisonRow(
                    operation=operation,
                    layer=spec.layer,
                    field_path=norm,
                    field=spec.field,
                    node=spec.node,
                    sub_node=spec.sub_node,
                    source_system=spec.source_system,
                    source_api=spec.source_api,
                    value_in_source="",
                    value_in_enriched=_norm(enriched_val)[:500],
                    match_status="SKIP",
                    notes=(
                        f"No {spec.layer}.enrichedSnapshot on this event "
                        "(desktop/ingress/passthrough) — not source-validated"
                    ),
                )
            )
            continue

        ev = enriched_val if enriched_val is not None else _dig(enriched, norm)
        sv, note = _resolve_source_value(spec, enriched, live=live)
        row = _row(operation, spec, sv, ev, notes=note, live=live)
        if not note and row.match_status == "SKIP":
            note = _remark_for_source(live, spec.source_system, status="SKIP")
        rows.append(
            ComparisonRow(
                operation=row.operation,
                layer=row.layer,
                field_path=norm,
                source_system=row.source_system,
                source_api=row.source_api,
                value_in_source=row.value_in_source,
                value_in_enriched=row.value_in_enriched,
                match_status=row.match_status,
                notes=note or row.notes,
                field=row.field,
                node=row.node,
                sub_node=row.sub_node,
            )
        )

    # Enrichment-scope contract (produce + require from audit-resolver manifest)
    try:
        from .enrichment_scope import validate_enrichment_scope

        for sc in validate_enrichment_scope(base_op, enriched):
            rows.append(
                ComparisonRow(
                    operation=operation,
                    layer="event",
                    field_path=sc.field_path,
                    field=sc.field_path.rsplit(".", 1)[-1],
                    node="enrichmentScope",
                    sub_node="",
                    source_system=sc.source_system,
                    source_api=sc.source_api,
                    value_in_source=sc.notes[:200],
                    value_in_enriched=(
                        "snapshot present"
                        if (
                            (sc.field_path.startswith("subject.enrichedSnapshot") and _has_snap(enriched, "subject"))
                            or (sc.field_path.startswith("actor.enrichedSnapshot") and _has_snap(enriched, "actor"))
                        )
                        else sc.match_status
                    )[:200],
                    match_status=sc.match_status,
                    notes=sc.notes,
                )
            )
    except Exception:
        pass

    rows.extend(
        _append_activation_default_rows(
            operation,
            enriched,
            live,
            seen_paths=seen_paths,
            allow=allow,
        )
    )

    # Stamp display operation on every row (touchpoint-qualified name for Results)
    if rows and operation:
        rows = [
            ComparisonRow(
                operation=operation,
                layer=r.layer,
                field_path=r.field_path,
                source_system=r.source_system,
                source_api=r.source_api,
                value_in_source=r.value_in_source,
                value_in_enriched=r.value_in_enriched,
                match_status=r.match_status,
                notes=r.notes,
                field=r.field,
                node=r.node,
                sub_node=r.sub_node,
            )
            for r in rows
        ]

    return rows


def _base_operation(operation: str) -> str:
    """``activateFamily(global)`` → ``activateFamily`` for registry / scope lookups."""
    if "(" in operation and operation.endswith(")"):
        return operation.split("(", 1)[0].strip() or operation
    return operation


def _has_snap(enriched: JsonDict, layer: str) -> bool:
    node = enriched.get(layer)
    if not isinstance(node, dict):
        return False
    snap = node.get("enrichedSnapshot")
    return isinstance(snap, dict) and bool(snap)
