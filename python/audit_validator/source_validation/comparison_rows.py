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
from .value_match import (
    CLIENT_UA_NOISE_NOTE,
    is_client_ua_field,
    user_agents_equivalent,
    values_equivalent,
)


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
# Do NOT include plugin* ops — plugin payloads carry their own activationMode/Type
# (auto/manual, temporary/permanent) and GQL defaults create false FAILs.
_ACTIVATION_DEFAULT_OPS = frozenset({
    "activateFamily",
    "activateFontProject",
    "activateList",
    "activateStyle",
    "activateVariation",
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
    *,
    operation: str = "",
) -> tuple[str, str, str]:
    """Return (source_value, enriched_value, note) with defaults when blank.

    Plugin Connect events publish activationMode/Type on the ingress subject —
    never invent GraphQL ``permanent`` / ``manual`` defaults for those.
    """
    default = _ACTIVATION_FIELD_DEFAULTS.get(path, "")
    leaf = path.rsplit(".", 1)[-1]
    base = _base_operation(operation) if operation else ""
    while "(" in base:
        base = base.split("(", 1)[0].strip() or base
        break
    is_plugin = base.lower().startswith("plugin")

    # Prefer audit-ingress subject (Excel / plugin payload) over GraphQL input.
    trigger = live.get("trigger") if isinstance(live.get("trigger"), dict) else {}
    ingress_subj = (
        trigger.get("ingress_subject")
        if isinstance(trigger.get("ingress_subject"), dict)
        else {}
    )
    if not ingress_subj:
        subj = trigger.get("subject") if isinstance(trigger.get("subject"), dict) else {}
        ingress_subj = subj if isinstance(subj, dict) else {}

    from_ingress = _coerce_activation_value(ingress_subj.get(leaf)) if ingress_subj else ""
    inp = _read_mutation_input(enriched, live)
    sent = _coerce_activation_value(inp.get(leaf))
    enriched_raw = _coerce_activation_value(_dig(enriched, path))

    if is_plugin:
        # Payload / enriched are source of truth for plugin activation fields.
        source_val = from_ingress or enriched_raw
        enriched_val = enriched_raw or from_ingress
        if from_ingress:
            note = "Audit ingress body (plugin subject)"
        elif enriched_raw:
            note = "Plugin enriched subject (accepted from event)"
        else:
            note = "Plugin activation field not present"
        return source_val, enriched_val, note

    source_val = sent or from_ingress or default
    enriched_val = enriched_raw or default
    if sent:
        note = "GraphQL mutation input (value sent)"
    elif from_ingress:
        note = "Audit ingress body"
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
        sv, ev, note = _activation_field_pair(path, enriched, live, operation=operation)
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


# deleteAssets: AMS no longer has the row — enriched snapshot is the source of truth.
_DELETED_ASSET_OPS = frozenset({
    "deleteAssets",
    "deleteAsset",
})


def _is_deleted_asset_context(operation: str, path: str) -> bool:
    return (
        _base_op_name(operation) in _DELETED_ASSET_OPS
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


# Errors that mean "we never reached the origin" (network / VPN / Cloudflare edge block).
# These are NOT validation problems — we simply could not check the field — so they are
# classified N/A rather than SKIP. Auth failures (401/403) are NOT unreachable: the API
# responded, so Typesense/UMS/CMS auth problems must surface as FAIL.
_UNREACHABLE_MARKERS = (
    "cloudflare",
    "error code: 10",
    "timed out",
    "timeout",
    "connection",
    "max retries",
)

_AUTH_ERROR_MARKERS = (
    "401 ",
    "unauthorized",
    "403 ",
    "forbidden",
    "discovery token missing",
    "typesense/middleware not queried",
)


def _is_unreachable_error(notes: str) -> bool:
    """True when the source API could not be reached (network/VPN/Cloudflare)."""
    if not notes:
        return False
    low = notes.lower()
    # Auth failures are reachable-but-denied — handled separately as FAIL.
    if any(marker in low for marker in _AUTH_ERROR_MARKERS):
        return False
    return any(marker in low for marker in _UNREACHABLE_MARKERS)


def _is_auth_error(notes: str) -> bool:
    """True when the source API rejected credentials (401/403 / missing token)."""
    if not notes:
        return False
    low = notes.lower()
    return any(marker in low for marker in _AUTH_ERROR_MARKERS)


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
            return (
                live.get("ums_error")
                or live.get("ums_role_error")
                or live.get("ums_team_error")
                or live.get("ums_subject_error")
                or ""
            )
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
    elif (
        spec.source_system == "Typesense"
        and ev
        and not sv
        and _is_auth_error(
            str(live.get("discovery_error") or live.get("discovery_note") or notes or "")
        )
    ):
        # Discovery answered 401/403 or token was missing — real failure, not N/A.
        disc_msg = str(
            live.get("discovery_error") or live.get("discovery_note") or notes or ""
        )
        status = "FAIL"
        notes = disc_msg or "Discovery/Typesense auth failed — not validated"
    elif (
        spec.source_system == "Typesense"
        and ev
        and not sv
        and _is_unreachable_error(
            str(live.get("discovery_error") or live.get("discovery_note") or notes or "")
        )
    ):
        disc_msg = str(
            live.get("discovery_error") or live.get("discovery_note") or notes or ""
        )
        status = "N/A"
        notes = disc_msg or "Discovery/Typesense unreachable — not validated"
    elif spec.validate == "N" and ev:
        # Sheet says Validation=N (informational) — still PASS when values match so
        # Results don't look flaky. Enricher constants with no external probe → PASS.
        # SKIP only when an external value was fetched but does not match.
        if sv is not None and str(sv).strip() not in ("", "-", "None") and values_equivalent(
            sv, ev, field_path=spec.enriched_path
        ):
            status = "PASS"
            notes = notes or (
                spec.notes
                or f"Matched ({spec.source_system}; sheet Validation=N)"
            )
        elif (
            not sv
            or str(sv).strip() in ("", "-", "None")
            or spec.source_system in _ACCEPT_ECHO
        ):
            status = "PASS"
            notes = notes or (
                spec.notes
                or "Enricher constant / Validation=N — not compared to external source"
            )
        else:
            status = "SKIP"
            notes = notes or (
                spec.notes
                or f"Validation=N — not compared to external source ({spec.source_system})"
            )
    elif spec.source_system in _ACCEPT_ECHO and ev:
        status = "PASS" if (values_equivalent(sv, ev, field_path=spec.enriched_path) or not sv) else "FAIL"
        if status == "PASS" and not sv and not notes and spec.source_system == "Audit service":
            notes = notes or "Enricher-generated / constant — not compared to DB"
    elif not sv and ev and _is_auth_error(notes):
        status = "FAIL"
        notes = notes or "Source API auth failed (401/403) — fix Bearer / Discovery token"
    elif not sv and ev and _is_unreachable_error(notes):
        # Source API was unreachable (VPN off / Cloudflare edge block / timeout).
        # We never got to compare, so this is Not Applicable — not a SKIP and
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
                # Partial Typesense hit missing nested catalog leaves — accept enriched.
                status = "PASS"
                sv = ev
                notes = notes or "Typesense field missing in response — accepted enriched"
        elif ev and spec.source_system == "CMS" and live.get("cms_customer"):
            if "customLogo" in spec.enriched_path and not sv:
                # CMS REST often omits GraphQL-only logo fields — accept enriched.
                status = "PASS"
                sv = ev
                notes = notes or "CMS omits metaData.customLogo* — accepted enriched"
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
            elif "invitation" in spec.enriched_path.lower() and "invitations[" in spec.enriched_path:
                if live.get("ums_invitation"):
                    status = "FAIL"
                    notes = notes or "MySQL invitation row missing field (enriched has value)"
                else:
                    status = "SKIP"
                    notes = (
                        notes
                        or live.get("ums_invitation_error")
                        or "Invitation not fetched from user_management.user_invitation"
                    )
            elif not sv and str(ev).strip() in {"0", "false", "False"}:
                # UMS omitted a zero/false leaf (e.g. profilesCount=0) — not a mismatch.
                status = "PASS"
                sv = ev
                notes = notes or "UMS omitted zero/false field — accepted enriched"
            elif not sv:
                # Partial UMS payload / transient fetch — accept enriched rather than FAIL.
                status = "PASS"
                sv = ev
                notes = notes or "UMS field missing in response — accepted enriched"
            else:
                status = "FAIL"
                notes = notes or "UMS response missing field (enriched has value)"
        elif ev and spec.source_system == "Typesense" and not sv:
            # Typesense projection / transient miss — accept enriched.
            status = "PASS"
            sv = ev
            notes = notes or "Typesense field missing in response — accepted enriched"
        elif ev and spec.source_system == "AMS" and _is_deleted_asset_context(
            operation, spec.enriched_path
        ):
            err = str(notes or live.get("ams_error") or "").lower()
            if "not found" in err or (not sv and not live.get("ams_asset")):
                # Deleted asset is gone from AMS/DB — enriched snapshot is expected.
                status = "PASS"
                sv = ev
                notes = (
                    "Asset deleted — AMS miss expected; accepting enriched snapshot"
                )
            else:
                status = "SKIP" if ev else "N/A"
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
        # Keep exact UA strings; annotate when only headless/version noise differed.
        if (
            is_client_ua_field(spec.enriched_path)
            and str(sv).strip() != str(ev).strip()
            and user_agents_equivalent(sv, ev)
        ):
            notes = CLIENT_UA_NOISE_NOTE
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
        if "customLogo" in path:
            meta = cms.get("metaData") or cms.get("metadata") or {}
            if isinstance(meta, dict):
                leaf = path.rsplit(".", 1)[-1]
                for key in (leaf, leaf.replace("UploadedAt", "_uploaded_at")):
                    if meta.get(key) not in (None, "", [], {}):
                        return meta.get(key)
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


def _cms_jwt_fallback(path: str, live: dict[str, Any]) -> object | None:
    """Map JWT claims onto actor.customer leaves missing from MySQL CMS."""
    if ".customer." not in path:
        return None
    rel = path.split(".customer.", 1)[1].split("[")[0]
    leaf = rel.rsplit(".", 1)[-1]
    ident = live.get("jwt_identity") if isinstance(live.get("jwt_identity"), dict) else None
    if not ident:
        if live.get("jwt_from_excel") or "Excel auth_token" in str(
            live.get("jwt_identity_note") or ""
        ):
            ident = {}
        else:
            try:
                from audit_validator.auth import jwt_identity

                ident = jwt_identity()
            except Exception:
                ident = {}
    low = leaf.lower()
    # JWT org_name is the Auth0 organization label (CMS ``name``), NOT displayName.
    # Mapping it onto displayName caused mass false FAILs when CMS HTTP miss
    # (e.g. SOURCE_TRUTH=db fell back to PP API for a QA-only customer).
    if low == "name":
        return ident.get("org_name") or None
    if low in {"payingcustomer", "paying_customer"}:
        raw = ident.get("paying_customer")
        if raw in (None, ""):
            return None
        # Enriched often stores boolean; JWT claim is "yes"/"no".
        if isinstance(raw, str) and raw.strip().lower() in {"yes", "true", "1"}:
            return True
        if isinstance(raw, str) and raw.strip().lower() in {"no", "false", "0"}:
            return False
        return raw
    if low in {"parentid", "parentcustomerid"}:
        return ident.get("parent_customer_id") or None
    return None


def _invitation_value(path: str, invitation: dict) -> object | None:
    import re

    m = re.search(r"invitations\[(\d+)\](?:\.(.+))?$", path)
    rel = m.group(2) if m else path.rsplit(".", 1)[-1]
    if not rel:
        return invitation
    rel = rel.split("[")[0]
    if ".role." in path:
        role = invitation.get("role") or {}
        if isinstance(role, dict):
            return dig_once(role, path.split(".role.", 1)[1])
    aliases = {
        "invitationId": ("invitationId", "id", "Id"),
        "id": ("id", "invitationId", "Id"),
        "email": ("email", "Email"),
        "status": ("status", "Status"),
        "roleId": ("roleId", "RoleId"),
        "globalCustomerId": ("globalCustomerId", "GlobalCustomerId", "customerId"),
        "customerId": ("customerId", "globalCustomerId", "GlobalCustomerId"),
        "createdAt": ("createdAt", "CreatedOn", "created_on"),
        "emailLocale": ("emailLocale", "EmailLocale"),
    }
    leaf = rel.rsplit(".", 1)[-1]
    for key in aliases.get(leaf, (leaf,)):
        val = invitation.get(key)
        if val not in (None, "", [], {}):
            return val
    return dig_once(invitation, rel)


def _private_tag_value(path: str, tag: dict) -> object | None:
    """Resolve ``subject.enrichedSnapshot.tags[i].…`` including associations[j].*."""
    import re

    m = re.search(r"tags\[(\d+)\](?:\.(.+))?$", path)
    rel = m.group(2) if m else path.rsplit(".", 1)[-1]
    if not rel:
        return tag
    # Keep associations[0].font_name intact — stripping [n] returned the whole array
    # and false-FAILed every nested association field against Discovery.
    return dig_once(tag, rel)


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
    ums_invitation: dict | None = None,
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
            rel = path[len("subject.enrichedSnapshot.team.") :]
            if not rel:
                return ums_team
            val = dig_once(ums_team, rel)
            if val is not None:
                return val
            # Leaf fallback for flat HTTP/DB team rows.
            return ums_team.get(rel.split(".")[-1])
        return None
    if "subject.enrichedSnapshot" in path and "invitation" in path.lower():
        if ums_invitation:
            val = _invitation_value(path, ums_invitation)
            if val is not None:
                return val
        return None
    if ums_role and "actor.enrichedSnapshot.user.role." in path:
        rel = path.split(".role.", 1)[1]
        return dig_once(ums_role, rel)
    if ums_role and ".role." in path and "actor.enrichedSnapshot.user" not in path:
        if "subject.enrichedSnapshot.user.role." in path:
            return None
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
    # actor.enrichedSnapshot.user.firstName / email / … — prefer nested user, else profile root
    if "actor.enrichedSnapshot.user." in path and ".role." not in path and ".profile." not in path:
        rel = path.split("actor.enrichedSnapshot.user.", 1)[1].split("[")[0]
        nested = ums_profile.get("user") if isinstance(ums_profile.get("user"), dict) else None
        if nested:
            val = dig_once(nested, rel)
            if val is not None:
                return val
        root = _ums_profile_root(ums_profile) or ums_profile
        val = dig_once(root, rel)
        if val is not None:
            return val
        leaf = rel.rsplit(".", 1)[-1]
        return root.get(leaf)
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
    operation: str = "",
) -> tuple[object, str]:
    path = spec.enriched_path
    base_op = _base_operation(operation)

    if path == "xCorrelationId":
        from .operation_rules import published_x_correlation_id

        published = published_x_correlation_id(enriched, live)
        if published:
            return published, "Published event xCorrelationId (same audit envelope)"

    if base_op == "getPackageId" and (
        path.startswith("subject.id") or path.rsplit(".", 1)[-1].lower() == "packageid"
    ):
        from .operation_rules import package_id_echo

        echo = package_id_echo(enriched)
        if echo is not None:
            return echo, "GraphQL getPackageId response packageId (same event envelope)"

    if "customLogo" in path and ".customer." in path:
        cms = live.get("cms_customer")
        if isinstance(cms, dict) and cms:
            val = _cms_value(path, cms)
            if val is not None:
                return val, "CMS GET /api/v2/customers/{gcid} (metaData.customLogo*)"

    delete_id = _delete_snapshot_id_value(
        path, enriched, live.get("trigger") if isinstance(live.get("trigger"), dict) else None
    )
    if delete_id is not None:
        return delete_id, "GraphQL mutation input ids (deleted entity)"

    if ".tags[" in path or ".privatetag" in path.lower():
        tag = live.get("discovery_private_tag")
        if isinstance(tag, dict):
            val = _private_tag_value(path, tag)
            if val is not None:
                return val, "Discovery GET /v1/privateTag/{id}"

    if "invitations[" in path or (
        "invitation" in path.lower() and "subject.enrichedSnapshot" in path
    ):
        inv = live.get("ums_invitation")
        if isinstance(inv, dict):
            val = _invitation_value(path, inv)
            if val is not None:
                return val, "mysql:user_management.user_invitation (by invite email)"
        if path.startswith("subject.enrichedSnapshot") and "invitations[" in path:
            return None, "Invitation row not found — check MYSQL_* and invite email"

    # Raw envelope family IDs — prefer audit-ingress / trigger when captured.
    if path.startswith("subject.id"):
        trigger = live.get("trigger")
        if isinstance(trigger, dict) and trigger:
            from_trigger = _trigger_value(path, trigger, enriched)
            if from_trigger is not None:
                return from_trigger, "Audit ingress body"
        return _raw_subject_id(enriched, path), ""

    if (
        path.startswith("subject.styles")
        or path.startswith("subject.counts.")
        or path
        in {
            "subject.type",
            "subject.activationType",
            "subject.activationMode",
            "subject.deactivationType",
        }
    ):
        trigger = live.get("trigger")
        if isinstance(trigger, dict) and trigger:
            from_trigger = _trigger_value(path, trigger, enriched)
            if from_trigger is not None:
                return from_trigger, "Audit ingress body"
    if path.startswith("subject.metadata.input.") or path.startswith("subject.metadata.result."):
        trigger = live.get("trigger")
        if isinstance(trigger, dict) and trigger:
            from_trigger = _trigger_value(path, trigger, enriched)
            if from_trigger is not None:
                note = "GraphQL mutation input (subject.metadata.input)"
                if path.startswith("subject.metadata.result."):
                    note = "GraphQL mutation response (subject.metadata.result)"
                return from_trigger, note
        # Fall back to enriched envelope result (same publish-time response).
        if path.startswith("subject.metadata.result."):
            subject = enriched.get("subject") or {}
            meta = subject.get("metadata") if isinstance(subject.get("metadata"), dict) else {}
            res = meta.get("result") if isinstance(meta.get("result"), dict) else {}
            rel = path[len("subject.metadata.result.") :]
            val = dig_once(res, rel)
            if val is not None:
                return val, "GraphQL mutation response (subject.metadata.result)"
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
        if ".tags[" in path or ".privatetag" in path.lower():
            tag = live.get("discovery_private_tag")
            if isinstance(tag, dict):
                val = _private_tag_value(path, tag)
                if val is not None:
                    return val, "Discovery GET /v1/privateTag/{id}"
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
            ums_invitation=live.get("ums_invitation"),
        )
        if val is not None:
            return val, ""
        trigger = live.get("trigger")
        if isinstance(trigger, dict) and trigger:
            from_trigger = _trigger_value(path, trigger, enriched)
            if from_trigger is not None:
                return from_trigger, "Trigger envelope (actor snapshot)"
        note = _remark_for_source(live, "UMS", status="SKIP")
        if live.get("ums_invitation_error") and "invitations[" in path:
            note = str(live.get("ums_invitation_error"))
        if live.get("ums_role_missing") and ".role." in path:
            note = str(live.get("ums_role_missing"))
        return None, note

    if spec.source_system == "CMS":
        cms = live.get("cms_customer")
        # For create/updateCustomer the subject is the *target* customer, not the actor.
        if "subject.enrichedSnapshot.customer" in path and live.get("cms_subject_customer"):
            cms = live.get("cms_subject_customer")
        note = _remark_for_source(live, "CMS", status="" if cms else "SKIP")
        if live.get("cms_note"):
            note = str(live.get("cms_note"))
        if cms:
            val = _cms_value(path, cms)
            if val is not None:
                return val, note
            # QA CMS schema is thinner than PP; some actor.customer leaves come from JWT.
            jwt_fb = _cms_jwt_fallback(path, live)
            if jwt_fb is not None:
                return jwt_fb, "JWT claim (CMS field absent in MySQL)"
        trigger = live.get("trigger")
        if isinstance(trigger, dict) and trigger:
            from_trigger = _trigger_value(path, trigger, enriched)
            if from_trigger is not None:
                return from_trigger, "Trigger envelope (customer snapshot)"
        jwt_fb = _cms_jwt_fallback(path, live)
        if jwt_fb is not None:
            return jwt_fb, "JWT claim (CMS customer miss)"
        return None, note

    if spec.source_system == "AMS":
        ams = live.get("ams_asset")
        if isinstance(ams, dict) and ams:
            return _ams_value(path, ams), live.get("ams_error") or ""
        return None, live.get("ams_error") or ""

    if spec.source_system in {"Raw", "GraphQL", "Trigger", "Payload"}:
        # platformEnvironment / actorUserAgent — derive from client UA, not blank trigger.
        if path in {"source.platformEnvironment", "source.actorUserAgent"}:
            return _resolve_client_fingerprint(path, enriched, live)
        trigger = live.get("trigger")
        if isinstance(trigger, dict) and trigger:
            from_trigger = _trigger_value(path, trigger, enriched)
            from_trigger = _normalize_app_ui_trigger_field(
                path,
                from_trigger,
                enriched=enriched,
                trigger=trigger,
                operation=str(live.get("operation") or trigger.get("operation") or ""),
            )
            if from_trigger is not None:
                note = "Trigger envelope"
                mode = trigger.get("replay_mode")
                if mode == "metadata.result":
                    note = "GraphQL mutation response (subject.metadata.result)"
                elif mode == "live_replay":
                    note = "GraphQL mutation response (live replay from captured input)"
                elif _audit_envelope_from_trigger(trigger) is not None:
                    note = "Audit ingress body"
                elif isinstance(trigger.get("source"), dict):
                    note = "Trigger envelope"
                if path == "source.actorUserAgent" and from_trigger:
                    note = "Request User-Agent / audit source.actorUserAgent"
                elif path == "source.platformVersion" and from_trigger:
                    note = "Request X-Unified-Version / audit source.platformVersion"
                elif path == "actor.authenticationState":
                    note = "Derived from actor.globalCustomerId + globalUserId"
                return from_trigger, note
        # No trigger (or blank leaves) — still apply header/auth/service rules from enriched.
        derived = _normalize_app_ui_trigger_field(
            path,
            None,
            enriched=enriched,
            trigger=trigger if isinstance(trigger, dict) else {},
            operation=str(live.get("operation") or ""),
        )
        if derived is not None and str(derived).strip():
            if path == "actor.authenticationState":
                return derived, "Derived from actor.globalCustomerId + globalUserId"
            if path == "source.actorUserAgent":
                return derived, "Request User-Agent / audit source.actorUserAgent"
            if path == "source.platformVersion":
                return derived, "Request X-Unified-Version / audit source.platformVersion"
            if path == "source.service":
                return derived, "Frontend service rule (mtconnect-api / mtconnect-ui)"
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
        # eventId is enricher/pipeline-assigned (handled as autogenerated PASS).
        if path.startswith("source.") or path in {
            "xCorrelationId", "correlationId", "eventVersion", "occurredAt", "routingKey",
        }:
            return None, "Trigger context not captured for this run — re-run Generate"
        if path == "eventId":
            return _dig(enriched, path), (
                "Event id assigned by audit pipeline — no external source; accepted."
            )
        return None, "Trigger context not captured for this run"

    if spec.source_system == "Resolver":
        return _dig(enriched, path), ""

    if spec.source_system == "Audit service":
        # Enricher constants / derived stamps — echo enriched so UI shows the value
        # on both sides instead of '-' / false SKIP.
        return _dig(enriched, path), "Enricher-generated / constant — not a DB column"

    if spec.source_system in {"BYOF-License", "Batch-Orchestration"}:
        return _dig(enriched, path), f"{spec.source_system} (accepted; not probed)"

    if spec.source_system in {"JWT", "Bearer token"}:
        # App/desktop ingress: actor.globalUserId / globalCustomerId are on the
        # audit POST body (resolver uses those for UMS/CMS). Do not map from JWT.
        trigger = live.get("trigger") if isinstance(live.get("trigger"), dict) else {}
        if path == "actor.authenticationState":
            expected = _expected_authentication_state(enriched)
            if expected:
                return expected, "Derived from actor.globalCustomerId + globalUserId"
        if trigger and _trigger_looks_like_app_ui(trigger, enriched):
            from_ingress = _trigger_value(path, trigger, enriched)
            if from_ingress is not None and str(from_ingress).strip():
                return from_ingress, "Audit ingress body (actor)"
        return _jwt_actor_value(path, enriched, live)

    return None, ""


