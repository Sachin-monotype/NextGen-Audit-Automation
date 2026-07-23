"""Discover scalar fields present in enriched audit JSON (enriched-first validation)."""

from __future__ import annotations

import re
from typing import Any

from ..models import JsonDict
from .enriched_path_resolver import dig_enriched, normalize_enriched_path

_SKIP_PREFIXES = (
    "subject.metadata",
    "subject.enrichedSnapshot.asset.children",
    "subject.enrichedSnapshot.sharingInfo",
    "actor.enrichedSnapshot.user.profile.meta",
)

_SCALAR_TYPES = (str, int, float, bool)


def _is_scalar(val: object) -> bool:
    if val is None:
        return False
    if isinstance(val, _SCALAR_TYPES):
        return isinstance(val, str) and val.strip() != "" or not isinstance(val, str)
    return False


def _walk(obj: object, prefix: str, out: list[tuple[str, object]]) -> None:
    if prefix and any(prefix.startswith(p) for p in _SKIP_PREFIXES):
        return
    if _is_scalar(obj):
        out.append((prefix, obj))
        return
    if isinstance(obj, dict):
        for key, val in obj.items():
            if not key or key.startswith("_"):
                continue
            path = f"{prefix}.{key}" if prefix else key
            _walk(val, path, out)
        return
    if isinstance(obj, list):
        if not obj:
            return
        # Index concrete elements; also expose [0] paths for mappers
        for idx, item in enumerate(obj[:3]):
            seg = f"{prefix}[{idx}]"
            _walk(item, seg, out)
        if len(obj) == 1:
            return
        # Single indexed slot is enough for validation rows
        return


def scan_enriched_fields(enriched: JsonDict) -> list[tuple[str, object]]:
    """
    List ``(path, value)`` for leaves under event envelope + enriched snapshots.

    Paths use dotted notation with ``[0]`` array indexes (resolver layout).
    """
    out: list[tuple[str, object]] = []
    for top in ("xCorrelationId", "eventId", "eventVersion", "enrichmentVersion", "routingKey"):
        val = enriched.get(top)
        if _is_scalar(val):
            out.append((top, val))

    source = enriched.get("source")
    if isinstance(source, dict):
        for key, val in source.items():
            if _is_scalar(val):
                out.append((f"source.{key}", val))

    actor = enriched.get("actor")
    if isinstance(actor, dict):
        for key in ("globalUserId", "globalCustomerId", "orgId"):
            val = actor.get(key)
            if _is_scalar(val):
                out.append((f"actor.{key}", val))
        snap = actor.get("enrichedSnapshot")
        if isinstance(snap, dict) and snap:
            _walk(snap, "actor.enrichedSnapshot", out)

    subject = enriched.get("subject")
    if isinstance(subject, dict):
        if isinstance(subject.get("type"), str):
            out.append(("subject.type", subject["type"]))
        for key in ("activationType", "activationMode", "deactivationType"):
            val = subject.get(key)
            if _is_scalar(val):
                out.append((f"subject.{key}", val))
        ids = subject.get("id")
        if isinstance(ids, list):
            for idx, val in enumerate(ids[:3]):
                if _is_scalar(val):
                    out.append((f"subject.id[{idx}]", val))
        snap = subject.get("enrichedSnapshot")
        if isinstance(snap, dict) and snap:
            _walk(snap, "subject.enrichedSnapshot", out)

    # Dedupe by normalized path (prefer first non-empty)
    seen: dict[str, object] = {}
    for path, val in out:
        norm = normalize_enriched_path(path)
        if norm not in seen or not _is_scalar(seen[norm]):
            seen[norm] = val
    return sorted(seen.items(), key=lambda x: x[0])


_DELETE_SNAPSHOT_ID_RE = re.compile(
    r"^subject\.enrichedsnapshot\.(?:teams|roles)\[\d+\]\.id$",
    re.IGNORECASE,
)


