"""Resolve *working* GraphQL / inventory IDs for Edit&Send and curl.

Catalog search IDs and stale Mongo ``subject.metadata.input`` often fail with
``NOT_FOUND``. Prefer live ``getFamilies`` / favorites inventory for the Bearer
tenant, then enriched snapshot IDs, then .env seeds — and verify each ID still
exists before putting it in a payload.
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger(__name__)

_TIMEOUT = float(os.getenv("LIVE_SEEDS_TIMEOUT_SEC", "25"))

# Ops that need a family that is NOT already ACTIVATED (activate / add to fav).
_WANT_DEACTIVATED_FAMILY = frozenset(
    {
        "activateFamily",
        "addFavoriteFamilies",
        "bulkActivateFamilies",
    }
)
# Ops that need an already-ACTIVATED family.
_WANT_ACTIVATED_FAMILY = frozenset(
    {
        "deactivateFamilies",
        "bulkDeactivateFamilies",
    }
)


def _ensure_env(project_root: Path | None = None) -> None:
    try:
        from dotenv import load_dotenv

        root = project_root or Path(__file__).resolve().parents[2]
        load_dotenv(root / ".env", override=False)
    except Exception:
        pass


def _bearer(project_root: Path | None = None) -> str:
    _ensure_env(project_root)
    from .curl_builder import _resolve_bearer

    auth, real = _resolve_bearer()
    return auth if real else ""


def _graph_url() -> str:
    return (
        os.getenv("NEXTGEN_GRAPHQL_ENDPOINT")
        or os.getenv("GRAPHQL_ENDPOINT")
        or "https://nextgen.monotype-pp.com/graph"
    ).rstrip("/")


def _gql(
    document: str,
    variables: dict[str, Any],
    *,
    project_root: Path | None = None,
) -> dict[str, Any]:
    auth = _bearer(project_root)
    if not auth:
        return {}
    headers = {
        "Authorization": auth,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "x-correlation-id": str(uuid.uuid4()),
    }
    try:
        resp = requests.post(
            _graph_url(),
            headers=headers,
            json={"query": document, "variables": variables},
            timeout=_TIMEOUT,
        )
        data = resp.json()
    except Exception as exc:
        log.debug("live_seeds GraphQL failed: %s", exc)
        return {}
    if not isinstance(data, dict) or data.get("errors"):
        return {}
    return data.get("data") or {}


def family_activation_state(
    family_id: str, *, project_root: Path | None = None
) -> str | None:
    """Return ACTIVATED / DEACTIVATED / None if family missing for this tenant."""
    if not family_id:
        return None
    from .simulation.graphql_loader import load_graphql_documents

    doc = load_graphql_documents().get("GET_FAMILY_BY_ID") or (
        "query GetFamilyById($ids: [ID!]!) { getFamilies(input: { ids: $ids }) { "
        "nodes { ... on InventoryFamily { id activatedStatus { activationState } } } } }"
    )
    data = _gql(doc, {"ids": [family_id]}, project_root=project_root)
    nodes = ((data.get("getFamilies") or {}).get("nodes") or [])
    if not nodes:
        return None
    return (nodes[0].get("activatedStatus") or {}).get("activationState")


def _list_inventory_families(
    limit: int = 40, *, project_root: Path | None = None
) -> list[dict[str, Any]]:
    """Pull families from favorites (inventory-backed) for the current customer."""
    from .simulation.graphql_loader import load_graphql_documents

    docs = load_graphql_documents()
    fav_doc = docs.get("GET_FAVORITES") or ""
    out: list[dict[str, Any]] = []
    if fav_doc:
        data = _gql(
            fav_doc,
            {"input": {"pagination": {"skip": 0, "limit": limit}}},
            project_root=project_root,
        )
        nodes = (data.get("getFavorites") or {}).get("nodes") or []
        for n in nodes:
            fid = str(n.get("id") or "")
            if not fid:
                continue
            state = ((n.get("activatedStatus") or {}).get("activationState")) or ""
            out.append({"id": fid, "activationState": state, "source": "favorites"})
    return out


def _ids_from_enriched(operation: str, project_root: Path) -> list[str]:
    """Best-effort family ids from latest enrich sample / Mongo-shaped payload files."""
    ids: list[str] = []
    try:
        from .source_validation.runner import _load_enriched_sample

        enriched = _load_enriched_sample(project_root, operation)
    except Exception:
        enriched = None
    if not isinstance(enriched, dict):
        return ids
    meta = ((enriched.get("subject") or {}).get("metadata") or {}).get("input") or {}
    for key in ("familyIds", "ids"):
        vals = meta.get(key)
        if isinstance(vals, list):
            ids.extend(str(v) for v in vals if v)
    subj = enriched.get("subject") or {}
    sid = subj.get("id")
    if isinstance(sid, list):
        ids.extend(str(v) for v in sid if v)
    elif sid:
        ids.append(str(sid))
    for fd in ((subj.get("enrichedSnapshot") or {}).get("fontDetails") or [])[:5]:
        fam = (fd or {}).get("family") or {}
        if fam.get("id"):
            ids.append(str(fam["id"]))
    # dedupe preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            uniq.append(i)
    return uniq


def pick_working_family_id(
    operation: str,
    *,
    candidates: list[str] | None = None,
    project_root: Path | None = None,
) -> str:
    """Return a familyId that exists for this Bearer tenant and matches op intent."""
    root = project_root or Path(".").resolve()
    want_deact = operation in _WANT_DEACTIVATED_FAMILY
    want_act = operation in _WANT_ACTIVATED_FAMILY

    ordered: list[str] = []
    for c in candidates or []:
        if c and str(c) not in ordered:
            ordered.append(str(c))
    for c in _ids_from_enriched(operation, root):
        if c not in ordered:
            ordered.append(c)
    env_seed = (os.getenv("SEED_FAMILY_ID") or "").strip()
    if env_seed and env_seed not in ordered:
        ordered.append(env_seed)

    _ensure_env(root)
    inventory = _list_inventory_families(project_root=root)
    known_state = {row["id"]: row.get("activationState") or "" for row in inventory}
    for row in inventory:
        fid = row["id"]
        if fid not in ordered:
            ordered.append(fid)

    def _score(fid: str) -> tuple[int, str]:
        state = known_state.get(fid)
        if not state:
            state = family_activation_state(fid, project_root=root) or ""
            known_state[fid] = state
        if not state:
            return (99, fid)  # missing for this tenant
        if want_deact and state == "DEACTIVATED":
            return (0, fid)
        if want_act and state == "ACTIVATED":
            return (0, fid)
        if want_deact and state == "ACTIVATED":
            return (2, fid)  # usable after deactivate-first
        if want_act and state == "DEACTIVATED":
            return (2, fid)
        return (1, fid)

    scored = sorted((_score(f) for f in ordered), key=lambda t: t[0])
    for rank, fid in scored:
        if rank < 99:
            log.info(
                "live_seeds %s → familyId=%s state=%s (rank=%s)",
                operation,
                fid,
                known_state.get(fid),
                rank,
            )
            return fid
    log.warning("live_seeds: no working familyId for %s", operation)
    return env_seed or (ordered[0] if ordered else "")


def ensure_working_graphql_variables(
    operation: str,
    variables: dict[str, Any],
    *,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Rewrite familyIds (and similar) so the payload targets real inventory data."""
    import copy

    out = copy.deepcopy(variables) if variables else {}
    inp = out.get("input")
    if not isinstance(inp, dict):
        # Ops with top-level keys — leave alone unless we know them
        return out

    # Family-targeted mutations
    if "familyIds" in inp or operation in (
        _WANT_ACTIVATED_FAMILY | _WANT_DEACTIVATED_FAMILY | {"addFavoriteFamilies", "removeFavoriteFamilies"}
    ):
        current = inp.get("familyIds") if isinstance(inp.get("familyIds"), list) else []
        candidates = [str(x) for x in current if x]
        # Validate first candidate; refresh if missing / wrong
        pick = ""
        if candidates:
            st = family_activation_state(candidates[0])
            if st is not None:
                # Exists — for activate prefer not ACTIVATED when possible
                if operation in _WANT_DEACTIVATED_FAMILY and st == "ACTIVATED":
                    pick = pick_working_family_id(
                        operation, candidates=candidates, project_root=project_root
                    )
                elif operation in _WANT_ACTIVATED_FAMILY and st == "DEACTIVATED":
                    pick = pick_working_family_id(
                        operation, candidates=candidates, project_root=project_root
                    )
                else:
                    pick = candidates[0]
        if not pick:
            pick = pick_working_family_id(
                operation, candidates=candidates, project_root=project_root
            )
        if pick:
            inp["familyIds"] = [pick]

    return out