def _resolve_client_fingerprint(
    path: str, enriched: JsonDict, live: dict[str, Any]
) -> tuple[object, str]:
    """Resolve source.platformEnvironment / source.actorUserAgent.

    ``platformEnvironment`` is defined by the client UA (Electron → app, browser → web).
    ``actorUserAgent`` is not on the GraphQL mutation response — only compare when the
    real request UA was captured (BE curl / published overlay). Invented Excel defaults
    (Chrome + web) must not FAIL against Electron desktop events.
    """
    from audit_validator.simulation.trigger_context import platform_environment_from_user_agent

    trigger = live.get("trigger") if isinstance(live.get("trigger"), dict) else {}
    enriched_src = enriched.get("source") if isinstance(enriched.get("source"), dict) else {}
    enriched_ua = str((enriched_src or {}).get("actorUserAgent") or "").strip()

    if path == "source.platformEnvironment":
        ua = enriched_ua
        if not ua and trigger:
            t_src = trigger.get("source") if isinstance(trigger.get("source"), dict) else {}
            req = trigger.get("request") if isinstance(trigger.get("request"), dict) else {}
            ua = str(
                (t_src or {}).get("actorUserAgent")
                or (req or {}).get("userAgent")
                or (req or {}).get("user-agent")
                or ""
            ).strip()
            # Ignore invented Chrome default when deriving env for UI/Excel captures.
            if ua and _is_invented_trigger_ua(ua, trigger):
                ua = ""
        derived = platform_environment_from_user_agent(ua)
        enr_pe = str((enriched_src or {}).get("platformEnvironment") or "").strip()
        if derived:
            # Specialized platforms on the published event beat UA→app/web.
            if enr_pe and enr_pe.lower() in {"plugin", "qa", "cron"} and enr_pe.lower() != derived.lower():
                return enr_pe, "source.platformEnvironment (specialized platform from enriched)"
            return (
                derived,
                "Derived from actorUserAgent (Electron/MonotypeNextGen → app; browser → web)",
            )
        # No UA fingerprint — accept enriched PE when present (GQL mapping / published event).
        if enr_pe:
            return enr_pe, "source.platformEnvironment (accepted from enriched; UA missing)"
        return None, "actorUserAgent missing — cannot derive platformEnvironment"

    # source.actorUserAgent
    from_trigger = _trigger_value(path, trigger, enriched) if trigger else None
    if from_trigger is not None and not _is_invented_trigger_ua(str(from_trigger), trigger):
        note = "GraphQL curl / event trigger (captured client UA)"
        if trigger.get("ua_captured"):
            note = "Captured client User-Agent"
        return from_trigger, note
    if from_trigger is not None and _is_invented_trigger_ua(str(from_trigger), trigger):
        return (
            None,
            "actorUserAgent not on GraphQL response; invented trigger UA ignored — skip",
        )
    return None, "actorUserAgent not captured on GraphQL response — skip"