def infer_source_system(path: str, operation: str | None = None) -> tuple[str, str]:
    """Best-effort source label when registry has no mapping row."""
    p = path.lower()
    base_op = (operation or "").split("(", 1)[0].strip()
    if base_op in {"deleteTeams", "deleteRoles"}:
        if _DELETE_SNAPSHOT_ID_RE.match(path) or path == "subject.enrichedsnapshot.role.id":
            return "GraphQL", "mutation input ids (deleted entity)"
    # Subject envelope — validated against the GraphQL mutation response we sent.
    if p == "subject.type":
        return "GraphQL", "mutation response / subject.type"
    if p.startswith("subject.id"):
        return "GraphQL", "mutation response subject.id (mutation target)"
    # Actor identity (globalUserId / globalCustomerId / orgId) is carried by the
    # Bearer token (JWT) that triggered the event — it isn't fetched from an external
    # source. Label it as such so the Result view shows "Bearer token" instead of "-".
    if p.startswith("actor.") and p.split(".")[-1] in {
        "globaluserid",
        "globalcustomerid",
        "orgid",
        "parentcustomerid",
    }:
        return "Bearer token", "JWT claim (actor identity)"
    # Mutation input / subject join keys — compare to GraphQL curl response, not Raw echo.
    leaf = p.split(".")[-1].split("[")[0]
    if leaf in {
        "familyids",
        "styleids",
        "variationids",
        "md5s",
        "listids",
        "projectids",
        "assetids",
        "ids",
    } or p.endswith(".familyids") or ".familyids[" in p or ".styleids[" in p or ".md5s[" in p:
        return "GraphQL", "mutation response id list (join key)"

    if "fontdetails" in p or ".family." in p or ".styles[" in p or "variations" in p:
        # fontDetails.* (family/styles/variations catalog objects) are enriched from
        # Discovery/Typesense per the QA sheet — NOT the GraphQL mutation echo. Route
        # every catalog leaf (including `.catalog.id`) to Typesense so we compare
        # against a source we can actually fetch (avoids false "GraphQL not captured").
        if "variation" in p and ("md5" in p or "catalog" in p):
            return "Typesense", "GET /v1/variations"
        if ".catalog." in p or ".catalog" in p or "catalog" in p:
            return "Typesense", "POST /v1/styles (catalog)"
        # A bare mutation-target id (e.g. fontDetails[0].id with no catalog) is a join
        # key from the GraphQL response.
        if p.endswith(".id") and ".catalog." not in p and ".family." not in p and ".styles[" not in p:
            return "GraphQL", "mutation response entity id (join key)"
        return "Typesense", "POST /v1/styles"
    # Asset / customer / user `.source` literals are enricher constants
    # (customer-management-service, user-management-service, …) — not CMS/UMS/AMS columns.
    if "enrichedsnapshot" in p and leaf == "source":
        if ".customer." in p:
            return "Audit service", "enricher constant (customer-management-service)"
        if ".user." in p or ".profile." in p or ".role." in p or ".team." in p:
            return "Audit service", "enricher constant (user-management-service)"
        if ".asset." in p:
            return "Audit service", "enricher constant (asset-management-service)"
        return "Audit service", "enricher constant (source stamp)"
    if "enrichedsnapshot" in p:
        if leaf == "isshared":
            return "Audit service", "derived (computeIsShared)"
        if leaf in {"rootancestorassetid", "rootancestorassettype"}:
            return "Audit service", "derived (asset path)"
    if "sharinginfo" in p:
        # sharingInfo is derived from AMS's sharing/bulk endpoint (needs the AMS API key)
        # + ACCESS_ID_MAP. We don't probe it, so accept the resolver's value.
        return "Audit service", "derived (AMS sharing/bulk + ACCESS_ID_MAP)"
    if ".asset." in p or p.endswith(".asset.id"):
        return "AMS", "GET /v2/type/{type}/asset/{id}"
    if ".customer." in p or ".subscription." in p:
        return "CMS", "GET /api/v2/customers/{gcid}"
    if "customlogo" in p:
        return "CMS", "GET /api/v2/customers/{gcid} (metaData)"
    if "deletedprofiles" in p:
        # deleteProfiles subject.enrichedSnapshot.deletedProfiles[*] — profile gone;
        # user details rehydrated via UMS GET /api/v3/users?idpUserId=…
        return "UMS", "GET /api/v3/users?idpUserId=…"
    if ".user.profile" in p or ".user.role" in p or ".profile." in p:
        if ".role." in p:
            return "UMS", "GET /api/v3/customers/{gcid}/roles"
        return "UMS", "POST/GET /api/v3/customers/{gcid}/profiles"
    # Service-account snapshots expose `users[]` (UMS profiles with userType=service).
    if ".users[" in p and "enrichedsnapshot" in p:
        return "UMS", "POST /api/v3/customers/{gcid}/profiles (service)"
    if ".role." in p:
        return "UMS", "GET /api/v3/customers/{gcid}/roles"
    if ".team." in p or ".teams[" in p:
        return "UMS", "GET /api/v3/customers/{gcid}/teams"
    if ".invitation" in p:
        return "UMS", "GET UMS invitation"
    if ".privatetag" in p or ".tags[" in p:
        return "UMS/Search", "private tags index"
    # BYOF contract / batch-orchestration blocks: the resolver sources these from the
    # BYOF-License and Batch-Orchestration services, which the validator does not probe
    # today. Accept the enriched value as-is (PASS) rather than emitting false SKIP/FAIL.
    if ".contract." in p or p.endswith(".contract"):
        return "BYOF-License", "BYOF-License API (accepted; not probed)"
    if "batchdetails" in p:
        return "Batch-Orchestration", "Batch-Orchestration API (accepted; not probed)"
    if leaf in {
        "isimportedfont",
        "activationtype",
        "activationmode",
        "deactivationtype",
        "isproductionfont",
        "isfavorite",
        "isenabled",
    }:
        if p.startswith("subject.") and leaf in {
            "activationtype",
            "activationmode",
            "deactivationtype",
        }:
            return "Trigger", "GraphQL mutation input / resolver default"
        return "Audit service", "enricher-added flag/default"
    if p in {"enrichedeventid", "enrichmentversion", "enrichedat"}:
        return "Audit service", "enricher-generated"
    # Envelope fields come from the GraphQL/curl trigger we sent (headers + response),
    # not from a Raw Mongo echo.
    if p.startswith("source.") or p in {
        "xcorrelationid",
        "eventid",
        "eventversion",
        "occurredat",
        "routingkey",
    }:
        return "Trigger", "GraphQL curl / event trigger"
    # Remaining snapshot leaves with no mapped external API — accept as enricher output.
    # (Probed systems above already claimed their fields; do not FAIL these against DB.)
    if "enrichedsnapshot" in p:
        return "Audit service", "enricher snapshot (not independently sourced)"
    # Never emit bare Unknown — fall back to trigger envelope for anything left.
    return "Trigger", "event trigger / mutation response"


def display_node_subnode(path: str) -> tuple[str, str, str]:
    """Split enriched path into field label + node/subnode for Excel."""
    if path.startswith("actor.enrichedSnapshot."):
        rest = path[len("actor.enrichedSnapshot.") :]
    elif path.startswith("subject.enrichedSnapshot."):
        rest = path[len("subject.enrichedSnapshot.") :]
    else:
        parts = path.split(".")
        return parts[-1], "", ""
    parts = re.split(r"\.(?=[^[\]]*(?:\[|$))", rest)
    if len(parts) >= 2:
        return parts[-1], parts[0], "/".join(parts[1:-1]) if len(parts) > 2 else parts[1]
    return rest, "", ""
