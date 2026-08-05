"""Operation-specific source-validation rules aligned with mt-audit-log-resolver-service."""

from __future__ import annotations

from typing import Any

from ..enriched_path_resolver import dig_once
from ...models import JsonDict

# Resolver uses buildServiceAccountActorEnrichedSnapshot (userType=service).
SERVICE_ACCOUNT_ACTOR_OPS = frozenset({
    "regenerateToken",
    "revokeToken",
    "createServiceAccount",
    "updateServiceAccount",
    "deleteServiceAccount",
    "suspendServiceAccount",
})

_WEB_PROJECT_OPS = frozenset({
    "downloadWebProject",
    "publishProject",
    "createWebProject",
    "addStylesToWebProject",
    "removeStylesFromWebProject",
})

_ASSET_TYPE_BY_SUBJECT = {
    "webProject": "WebProject",
    "fontProject": "FontProject",
    "fontList": "FontList",
    "fontSet": "FontSet",
}


def _base_operation(operation: str) -> str:
    if "(" in operation and operation.endswith(")"):
        return operation.split("(", 1)[0].strip() or operation
    return operation


def should_fetch_service_profile(operation: str) -> bool:
    return _base_operation(operation) in SERVICE_ACCOUNT_ACTOR_OPS


def resolve_actor_profile_id(enriched: JsonDict, operation: str) -> str | None:
    """Profile id used for UMS actor lookup (mirrors resolver enrichers)."""
    actor = enriched.get("actor") or {}
    gid = str(actor.get("globalUserId") or "").strip()
    if gid:
        return gid
    return None


def published_x_correlation_id(enriched: JsonDict, live: dict[str, Any] | None = None) -> str | None:
    """Prefer the published audit envelope cid — never a live-replay mint."""
    live = live or {}
    cid = str(enriched.get("xCorrelationId") or "").strip()
    if cid:
        return cid
    trigger = live.get("trigger")
    if isinstance(trigger, dict):
        for key in ("xCorrelationId", "correlation_id"):
            val = str(trigger.get(key) or "").strip()
            if val:
                return val
    return None


def package_id_echo(enriched: JsonDict) -> object | None:
    """getPackageId: subject.id is the packageId from the same GraphQL response."""
    subject = enriched.get("subject") or {}
    meta = subject.get("metadata") if isinstance(subject.get("metadata"), dict) else {}
    result = meta.get("result") if isinstance(meta.get("result"), dict) else {}
    pkg = result.get("packageId")
    if pkg not in (None, "", [], {}):
        return pkg
    ids = subject.get("id")
    if isinstance(ids, list) and ids:
        return ids[0]
    if isinstance(ids, str) and ids.strip():
        return ids
    inp = meta.get("input") if isinstance(meta.get("input"), dict) else {}
    return inp.get("packageId")


def asset_ref_for_operation(
    enriched: JsonDict,
    operation: str | None = None,
) -> tuple[str | None, str | None]:
    """Asset id + AMS type for subject snapshot lookups."""
    base_op = _base_operation(operation or "")
    subject = enriched.get("subject") or {}
    snap = subject.get("enrichedSnapshot") or {}
    asset = snap.get("asset") or {}
    aid: str | None = None
    atype: str | None = None

    if isinstance(asset, dict):
        if asset.get("id"):
            aid = str(asset["id"])
        if asset.get("assetType"):
            atype = str(asset["assetType"])

    meta = subject.get("metadata") if isinstance(subject.get("metadata"), dict) else {}
    inp = meta.get("input") if isinstance(meta.get("input"), dict) else {}
    result = meta.get("result") if isinstance(meta.get("result"), dict) else {}

    if not aid:
        for candidate in (
            dig_once(result, "id"),
            dig_once(result, "asset.id"),
            inp.get("id"),
            inp.get("projectId"),
            inp.get("assetId"),
        ):
            if candidate not in (None, "", [], {}):
                aid = str(candidate)
                break

    ids = subject.get("id")
    if not aid:
        if isinstance(ids, list) and ids:
            aid = str(ids[0])
        elif isinstance(ids, str) and ids.strip():
            aid = ids

    subj_type = str(subject.get("type") or "").strip()
    if not atype:
        if isinstance(asset, dict) and asset.get("assetType"):
            atype = str(asset["assetType"])
        elif subj_type in _ASSET_TYPE_BY_SUBJECT:
            atype = _ASSET_TYPE_BY_SUBJECT[subj_type]
        elif base_op in _WEB_PROJECT_OPS:
            atype = "WebProject"
        elif base_op in {"createProject", "publishProject", "deleteProject", "duplicateProject"}:
            atype = "FontProject"

    if atype == "FontList":
        atype = "FontSet"  # AMS type map (resolver ams-resolution.util.ts)

    return aid, atype