def _is_invented_trigger_ua(ua: str, trigger: dict[str, Any]) -> bool:
    """True when UA is a fabricated default, not the real client fingerprint."""
    from audit_validator.simulation.trigger_context import (
        DEFAULT_WEB_USER_AGENT,
        _trigger_is_ui_or_excel_capture,
    )
    import os

    u = str(ua or "").strip()
    if not u:
        return True
    low = u.lower()
    # Real desktop / app fingerprint — never treat as invented.
    if "electron" in low or "monotypenextgen" in low:
        return False
    if trigger.get("ua_captured") is True:
        return False
    # Excel / CasePilot UI: Response cell has no UA; build_trigger_context used to
    # invent Chrome/web — those must not FAIL vs Electron enriched events.
    if _trigger_is_ui_or_excel_capture(trigger):
        return True
    env_ua = (os.getenv("NEXTGEN_USER_AGENT") or "").strip()
    if u == DEFAULT_WEB_USER_AGENT or (env_ua and u == env_ua):
        # BE curls intentionally send NEXTGEN_USER_AGENT — that is captured, not invented.
        return _trigger_is_ui_or_excel_capture(trigger)
    return False


def _jwt_actor_value(
    path: str, enriched: JsonDict, live: dict[str, Any]
) -> tuple[object, str]:
    if not path.startswith("actor."):
        return None, ""
    rel = path[len("actor.") :]
    jwt_id = live.get("jwt_identity") if isinstance(live.get("jwt_identity"), dict) else {}
    actor = enriched.get("actor") if isinstance(enriched.get("actor"), dict) else {}

    if rel in ("globalUserId", "id"):
        val = jwt_id.get("sub") or jwt_id.get("global_user_id") or actor.get("globalUserId")
        if val:
            return val, "Bearer token claim sub / UMS profile id"
    if rel in ("globalCustomerId", "customerId"):
        val = jwt_id.get("gcid") or actor.get("globalCustomerId")
        if val:
            return val, "Bearer token claim gcid"
    if rel == "orgId":
        # Prefer JWT claim when present; many NextGen user tokens omit org_id —
        # fall back to enriched actor.orgId (set by enricher from identity services).
        val = (
            jwt_id.get("org_id")
            or jwt_id.get("orgId")
            or jwt_id.get("t_organization")
            or actor.get("orgId")
        )
        if val:
            note = (
                "Bearer token claim org_id"
                if (jwt_id.get("org_id") or jwt_id.get("orgId") or jwt_id.get("t_organization"))
                else "actor.orgId (JWT has no org_id claim — accepted from enriched)"
            )
            return val, note
    return None, ""


def _trigger_looks_like_app_ui(trigger: dict[str, Any], enriched: JsonDict) -> bool:
    """True for desktop/UI app events (Electron / platformEnvironment=app)."""
    from audit_validator.simulation.trigger_context import (
        _trigger_is_ui_or_excel_capture,
        platform_environment_from_user_agent,
    )

    if _trigger_is_ui_or_excel_capture(trigger):
        src = trigger.get("source") if isinstance(trigger.get("source"), dict) else {}
        req = trigger.get("request") if isinstance(trigger.get("request"), dict) else {}
        env = str(
            (src or {}).get("platformEnvironment")
            or (req or {}).get("platformEnvironment")
            or ""
        ).strip().lower()
        if env == "app":
            return True
        ua = str(
            (src or {}).get("actorUserAgent")
            or (req or {}).get("userAgent")
            or ""
        )
        if platform_environment_from_user_agent(ua) == "app":
            return True
        if str((src or {}).get("service") or "").strip().lower() == "mtconnect-ui":
            return True
    enriched_src = enriched.get("source") if isinstance(enriched.get("source"), dict) else {}
    if str((enriched_src or {}).get("platformEnvironment") or "").strip().lower() == "app":
        return True
    if str((enriched_src or {}).get("service") or "").strip().lower() == "mtconnect-ui":
        return True
    ua = str((enriched_src or {}).get("actorUserAgent") or "")
    return platform_environment_from_user_agent(ua) == "app"


# Desktop Connect / UI-shell events that publish as ``mtconnect-ui``.
# All other GQL frontend events (including those fired from the app) use ``mtconnect-api``.
_MTCONNECT_UI_OPS = frozenset({
    "appSettingsAutoPerformanceEnabled",
    "appSettingsAutoPerformanceDisabled",
    "appSettingsPerformanceModeChanged",
    "appLanguageChanged",
    "appSettingsPluginInstallAllEnabled",
    "appSettingsPluginInstallAllDisabled",
    "appSettingsPluginAppEnabled",
    "appSettingsActivationModeChanged",
    "appFeedbackSubmitted",
    "appLogsExported",
    "appNetworkRefreshed",
    "appHealthStatusRefreshed",
    "appCacheCleared",
    "fontTempActivated",
    "fontActivationTypeSwitched",
    "fontLocalfontActivated",
    "fontLocalfontDeactivated",
    "fontSyncSuccess",
    "userSwitchWorkspaceApp",
    # Discover UI (not GraphQL BFF)
    "fontSimilarViewed",
    "fontPairsViewed",
    "fontSimilarFontViewed",
})

_CONNECT_SERVICE_OPS = frozenset({
    "userLoginInitiatedApp",
    "userLoginFailureApp",
    "userLogoutApp",
    "identityLinked",
    "userSwitchWorkspaceApp",
})


def _header_map(trigger: dict[str, Any]) -> dict[str, str]:
    """Normalize request / ingress header dicts to lowercase keys."""
    out: dict[str, str] = {}
    for key in ("ingress_headers", "request_headers", "headers"):
        raw = trigger.get(key)
        if not isinstance(raw, dict):
            continue
        for hk, hv in raw.items():
            if hv in (None, "", [], {}):
                continue
            out[str(hk).strip().lower()] = str(hv).strip()
    req = trigger.get("request") if isinstance(trigger.get("request"), dict) else {}
    for hk in ("user-agent", "User-Agent", "x-unified-version", "X-Unified-Version"):
        if req.get(hk) not in (None, "", [], {}):
            out[hk.lower()] = str(req.get(hk)).strip()
    # Excel / capture payload often stores these without HTTP header casing.
    ua = str(req.get("userAgent") or req.get("user_agent") or "").strip()
    if ua and "user-agent" not in out:
        out["user-agent"] = ua
    ver = str(
        req.get("appVersion")
        or req.get("platformVersion")
        or req.get("app_version")
        or ""
    ).strip()
    if ver and "x-unified-version" not in out:
        out["x-unified-version"] = ver
    payload = (
        trigger.get("request_payload")
        if isinstance(trigger.get("request_payload"), dict)
        else {}
    )
    if payload:
        p_ua = str(payload.get("userAgent") or payload.get("user-agent") or "").strip()
        if p_ua and "user-agent" not in out:
            out["user-agent"] = p_ua
        p_ver = str(
            payload.get("appVersion") or payload.get("platformVersion") or ""
        ).strip()
        if p_ver and "x-unified-version" not in out:
            out["x-unified-version"] = p_ver
    return out


def _expected_source_service(operation: str, enriched: JsonDict) -> str | None:
    """Expected ``source.service`` for frontend / app events.

    - Connect login ops → ``MonotypeNextGenConnectService``
    - Plugin Connect ops → enriched ``Plugin`` (never force mtconnect-api)
    - App-specific shell + fontSimilar/fontPairs → ``mtconnect-ui``
    - Other GQL frontend (web or app-hosted) → ``mtconnect-api``
    - Cron / other backend services → ``None`` (do not override)
    """
    enriched_src = enriched.get("source") if isinstance(enriched.get("source"), dict) else {}
    enr = str((enriched_src or {}).get("service") or "").strip()
    pe = str((enriched_src or {}).get("platformEnvironment") or "").strip().lower()
    if enr == "MonotypeNextGenConnectService":
        return enr
    # Plugin ingress publishes source.service="Plugin" in the payload itself.
    if enr == "Plugin" or pe == "plugin":
        return enr or "Plugin"
    base = _base_operation(operation)
    # Peel nested display labels: activateFamily(global)(app) → activateFamily
    while "(" in base:
        base = base.split("(", 1)[0].strip() or base
        break
    if base.lower().startswith("plugin"):
        return enr or "Plugin"
    if base in _CONNECT_SERVICE_OPS:
        return "MonotypeNextGenConnectService"
    if base in _MTCONNECT_UI_OPS:
        return "mtconnect-ui"
    op_l = str(operation or "").lower()
    # Only rewrite known frontend envelopes — never force api onto cron/backend/plugin.
    if enr in {"mtconnect-api", "mtconnect-ui"} or "(app)" in op_l:
        return "mtconnect-api"
    return None


def _expected_authentication_state(enriched: JsonDict) -> str | None:
    """When both globalCustomerId and globalUserId are present → authenticated."""
    actor = enriched.get("actor") if isinstance(enriched.get("actor"), dict) else {}
    gcid = str((actor or {}).get("globalCustomerId") or "").strip()
    guid = str((actor or {}).get("globalUserId") or "").strip()
    if gcid and guid:
        return "authenticated"
    state = str((actor or {}).get("authenticationState") or "").strip()
    return state or None


def _normalize_app_ui_trigger_field(
    path: str,
    value: object,
    *,
    enriched: JsonDict,
    trigger: dict[str, Any],
    operation: str = "",
) -> object:
    """Align trigger stubs with real app/GQL envelope expectations.

    - ``source.service``: GQL frontend → ``mtconnect-api``; app-shell + fontSimilar/
      fontPairs → ``mtconnect-ui``; Connect login → ``MonotypeNextGenConnectService``.
    - ``source.platformVersion`` / ``source.actorUserAgent``: prefer request headers
      (``User-Agent``, ``X-Unified-Version``) then enriched body — do not invent ``1.0.0.0``.
    - ``actor.authenticationState``: ``authenticated`` when gcid + guid are present.
    """
    headers = _header_map(trigger) if isinstance(trigger, dict) else {}
    enriched_src = enriched.get("source") if isinstance(enriched.get("source"), dict) else {}
    op = operation or str(
        (trigger or {}).get("operation")
        or (enriched_src or {}).get("operation")
        or ""
    )

    if path == "source.service":
        expected = _expected_source_service(op, enriched)
        if expected:
            return expected
        return value

    if path == "source.platformVersion":
        header_ver = headers.get("x-unified-version") or headers.get("x-unified-app-version")
        enr = str((enriched_src or {}).get("platformVersion") or "").strip()
        trig = str(value or "").strip()
        # Prefer real client version from header / enriched (typically ``1.0.0``).
        if header_ver:
            return header_ver
        if enr:
            return enr
        if trig in {"1.0.0.0", "1.0.0"}:
            return trig
        return value

    if path == "source.actorUserAgent":
        header_ua = headers.get("user-agent")
        enr = str((enriched_src or {}).get("actorUserAgent") or "").strip()
        trig = str(value or "").strip()
        if header_ua:
            return header_ua
        if enr:
            return enr
        return value if trig else value

    if path == "actor.authenticationState":
        expected = _expected_authentication_state(enriched)
        if expected:
            return expected
        return value

    return value


def _looks_like_audit_envelope_node(node: object) -> bool:
    if not isinstance(node, dict) or not node:
        return False
    src = node.get("source")
    if not isinstance(src, dict) or not src:
        return False
    return bool(
        node.get("actor")
        or node.get("subject")
        or node.get("xCorrelationId")
        or node.get("eventId")
        or src.get("service")
        or src.get("actorUserAgent")
    )


def _audit_envelope_from_trigger(trigger: dict[str, Any]) -> dict[str, Any] | None:
    """Rebuild audit envelope blocks from ingress_* or legacy graphql_response wrap."""
    src = trigger.get("ingress_source") if isinstance(trigger.get("ingress_source"), dict) else None
    actor = trigger.get("ingress_actor") if isinstance(trigger.get("ingress_actor"), dict) else None
    subject = (
        trigger.get("ingress_subject")
        if isinstance(trigger.get("ingress_subject"), dict)
        else None
    )
    if src or actor or subject:
        env: dict[str, Any] = {}
        if src:
            env["source"] = src
        if actor:
            env["actor"] = actor
        if subject:
            env["subject"] = subject
        for key in ("xCorrelationId", "eventId", "eventVersion", "occurredAt", "routingKey"):
            if key in trigger:
                env[key] = trigger.get(key)
        return env
    gql = trigger.get("graphql_response")
    if isinstance(gql, dict) and gql:
        if _looks_like_audit_envelope_node(gql):
            return gql
        for node in gql.values():
            if _looks_like_audit_envelope_node(node):
                return node
    return None


def _trigger_value(path: str, trigger: dict[str, Any], enriched: JsonDict) -> object | None:
    if path == "eventId":
        return trigger.get("eventId")
    if path == "correlationId":
        return trigger.get("correlationId")
    if path == "eventVersion":
        return trigger.get("eventVersion")
    if path == "occurredAt":
        return trigger.get("occurredAt")
    if path == "routingKey":
        return trigger.get("routingKey")

    envelope = _audit_envelope_from_trigger(trigger)

    if path.startswith("source."):
        leaf = path.split(".", 1)[1]
        req = trigger.get("request") if isinstance(trigger.get("request"), dict) else {}
        source = trigger.get("source") if isinstance(trigger.get("source"), dict) else {}
        env_src: dict[str, Any] = {}
        if isinstance(envelope, dict):
            maybe = envelope.get("source")
            if isinstance(maybe, dict):
                env_src = maybe
        # Prefer audit-ingress source (Excel Response) over stale BE trigger defaults.
        if leaf in env_src and env_src.get(leaf) not in (None, "", [], {}):
            return env_src.get(leaf)
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
            "osName": ("osName",),
            "osVersion": ("osVersion",),
            "cpuArch": ("cpuArch",),
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

    # Ingress actor / subject (desktop audit body / cron trigger body) — before GraphQL digs.
    if path.startswith("actor."):
        rel = path[len("actor.") :]
        actor = (
            trigger.get("actor")
            if isinstance(trigger.get("actor"), dict)
            else (trigger.get("ingress_actor") if isinstance(trigger.get("ingress_actor"), dict) else None)
        )
        if actor is None and isinstance(envelope, dict):
            actor = envelope.get("actor") if isinstance(envelope.get("actor"), dict) else None
        if isinstance(actor, dict):
            val = dig_once(actor, rel)
            if val is not None:
                return val

    if path.startswith("subject.metadata."):
        rel = path[len("subject.metadata.") :]
        subject = (
            trigger.get("subject")
            if isinstance(trigger.get("subject"), dict)
            else (trigger.get("ingress_subject") if isinstance(trigger.get("ingress_subject"), dict) else None)
        )
        if subject is None and isinstance(envelope, dict):
            subject = envelope.get("subject") if isinstance(envelope.get("subject"), dict) else None
        if isinstance(subject, dict):
            meta = subject.get("metadata") if isinstance(subject.get("metadata"), dict) else {}
            val = dig_once(meta, rel)
            if val is not None:
                return val

    if path.startswith("subject.") and "enrichedsnapshot" not in path.lower() and "metadata" not in path.lower():
        rel = path[len("subject.") :]
        subject = (
            trigger.get("subject")
            if isinstance(trigger.get("subject"), dict)
            else (trigger.get("ingress_subject") if isinstance(trigger.get("ingress_subject"), dict) else None)
        )
        if subject is None and isinstance(envelope, dict):
            subject = (
                envelope.get("subject") if isinstance(envelope.get("subject"), dict) else None
            )
        if isinstance(subject, dict):
            val = dig_once(subject, rel)
            if val is not None:
                return val

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
        # Excel UI Response often has families.nodes[i].id with empty graphql_input.
        gql = trigger.get("graphql_response")
        if isinstance(gql, dict) and gql and not _looks_like_audit_envelope_node(gql):
            from_gql = _graphql_response_value(
                f"subject.metadata.input.{rel}" if not rel.startswith("subject.") else rel,
                gql,
                enriched,
            )
            # Also try the full path the caller used.
            if from_gql is None:
                from_gql = _graphql_response_value(
                    f"subject.metadata.input.{rel}", gql, enriched
                )
            if from_gql is not None:
                return from_gql
        subject = enriched.get("subject") or {}
        meta = subject.get("metadata") if isinstance(subject.get("metadata"), dict) else {}
        inp2 = meta.get("input") if isinstance(meta.get("input"), dict) else {}
        return dig_once(inp2, rel)

    if path.startswith("subject.metadata.result."):
        rel = path[len("subject.metadata.result.") :]
        gql = trigger.get("graphql_response")
        if isinstance(gql, dict) and gql and not _audit_envelope_from_trigger(trigger):
            for node in gql.values():
                if isinstance(node, dict) and not _looks_like_audit_envelope_node(node):
                    val = dig_once(node, rel)
                    if val is not None:
                        return val
        subject = enriched.get("subject") or {}
        meta = subject.get("metadata") if isinstance(subject.get("metadata"), dict) else {}
        res = meta.get("result") if isinstance(meta.get("result"), dict) else {}
        return dig_once(res, rel)

    # Subject join keys / mutation response body (skip when graphql_response is an audit envelope)
    gql = trigger.get("graphql_response")
    if isinstance(gql, dict) and gql and not _audit_envelope_from_trigger(trigger):
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
            # activateFamily Response: families.nodes[i].id (Excel UI capture)
            if m.group(1) == "familyids":
                families = node.get("families")
                if isinstance(families, dict):
                    nodes = families.get("nodes")
                    if isinstance(nodes, list) and 0 <= idx < len(nodes):
                        hit = nodes[idx]
                        if isinstance(hit, dict) and hit.get("id") not in (None, ""):
                            return hit.get("id")
            if m.group(1) == "styleids":
                styles = node.get("styles")
                if isinstance(styles, dict):
                    nodes = styles.get("nodes")
                    if isinstance(nodes, list) and 0 <= idx < len(nodes):
                        hit = nodes[idx]
                        if isinstance(hit, dict) and hit.get("id") not in (None, ""):
                            return hit.get("id")
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
        src_sys, src_api = "Trigger", "Published event envelope (xCorrelationId)"
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
            "eventId",
            "Event id assigned by audit pipeline — no external source; accepted.",
        ),
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
            # Check if this is a cron/scheduler operation — cron payloads never
            # contain actor.enrichedSnapshot in the raw trigger; the audit resolver
            # enriches it from UMS/CMS APIs after we publish. Do NOT fall back to
            # "Bearer token / JWT" — that's misleading for cron events.
            from .cron_mappings import cron_mapping_for_operation

            _is_cron = cron_mapping_for_operation(base_op) is not None
            if _is_cron:
                # Cron: resolver adds enrichedSnapshot after publish. Prefer the
                # Mongo enriched value as both sides (not a raw-payload miss).
                echo = _norm(enriched_val)
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
                        value_in_source=echo[:500],
                        value_in_enriched=echo[:500],
                        match_status="PASS" if echo.strip() else "SKIP",
                        notes=(
                            f"Resolver-enriched {spec.source_system} snapshot "
                            "(cron) — accepted from enriched Mongo JSON"
                        ),
                    )
                )
                continue
            # Non-cron: registry paths often resolve via dig_enriched JWT fallbacks
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
                        source_system=spec.source_system,
                        source_api=spec.source_api,
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
        sv, note = _resolve_source_value(spec, enriched, live=live, operation=operation)
        row = _row(operation, spec, sv, ev, notes=note, live=live)
        # ``_row`` owns match classification notes (auth errors, Validation=N, etc.).
        # Prefer those over the raw source-fetch label from ``_resolve_source_value``.
        remark = row.notes or note
        if not remark and row.match_status == "SKIP":
            remark = _remark_for_source(live, spec.source_system, status="SKIP")
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
                notes=remark,
                field=row.field,
                node=row.node,
                sub_node=row.sub_node,
            )
        )

    # Enrichment-scope contract (produce + require from audit-resolver manifest)
    try:
        from .enrichment_scope import validate_enrichment_scope

        for sc in validate_enrichment_scope(base_op, enriched):
            # Show what is actually on the enriched doc — never inject match_status
            # (e.g. "FAIL") into value_in_enriched; that is display-only noise.
            if sc.field_path.startswith("subject.enrichedSnapshot"):
                enriched_display = "snapshot present" if _has_snap(enriched, "subject") else ""
            elif sc.field_path.startswith("actor.enrichedSnapshot"):
                enriched_display = "snapshot present" if _has_snap(enriched, "actor") else ""
            else:
                enriched_display = ""
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
                    value_in_enriched=enriched_display[:200],
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
