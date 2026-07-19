"""
GraphQL simulation flows — executes operations against mtconnect-api.
Documents are loaded from audit_validator/data/graphql_documents.json.
"""

from __future__ import annotations

import logging
import os
from typing import Any
import re
import time
import uuid
from ..auth import customer_context_header_id
from ..utility.operation_graphql import get_document_for_operation, get_export_for_operation
from .client import DualEndpointGraphQLClient, GraphQLClient, operation_name_from_document
from .graphql_loader import load_graphql_documents
from .config import GraphQLSimulationConfig
from .operation_runner import FlowContext, OperationResult, run_operation

log = logging.getLogger(__name__)


def _audit_operation(operation: str) -> str:
    """Strip flow labels like 'createAsset (FontList)' → 'createAsset'."""
    return re.sub(r"\s*\([^)]+\)", "", operation).strip()


def _parse_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _doc(cfg: GraphQLSimulationConfig, operation: str) -> str:
    doc = get_document_for_operation(_audit_operation(operation))
    if not doc:
        export = get_export_for_operation(_audit_operation(operation)) or "?"
        raise KeyError(f"GraphQL document not found for {operation} ({export})")
    return doc


def _nextgen_request(ctx: FlowContext, cfg: GraphQLSimulationConfig, operation: str, variables: dict) -> dict:
    doc = _doc(cfg, operation)
    return ctx.nextgen_client.request_apollo(
        operation_name_from_document(doc),
        doc,
        variables,
        browser=True,
    )


def _family_activation_state(data: dict, root: str) -> str | None:
    """Read activationState from activateFamily / deactivateFamilies response."""
    nodes = (((data.get(root) or {}).get("families") or {}).get("nodes") or [])
    if not nodes:
        return None
    return (nodes[0].get("activatedStatus") or {}).get("activationState")


def _style_activation_state(data: dict, root: str) -> str | None:
    payload = data.get(root) or {}
    styles = payload.get("styles") or {}
    nodes = styles.get("nodes") or []
    if not nodes:
        return None
    return (nodes[0].get("activatedStatus") or {}).get("activationState")


def _query_family_activation_state(
    ctx: FlowContext, cfg: GraphQLSimulationConfig, family_id: str
) -> str | None:
    """Pre-check family activation via getFamilies (matches UI inventory ids)."""
    try:
        doc = load_graphql_documents()["GET_FAMILY_BY_ID"]
        data = ctx.nextgen_client.request_apollo(
            "GetFamilyById",
            doc,
            {"ids": [family_id]},
            browser=True,
        )
    except Exception as exc:
        log.debug("getFamilies pre-check failed for %s: %s", family_id, exc)
        return None
    nodes = ((data.get("getFamilies") or {}).get("nodes") or [])
    if not nodes:
        return None
    return (nodes[0].get("activatedStatus") or {}).get("activationState")


def _query_style_activation_state(
    ctx: FlowContext, cfg: GraphQLSimulationConfig, style_id: str
) -> str | None:
    """Best-effort style state lookup — returns None when family context is unknown."""
    _ = ctx, cfg, style_id
    return None


def _activate_family_for_notification(
    ctx: FlowContext,
    cfg: GraphQLSimulationConfig,
    family_id: str,
    *,
    list_ids: list[str] | None = None,
    list_type: str | None = None,
    project_id: str | None = None,
) -> dict:
    """UI-exact activateFamily — optional list/favourite/project scope (4+ touchpoints).

    Touchpoints (raw ``subject.metadata.input`` differs; enrich snapshot is family-catalog):
    - Discovery: familyIds only
    - List: listIds + listType=FONTLIST
    - Favourite: listType=FAVORITE
    - Project: listIds=["project_<id>"] + listType=FONTPROJECT + projectId
    - Project>List: listIds + listType=FONTLIST + projectId
    """
    state = _query_family_activation_state(ctx, cfg, family_id)
    if state == "ACTIVATED" and not list_type:
        # Only force global deactivate→activate transition for Discovery path
        log.info(
            "activateFamily: family %s already ACTIVATED — deactivating first for transition",
            family_id,
        )
        _nextgen_request(
            ctx,
            cfg,
            "deactivateFamilies",
            {
                "input": {
                    "familyIds": [family_id],
                    "deactivationType": "PERMANENT",
                }
            },
        )
        _settle_after_mutation()
    inp: dict = {
        "familyIds": [family_id],
        "activationType": "PERMANENT",
    }
    if list_ids:
        inp["listIds"] = list_ids
    if list_type:
        inp["listType"] = list_type
    if project_id:
        inp["projectId"] = project_id
    return _nextgen_request(ctx, cfg, "activateFamily", {"input": inp})


def _deactivate_family_for_notification(
    ctx: FlowContext, cfg: GraphQLSimulationConfig, family_id: str
) -> dict:
    """UI-exact deactivateFamilies — ensure ACTIVATED→DEACTIVATED transition when possible."""
    state = _query_family_activation_state(ctx, cfg, family_id)
    if state == "DEACTIVATED":
        log.info(
            "deactivateFamilies: family %s already DEACTIVATED — activating first for transition",
            family_id,
        )
        _nextgen_request(
            ctx,
            cfg,
            "activateFamily",
            {"input": {"familyIds": [family_id]}},
        )
        _settle_after_mutation()
    return _nextgen_request(
        ctx,
        cfg,
        "deactivateFamilies",
        {
            "input": {
                "familyIds": [family_id],
                "deactivationType": "PERMANENT",
            }
        },
    )


def _activate_style_for_notification(
    ctx: FlowContext, cfg: GraphQLSimulationConfig, style_id: str
) -> dict:
    state = _query_style_activation_state(ctx, cfg, style_id)
    if state == "ACTIVATED":
        log.info(
            "activateStyle: style %s already ACTIVATED — deactivating first for transition",
            style_id,
        )
        _nextgen_request(
            ctx,
            cfg,
            "deactivateStyle",
            {"input": {"styleIds": [style_id]}},
        )
        _settle_after_mutation()
    return _nextgen_request(
        ctx,
        cfg,
        "activateStyle",
        {"input": {"styleIds": [style_id]}},
    )


def _deactivate_style_for_notification(
    ctx: FlowContext, cfg: GraphQLSimulationConfig, style_id: str
) -> dict:
    state = _query_style_activation_state(ctx, cfg, style_id)
    if state == "DEACTIVATED":
        log.info(
            "deactivateStyle: style %s already DEACTIVATED — activating first for transition",
            style_id,
        )
        _nextgen_request(
            ctx,
            cfg,
            "activateStyle",
            {"input": {"styleIds": [style_id]}},
        )
        _settle_after_mutation()
    return _nextgen_request(
        ctx,
        cfg,
        "deactivateStyle",
        {"input": {"styleIds": [style_id]}},
    )


def _settle_after_mutation(sec: float | None = None) -> None:
    gap = sec if sec is not None else float(os.getenv("SIMULATION_MUTATION_GAP_SEC", "1.5"))
    if gap > 0:
        time.sleep(gap)


def _activate_variation_for_notification(
    ctx: FlowContext, cfg: GraphQLSimulationConfig, style_id: str, md5: str
) -> dict:
    return _nextgen_request(
        ctx,
        cfg,
        "activateVariation",
        {"input": {"variations": [{"styleId": style_id, "md5": md5}]}},
    )


def _deactivate_variation_for_notification(
    ctx: FlowContext, cfg: GraphQLSimulationConfig, style_id: str, md5: str
) -> dict:
    try:
        _nextgen_request(
            ctx,
            cfg,
            "activateVariation",
            {"input": {"variations": [{"styleId": style_id, "md5": md5}]}},
        )
    except Exception:
        pass
    return _nextgen_request(
        ctx,
        cfg,
        "deactivateVariation",
        {"input": {"md5s": [md5]}},
    )


def _activate_list_for_notification(
    ctx: FlowContext, cfg: GraphQLSimulationConfig, list_id: str
) -> dict:
    try:
        ctx.client.request(
            _doc(cfg, "deActivateList"),
            {"input": {"listId": list_id, "listType": "FONTLIST", "deactivationType": "PERMANENT"}},
        )
    except Exception:
        pass
    return ctx.client.request(
        _doc(cfg, "activateList"),
        {"input": {"listId": list_id, "listType": "FONTLIST", "activationType": "PERMANENT"}},
    )


def _deactivate_list_for_notification(
    ctx: FlowContext, cfg: GraphQLSimulationConfig, list_id: str
) -> dict:
    try:
        ctx.client.request(
            _doc(cfg, "activateList"),
            {"input": {"listId": list_id, "listType": "FONTLIST", "activationType": "PERMANENT"}},
        )
    except Exception:
        pass
    return ctx.client.request(
        _doc(cfg, "deActivateList"),
        {"input": {"listId": list_id, "listType": "FONTLIST", "deactivationType": "PERMANENT"}},
    )


def _add_favorite_families_for_notification(
    ctx: FlowContext, cfg: GraphQLSimulationConfig, family_id: str
) -> dict:
    return _nextgen_request(
        ctx,
        cfg,
        "addFavoriteFamilies",
        {"input": {"familyIds": [family_id]}},
    )


def _add_favorite_styles_for_notification(
    ctx: FlowContext, cfg: GraphQLSimulationConfig, style_id: str
) -> dict:
    try:
        _nextgen_request(
            ctx,
            cfg,
            "removeFavoriteStyles",
            {"input": {"styleIds": [style_id]}},
        )
    except Exception:
        pass
    return _nextgen_request(
        ctx,
        cfg,
        "addFavoriteStyles",
        {"input": {"styleIds": [style_id]}},
    )


def _project_family_ids(cfg: GraphQLSimulationConfig) -> list[str]:
    if cfg.seed.family_ids:
        return list(cfg.seed.family_ids)
    if cfg.seed.family_id:
        return [cfg.seed.family_id]
    return []


def _accounts_customer_update(cfg: GraphQLSimulationConfig) -> dict:
    from .accounts_client import AccountsApiClient

    if not cfg.accounts or not cfg.seed.shopify_customer_gid:
        raise RuntimeError("ACCOUNTS_API_TOKEN and SHOPIFY_CUSTOMER_GID are required")
    client = AccountsApiClient(cfg.accounts)
    suffix = str(int(time.time()) % 1000)
    data = client.customer_update(
        shopify_customer_gid=cfg.seed.shopify_customer_gid,
        first_name="Sachin",
        last_name=f"Koirala Tester {suffix}",
        current_first_name="Sachin",
        current_last_name="Koirala",
    )
    return {"updateCustomer": {"success": True, "customerUpdate": data.get("customerUpdate")}}


def _reset_password(cfg: GraphQLSimulationConfig) -> dict:
    from .accounts_client import AccountsApiClient

    if not cfg.accounts:
        raise RuntimeError("ACCOUNTS_API_TOKEN is required for resetPassword")
    AccountsApiClient(cfg.accounts).request_password_reset()
    return {"resetPassword": {"success": True}}


def _profile_nodes(ctx: FlowContext, cfg: GraphQLSimulationConfig, *, limit: int = 10) -> list[dict]:
    resp = ctx.client.request(
        _doc(cfg, "getProfiles"),
        {"pagination": {"skip": 0, "limit": limit}},
    )
    return (resp.get("getProfiles") or {}).get("nodes") or []


def _other_profile_ids(ctx: FlowContext, cfg: GraphQLSimulationConfig, *, limit: int = 5) -> list[str]:
    return [
        node["id"]
        for node in _profile_nodes(ctx, cfg, limit=limit + 1)
        if node.get("id") and node["id"] != ctx.profile_id
    ][:limit]


def _resolve_sharee_id(ctx: FlowContext, cfg: GraphQLSimulationConfig) -> str:
    seed_sharee = (cfg.seed.sharee_id or "").strip()
    if seed_sharee and seed_sharee != ctx.profile_id:
        return seed_sharee
    other_profile = (os.getenv("OTHER_PROFILE_ID", "") or "").strip()
    if other_profile and other_profile != ctx.profile_id:
        return other_profile
    others = _other_profile_ids(ctx, cfg, limit=1)
    return others[0] if others else ""


def _secondary_client(cfg: GraphQLSimulationConfig) -> GraphQLClient | DualEndpointGraphQLClient:
    if cfg.route_mutations_to_bff:
        return DualEndpointGraphQLClient(cfg, bearer_token=cfg.secondary_bearer_token)
    return GraphQLClient(cfg, bearer_token=cfg.secondary_bearer_token)


def _grant_asset_access(
    ctx: FlowContext,
    cfg: GraphQLSimulationConfig,
    *,
    asset_id: str,
    asset_type: str,
    sharee_id: str,
    label: str,
    access_id: int = 27,
) -> None:
    ctx.results.append(
        run_operation(
            ctx,
            label,
            lambda: _nextgen_request(
                ctx,
                cfg,
                "updateAssetSharing",
                {
                    "input": {
                        "assetId": asset_id,
                        "assetType": asset_type,
                        "notify": True,
                        "data": [
                            {
                                "action": "GRANT",
                                "payload": [
                                    {
                                        "shareeType": "User",
                                        "accessIdMap": [{"accessId": access_id, "shareeId": sharee_id}],
                                    }
                                ],
                            }
                        ],
                    }
                },
            ),
            skip=not asset_id or not sharee_id,
        )
    )


def _revoke_asset_access(
    ctx: FlowContext,
    cfg: GraphQLSimulationConfig,
    *,
    asset_id: str,
    asset_type: str,
    sharee_id: str,
    label: str,
) -> None:
    ctx.results.append(
        run_operation(
            ctx,
            label,
            lambda: _nextgen_request(
                ctx,
                cfg,
                "updateAssetSharing",
                {
                    "input": {
                        "assetId": asset_id,
                        "assetType": asset_type,
                        "notify": True,
                        "data": [
                            {
                                "action": "REVOKE",
                                "payload": [
                                    {
                                        "shareeType": "User",
                                        "accessIdMap": [{"shareeId": sharee_id}],
                                    }
                                ],
                            }
                        ],
                    }
                },
            ),
            skip=not asset_id or not sharee_id,
        )
    )


def font_activation_flow(ctx: FlowContext, cfg: GraphQLSimulationConfig) -> None:
    s = cfg.seed
    activate_family = s.family_id or "910042901"
    deactivate_family = s.deactivate_family_id or "8kL8ZM64"
    activate_style = s.style_id or "920374778"
    deactivate_style = s.deactivate_style_id or s.style_id or "920374778"
    variation_style = s.variation_style_id or s.style_id or "e7z4R6sG"
    activate_md5 = s.variation_md5 or "b783215634650cf0a55e0d723123d5e0"
    deactivate_md5 = s.deactivate_variation_md5 or activate_md5
    ctx.results.append(
        run_operation(
            ctx,
            "deactivateFamilies",
            lambda fam=deactivate_family: _deactivate_family_for_notification(ctx, cfg, fam),
            skip=not deactivate_family,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "activateFamily",
            lambda fam=activate_family: _activate_family_for_notification(ctx, cfg, fam),
            skip=not activate_family,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "activateStyle",
            lambda st=activate_style: _activate_style_for_notification(ctx, cfg, st),
            skip=not activate_style,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "deactivateStyle",
            lambda st=deactivate_style: _deactivate_style_for_notification(ctx, cfg, st),
            skip=not deactivate_style,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "activateVariation",
            lambda st=variation_style, md5=activate_md5: _activate_variation_for_notification(
                ctx, cfg, st, md5
            ),
            skip=not variation_style or not activate_md5,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "deactivateVariation",
            lambda st=variation_style, md5=deactivate_md5: _deactivate_variation_for_notification(
                ctx, cfg, st, md5
            ),
            skip=not variation_style or not deactivate_md5,
        )
    )
    activate_styles = list(s.bulk_activate_styles) if s.bulk_activate_styles else (
        [{"id": activate_style}] if activate_style else []
    )
    deactivate_styles = list(s.bulk_activate_styles) if s.bulk_activate_styles else (
        [{"id": deactivate_style}] if deactivate_style else []
    )
    ctx.results.append(
        run_operation(
            ctx,
            "bulkActivateStyles",
            lambda styles=activate_styles: _nextgen_request(
                ctx,
                cfg,
                "bulkActivateStyles",
                {"input": {"styles": styles, "activationType": "PERMANENT"}},
            ),
            skip=not activate_styles,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "bulkDeactivateStyles",
            lambda styles=deactivate_styles: _nextgen_request(
                ctx,
                cfg,
                "bulkDeactivateStyles",
                {"input": {"styles": styles, "deactivationType": "PERMANENT"}},
            ),
            skip=not deactivate_styles,
        )
    )


    ctx.results.append(
        run_operation(
            ctx,
            "syncUnSyncVariations",
            lambda: ctx.client.request(
                _doc(cfg, "syncUnSyncVariations"),
                {"input": {"md5s": [s.variation_md5] if s.variation_md5 else [], "operation": "SYNC"}},
            ),
            skip=not s.variation_md5,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "bulkActivateAll",
            lambda: ctx.client.request(
                _doc(cfg, "bulkActivateAll"),
                {"input": {"resourceType": "FAVORITE"}},
            ),
        )
    )


def favorites_flow(ctx: FlowContext, cfg: GraphQLSimulationConfig) -> None:
    s = cfg.seed
    ctx.results.append(
        run_operation(
            ctx,
            "getFavorites",
            lambda: ctx.client.request(
                _doc(cfg, "getFavorites"),
                {"input": {"pagination": {"skip": 0, "limit": 10}}},
            ),
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "addFavoriteFamilies",
            lambda fam=s.favorite_family_id: _add_favorite_families_for_notification(
                ctx, cfg, fam
            ),
            skip=not s.favorite_family_id,
        )
    )
    # Favourite touchpoint: activateFamily with listType=FAVORITE (raw metadata differs)
    ctx.results.append(
        run_operation(
            ctx,
            "activateFamily",
            lambda fam=s.favorite_family_id or s.family_id: _activate_family_for_notification(
                ctx,
                cfg,
                fam,
                list_type="FAVORITE",
            ),
            skip=not (s.favorite_family_id or s.family_id),
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "removeFavoriteFamilies",
            lambda: ctx.client.request(
                _doc(cfg, "removeFavoriteFamilies"),
                {"input": {"familyIds": [s.favorite_family_id]}},
            ),
            skip=not s.favorite_family_id,
        )
    )
    favourite_styles = list(s.bulk_favourite_styles) if s.bulk_favourite_styles else (
        [{"id": s.style_id}] if s.style_id else []
    )
    ctx.results.append(
        run_operation(
            ctx,
            "bulkAddStylesToFavourites",
            lambda: _nextgen_request(
                ctx,
                cfg,
                "bulkAddStylesToFavourites",
                {"input": {"styles": favourite_styles}},
            ),
            skip=not favourite_styles,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "bulkRemoveStylesFromFavourites",
            lambda: _nextgen_request(
                ctx,
                cfg,
                "bulkRemoveStylesFromFavourites",
                {"input": {"styles": favourite_styles}},
            ),
            skip=not favourite_styles,
            expected_to_fail=True,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "addFavoriteStyles",
            lambda st=s.favorite_style_id: _add_favorite_styles_for_notification(
                ctx, cfg, st
            ),
            skip=not s.favorite_style_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "removeFavoriteStyles",
            lambda: ctx.client.request(
                _doc(cfg, "removeFavoriteStyles"),
                {"input": {"styleIds": [s.favorite_style_id]}},
            ),
            skip=not s.favorite_style_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "addFavoritePair",
            lambda: ctx.client.request(
                _doc(cfg, "addFavoritePair"),
                {
                    "input": {
                        "pairs": [
                            {"headline": {"id": s.headline_style_id}, "body": {"id": s.body_style_id}}
                        ]
                    }
                },
            ),
            skip=not s.headline_style_id or not s.body_style_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "removeFavoritePair",
            lambda: ctx.client.request(
                _doc(cfg, "removeFavoritePair"),
                {
                    "input": {
                        "pairs": [
                            {"headline": {"id": s.headline_style_id}, "body": {"id": s.body_style_id}}
                        ]
                    }
                },
            ),
            skip=not s.headline_style_id or not s.body_style_id,
        )
    )


def tags_flow(ctx: FlowContext, cfg: GraphQLSimulationConfig) -> None:
    s = cfg.seed
    state: dict[str, str | None] = {"tag_id": None, "created_tag": False}

    ctx.results.append(
        run_operation(
            ctx,
            "getPrivateTags",
            lambda: _nextgen_request(
                ctx,
                cfg,
                "getPrivateTags",
                {"input": {"filter": {}, "pageInfo": {"skip": 0, "limit": 3}}},
            ),
            skip=not ctx.customer_id,
        )
    )

    create = run_operation(
        ctx,
        "createPrivateTags",
        lambda: _nextgen_request(
            ctx,
            cfg,
            "createPrivateTags",
            {
                "input": {
                    "customerId": ctx.customer_id,
                    "tags": [{"name": f"automation-tag-{int(time.time() * 1000)}"}],
                }
            },
        ),
        skip=not ctx.customer_id,
    )
    ctx.results.append(create)
    if create.status == "PASS" and isinstance(create.response, dict):
        data = (create.response.get("createPrivateTags") or {}).get("data") or []
        if data:
            state["tag_id"] = (data[0].get("tag") or {}).get("id")
            state["created_tag"] = bool(state["tag_id"])
    if not state["tag_id"] and s.tag_id:
        state["tag_id"] = s.tag_id

    tag_id = state["tag_id"] or ""
    updated_name = f"automation-tag-updated-{int(time.time() * 1000)}"
    ctx.results.append(
        run_operation(
            ctx,
            "updatePrivateTag",
            lambda: _nextgen_request(
                ctx,
                cfg,
                "updatePrivateTag",
                {
                    "input": {
                        "tagId": tag_id,
                        "customerId": ctx.customer_id,
                        "name": updated_name,
                    }
                },
            ),
            skip=not tag_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "updatePrivateTagAssociations",
            lambda: _nextgen_request(
                ctx,
                cfg,
                "updatePrivateTagAssociations",
                {
                    "input": {
                        "customerId": ctx.customer_id,
                        "tags": [{"id": tag_id, "associate": [{"styleId": s.style_id}]}],
                    }
                },
            ),
            skip=not tag_id or not s.style_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "updatePrivateTagAssociations (disassociate)",
            lambda: _nextgen_request(
                ctx,
                cfg,
                "updatePrivateTagAssociations",
                {
                    "input": {
                        "customerId": ctx.customer_id,
                        "tags": [{"id": tag_id, "disassociate": [{"styleId": s.style_id}]}],
                    }
                },
            ),
            skip=not tag_id or not s.style_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "bulkTagStyles",
            lambda: ctx.client.request(
                _doc(cfg, "bulkTagStyles"),
                {"input": {"tagId": tag_id, "styles": [{"id": s.style_id}]}},
            ),
            skip=not tag_id or not s.style_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "bulkUntagStyles",
            lambda: ctx.client.request(
                _doc(cfg, "bulkUntagStyles"),
                {"input": {"tagId": tag_id, "styles": [{"id": s.style_id}]}},
            ),
            skip=not tag_id or not s.style_id,
        )
    )
    if state["created_tag"]:
        ctx.results.append(
            run_operation(
                ctx,
                "deletePrivateTags",
                lambda: _nextgen_request(
                    ctx,
                    cfg,
                    "deletePrivateTags",
                    {"input": {"customerId": ctx.customer_id, "tagIds": [tag_id]}},
                ),
                skip=not tag_id,
            )
        )
    else:
        ctx.results.append(
            OperationResult(
                operation="deletePrivateTags",
                status="SKIP",
                duration_ms=0,
                error="Skipped delete — TAG_ID seed is a shared tag",
            )
        )
        ctx.results.append(
            run_operation(ctx, "deleteAllPrivateTags", lambda: ctx.client.request(_doc(cfg, "deleteAllPrivateTags"), {"input": {"customerId": ctx.customer_id}}), skip=True)
        )
        return

    ctx.results.append(
        run_operation(ctx, "deleteAllPrivateTags", lambda: ctx.client.request(_doc(cfg, "deleteAllPrivateTags"), {"input": {"customerId": ctx.customer_id}}), skip=True)
    )


def assets_flow(ctx: FlowContext, cfg: GraphQLSimulationConfig) -> None:
    s = cfg.seed
    state: dict[str, str | None] = {"folder_id": None, "copied_id": None}

    create = run_operation(
        ctx,
        "createAsset",
        lambda: ctx.client.request(
            _doc(cfg, "createAsset"),
            {"input": {"name": f"automation-folder-{int(time.time() * 1000)}", "assetType": "Folder", "accessRight": "FullAccess"}},
        ),
    )
    ctx.results.append(create)
    folder_id = ""
    if isinstance(create.response, dict):
        folder_id = (create.response.get("createAsset") or {}).get("asset", {}).get("id") or ""
    state["folder_id"] = folder_id
    sharee_id = _resolve_sharee_id(ctx, cfg)

    ctx.results.append(
        run_operation(
            ctx,
            "getAssets",
            lambda: ctx.client.request(
                _doc(cfg, "getAssets"),
                {"input": {"accessRights": ["FullAccess"], "pagination": {"skip": 0, "limit": 10}}},
            ),
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "getCompanyAssets",
            lambda: ctx.client.request(
                _doc(cfg, "getCompanyAssets"),
                {"input": {"accessRights": ["FullAccess"], "pagination": {"skip": 0, "limit": 10}}},
            ),
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "getAsset",
            lambda: ctx.client.request(
                _doc(cfg, "getAsset"),
                {"input": {"assetType": "Folder", "assetId": folder_id, "pagination": {"skip": 0, "limit": 10}}},
            ),
            skip=not folder_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "getAssetsFolderSummary",
            lambda: ctx.client.request(
                _doc(cfg, "getAssetsFolderSummary"),
                {"input": {"assetIds": [folder_id]}},
            ),
            skip=not folder_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "updateAsset",
            lambda: ctx.client.request(
                _doc(cfg, "updateAsset"),
                {"input": {"assetType": "Folder", "assetId": folder_id, "name": f"automation-folder-renamed-{int(time.time() * 1000)}"}},
            ),
            skip=not folder_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "updateAssets",
            lambda: ctx.client.request(
                _doc(cfg, "updateAssets"),
                {"input": {"items": [{"assetType": "Folder", "assetId": folder_id, "description": f"automation-folder-bulk-description-{int(time.time() * 1000)}"}]}},
            ),
            skip=not folder_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "updateAssetSharing (GRANT)",
            lambda: _nextgen_request(
                ctx,
                cfg,
                "updateAssetSharing",
                {
                    "input": {
                        "assetId": folder_id,
                        "assetType": "Folder",
                        "notify": True,
                        "data": [
                            {
                                "action": "GRANT",
                                "payload": [
                                    {
                                        "shareeType": "User",
                                        "accessIdMap": [{"accessId": 27, "shareeId": sharee_id}],
                                    }
                                ],
                            }
                        ],
                    }
                },
            ),
            skip=not folder_id or not sharee_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "updateAssetSharing (REVOKE)",
            lambda: _nextgen_request(
                ctx,
                cfg,
                "updateAssetSharing",
                {
                    "input": {
                        "assetId": folder_id,
                        "assetType": "Folder",
                        "notify": True,
                        "data": [
                            {
                                "action": "REVOKE",
                                "payload": [{"shareeType": "User", "accessIdMap": [{"shareeId": sharee_id}]}],
                            }
                        ],
                    }
                },
            ),
            skip=not folder_id or not sharee_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "getAssetsSharings",
            lambda: ctx.client.request(
                _doc(cfg, "getAssetsSharings"),
                {"input": {"assets": [{"assetType": "Folder", "assetIds": [folder_id]}]}},
            ),
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "pinAsset",
            lambda: ctx.client.request(_doc(cfg, "pinAsset"), {"input": {"assetId": folder_id}}),
            skip=not folder_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "getPinnedAssets",
            lambda: ctx.client.request(_doc(cfg, "getPinnedAssets"), {"types": ["Folder"]}),
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "unpinAsset",
            lambda: ctx.client.request(_doc(cfg, "unpinAsset"), {"input": {"assetId": folder_id}}),
            skip=not folder_id,
        )
    )
    copy = run_operation(
        ctx,
        "bulkCopyAssets",
        lambda: ctx.client.request(
            _doc(cfg, "bulkCopyAssets"),
            {"input": {"items": [{"source": {"assetId": folder_id, "assetType": "Folder"}, "target": {"assetId": "root"}}]}},
        ),
        skip=not folder_id,
    )
    ctx.results.append(copy)
    copied_id = ""
    if isinstance(copy.response, dict):
        results = (copy.response.get("bulkCopyAssets") or {}).get("results") or []
        if results:
            copied_id = (results[0].get("copiedAsset") or {}).get("id") or ""
    state["copied_id"] = copied_id
    ctx.results.append(
        run_operation(
            ctx,
            "bulkMoveAssets",
            lambda: ctx.client.request(
                _doc(cfg, "bulkMoveAssets"),
                {"input": {"sourceAssetIds": [copied_id], "targetAssetId": folder_id}},
            ),
            skip=not copied_id or not folder_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "getFamiliesOfAllFontLists",
            lambda: ctx.client.request(_doc(cfg, "getFamiliesOfAllFontLists"), {}),
            skip=True,  # query-only — not validated in E2E for now
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "getStylesOfAllFontLists",
            lambda: ctx.client.request(_doc(cfg, "getStylesOfAllFontLists"), {}),
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "getProjectByDocumentId",
            lambda: ctx.client.request(
                _doc(cfg, "getProjectByDocumentId"),
                {"documentId": os.getenv("DOCUMENT_ID", "doc-1")},
            ),
            skip=True,  # query-only — not validated in E2E for now
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "sharingInfoForAssets",
            lambda: ctx.client.request(
                _doc(cfg, "sharingInfoForAssets"),
                {"assets": [{"id": folder_id, "assetType": "Folder"}]},
            ),
            skip=not folder_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "deleteAssets",
            lambda: ctx.client.request(
                _doc(cfg, "deleteAssets"),
                {"input": {"assets": [{"assetType": "Folder", "assetIds": [folder_id]}]}},
            ),
            skip=not folder_id,
        )
    )


def font_list_flow(ctx: FlowContext, cfg: GraphQLSimulationConfig) -> None:
    s = cfg.seed
    style_filter = {"pagination": {"skip": 0, "limit": 10}}
    state: dict[str, str | None] = {}

    create_list = run_operation(
        ctx,
        "createAsset (FontList)",
        lambda: ctx.client.request(
            _doc(cfg, "createAsset"),
            {"input": {"assetType": "FontList", "name": f"TEST_FontList_{int(time.time() * 1000)}", "accessRight": "FullAccess"}},
        ),
    )
    ctx.results.append(create_list)
    font_list_id = ""
    if isinstance(create_list.response, dict):
        font_list_id = (create_list.response.get("createAsset") or {}).get("asset", {}).get("id") or ""
    state["font_list_id"] = font_list_id

    create_folder = run_operation(
        ctx,
        "createAsset (Folder)",
        lambda: ctx.client.request(
            _doc(cfg, "createAsset"),
            {"input": {"assetType": "Folder", "name": f"TEST_Folder_{int(time.time() * 1000)}", "accessRight": "FullAccess"}},
        ),
    )
    ctx.results.append(create_folder)
    folder_id = ""
    if isinstance(create_folder.response, dict):
        folder_id = (create_folder.response.get("createAsset") or {}).get("asset", {}).get("id") or ""

    ctx.results.append(
        run_operation(
            ctx,
            "addFontListFamilies",
            lambda: ctx.client.request(
                _doc(cfg, "addFontListFamilies"),
                {"input": {"fontListId": font_list_id, "families": {"familyIds": [s.family_id]}}, "styleFilterInput": style_filter},
            ),
            skip=not font_list_id or not s.family_id,
        )
    )
    # List touchpoint: create list → add family → activateFamily with FONTLIST scope
    # (raw metadata.input carries listIds/listType; enrich snapshot stays family-catalog).
    ctx.results.append(
        run_operation(
            ctx,
            "activateFamily",
            lambda fam=s.family_id, lid=font_list_id: _activate_family_for_notification(
                ctx,
                cfg,
                fam,
                list_ids=[lid],
                list_type="FONTLIST",
            ),
            skip=not font_list_id or not s.family_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "addFontListStyles",
            lambda: ctx.client.request(
                _doc(cfg, "addFontListStyles"),
                {"input": {"fontListId": font_list_id, "styles": [{"styleId": s.style_id}]}},
            ),
            skip=not font_list_id or not s.style_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "bulkAddStylesToList",
            lambda: ctx.client.request(
                _doc(cfg, "bulkAddStylesToList"),
                {"input": {"listId": font_list_id, "styles": [{"id": s.style_id}]}},
            ),
            skip=not font_list_id or not s.style_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "activateList",
            lambda lid=font_list_id: _activate_list_for_notification(ctx, cfg, lid),
            skip=not font_list_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "deActivateList",
            lambda lid=font_list_id: _deactivate_list_for_notification(ctx, cfg, lid),
            skip=not font_list_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "removeFontListStyles",
            lambda: ctx.client.request(
                _doc(cfg, "removeFontListStyles"),
                {"input": {"fontListId": font_list_id, "styles": {"styleIds": [s.style_id]}}, "styleFilterInput": style_filter},
            ),
            skip=not font_list_id or not s.style_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "removeFontListFamilies",
            lambda: ctx.client.request(
                _doc(cfg, "removeFontListFamilies"),
                {"input": {"fontListId": font_list_id, "families": {"familyIds": [s.family_id]}}, "styleFilterInput": style_filter},
            ),
            skip=not font_list_id or not s.family_id,
        )
    )
    copy = run_operation(
        ctx,
        "bulkCopyAssets",
        lambda: ctx.client.request(
            _doc(cfg, "bulkCopyAssets"),
            {"input": {"items": [{"source": {"assetId": font_list_id, "assetType": "FontList"}, "target": {"assetId": "root"}}]}},
        ),
        skip=not font_list_id,
    )
    ctx.results.append(copy)
    copied_id = ""
    if isinstance(copy.response, dict):
        results = (copy.response.get("bulkCopyAssets") or {}).get("results") or []
        if results:
            copied_id = (results[0].get("copiedAsset") or {}).get("id") or ""
    ctx.results.append(
        run_operation(
            ctx,
            "bulkMoveAssets",
            lambda: ctx.client.request(
                _doc(cfg, "bulkMoveAssets"),
                {"input": {"sourceAssetIds": [copied_id], "targetAssetId": folder_id}},
            ),
            skip=not copied_id or not folder_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "bulkRemoveStylesFromList",
            lambda: ctx.client.request(
                _doc(cfg, "bulkRemoveStylesFromList"),
                {"input": {"listId": font_list_id, "styles": [{"id": s.style_id}]}},
            ),
            skip=not font_list_id or not s.style_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "deleteAssets (FontList)",
            lambda: ctx.client.request(
                _doc(cfg, "deleteAssets"),
                {"input": {"assets": [{"assetType": "FontList", "assetIds": [font_list_id]}]}},
            ),
            skip=not font_list_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "deleteAssets (Copied FontList)",
            lambda: ctx.client.request(
                _doc(cfg, "deleteAssets"),
                {"input": {"assets": [{"assetType": "FontList", "assetIds": [copied_id]}]}},
            ),
            skip=not copied_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "deleteAssets (Folder)",
            lambda: ctx.client.request(
                _doc(cfg, "deleteAssets"),
                {"input": {"assets": [{"assetType": "Folder", "assetIds": [folder_id]}]}},
            ),
            skip=not folder_id,
        )
    )


def font_project_flow(ctx: FlowContext, cfg: GraphQLSimulationConfig) -> None:
    s = cfg.seed
    project_id = s.project_id or ""
    create = run_operation(
        ctx,
        "createProject",
        lambda: ctx.client.request(
            _doc(cfg, "createProject"),
            {
                "input": {
                    "name": f"automation-project-{int(time.time() * 1000)}",
                    "description": "Automation test project",
                    "allowFontAdditionsByCollaborators": False,
                    "allowFontDownloadsByCollaborators": False,
                    "allowFontImportsByCollaborators": False,
                    "enableProjectLevelImportedFonts": False,
                    "autoActivateFontsForMembers": False,
                }
            },
        ),
        skip=bool(project_id),
        expected_to_fail=not bool(project_id),
    )
    ctx.results.append(create)
    project_id = s.project_id or ""
    if isinstance(create.response, dict) and not project_id:
        project_id = (create.response.get("createProject") or {}).get("asset", {}).get("id") or ""

    family_ids = _project_family_ids(cfg)

    duplicate_project_id = ""

    def _append(op: str, fn, *, skip: bool = False, expected_to_fail: bool = False):
        result = run_operation(ctx, op, fn, skip=skip, expected_to_fail=expected_to_fail)
        ctx.results.append(result)
        return result

    _append(
        "addFontProjectFamilies",
        lambda: _nextgen_request(
            ctx,
            cfg,
            "addFontProjectFamilies",
            {
                "input": {
                    "fontProjectId": project_id,
                    "families": {"familyIds": family_ids},
                }
            },
        ),
        skip=not project_id or not family_ids,
    )
    # Project touchpoint: activateFamily with FONTPROJECT scope (distinct from activateFontProject)
    _append(
        "activateFamily",
        lambda fam=(family_ids[0] if family_ids else s.family_id), pid=project_id: _activate_family_for_notification(
            ctx,
            cfg,
            fam,
            list_ids=[f"project_{pid}"],
            list_type="FONTPROJECT",
            project_id=pid,
        ),
        skip=not project_id or not (family_ids or s.family_id),
    )
    remove_family_id = family_ids[0] if family_ids else ""
    _append(
        "removeFontProjectFamilies",
        lambda rid=remove_family_id: _nextgen_request(
            ctx,
            cfg,
            "removeFontProjectFamilies",
            {"input": {"fontProjectId": project_id, "families": {"familyIds": [rid]}}},
        ),
        skip=not project_id or not remove_family_id,
    )
    _append(
        "addFontProjectStyles",
        lambda: _nextgen_request(
            ctx,
            cfg,
            "addFontProjectStyles",
            {"input": {"fontProjectId": project_id, "styles": [{"styleId": s.style_id}]}},
        ),
        skip=not project_id or not s.style_id,
    )
    _append(
        "updateFontProjectStyles",
        lambda: ctx.client.request(
            _doc(cfg, "updateFontProjectStyles"),
            {
                "input": {
                    "fontProjectId": project_id,
                    "resolutions": [
                        {
                            "styleId": s.style_id,
                            "resolvedMd5": s.variation_md5,
                            "resolvedVariationId": s.variation_id,
                            "unresolvedMd5s": [],
                        }
                    ],
                }
            },
        ),
        skip=not project_id or not s.style_id,
    )
    _append(
        "activateFontProject",
        lambda: ctx.client.request(_doc(cfg, "activateFontProject"), {"input": {"projectId": project_id}}),
        skip=not project_id,
    )
    _append(
        "deActivateFontProject",
        lambda: ctx.client.request(_doc(cfg, "deActivateFontProject"), {"input": {"projectId": project_id}}),
        skip=not project_id,
    )
    doc_id = getattr(s, "document_id", None) or getattr(s, "scanned_document_id", None) or ""
    _append(
        "linkDocumentToProject",
        lambda: ctx.client.request(
            _doc(cfg, "linkDocumentToProject"),
            {"input": {"projectId": project_id, "documentId": doc_id}},
        ),
        skip=not project_id or not doc_id,
    )
    _append(
        "publishProject",
        lambda: ctx.client.request(_doc(cfg, "publishProject"), {"input": {"projectId": project_id}}),
        skip=not project_id,
    )
    sharee_id = _resolve_sharee_id(ctx, cfg)
    _grant_asset_access(
        ctx,
        cfg,
        asset_id=project_id,
        asset_type="FontProject",
        sharee_id=sharee_id,
        label="updateAssetSharing (GRANT project)",
        access_id=cfg.seed.fontproject_contributor_access_id,
    )
    dup = _append(
        "duplicateProject",
        lambda: _nextgen_request(
            ctx,
            cfg,
            "duplicateProject",
            {
                "input": {
                    "sourceProjectId": project_id,
                    "name": f"Automation duplicate {int(time.time() * 1000)}",
                }
            },
        ),
        skip=not project_id,
    )
    if isinstance(dup.response, dict):
        duplicate_project_id = (
            (dup.response.get("duplicateProject") or {}).get("asset") or {}
        ).get("id") or ""
    _append(
        "unlinkDocumentFromProject",
        lambda: ctx.client.request(
            _doc(cfg, "unlinkDocumentFromProject"),
            {"input": {"projectId": project_id, "documentId": doc_id}},
        ),
        skip=not project_id or not doc_id,
    )
    _append(
        "removeFontProjectStyles",
        lambda: _nextgen_request(
            ctx,
            cfg,
            "removeFontProjectStyles",
            {"input": {"fontProjectId": project_id, "styles": {"styleIds": [s.style_id]}}},
        ),
        skip=not project_id or not s.style_id,
    )
    _revoke_asset_access(
        ctx,
        cfg,
        asset_id=project_id,
        asset_type="FontProject",
        sharee_id=sharee_id,
        label="updateAssetSharing (REVOKE project)",
    )
    _append(
        "deleteProject (duplicate)",
        lambda: ctx.client.request(_doc(cfg, "deleteProject"), {"input": {"projectId": duplicate_project_id}}),
        skip=not duplicate_project_id,
    )
    _append(
        "deleteProject (original)",
        lambda: ctx.client.request(_doc(cfg, "deleteProject"), {"input": {"projectId": project_id}}),
        skip=not project_id,
    )


def web_project_flow(ctx: FlowContext, cfg: GraphQLSimulationConfig) -> None:
    s = cfg.seed
    create = run_operation(
        ctx,
        "createWebProject",
        lambda: ctx.client.request(
            _doc(cfg, "createWebProject"),
            {"input": {"styles": [{"id": s.style_id, "isEmbedded": True}]}},
        ),
        skip=not s.style_id,
    )
    ctx.results.append(create)
    web_id = ""
    if isinstance(create.response, dict):
        web_id = (create.response.get("createWebProject") or {}).get("asset", {}).get("id") or ""

    ctx.results.append(
        run_operation(
            ctx,
            "getWebProjectSize",
            lambda: ctx.client.request(
                _doc(cfg, "getWebProjectSize"),
                {"input": {"styles": [{"id": s.style_id, "isEmbedded": True}]}},
            ),
            skip=not web_id or not s.style_id,
        )
    )

    ctx.results.append(
        run_operation(
            ctx,
            "addStylesToWebProject",
            lambda: ctx.client.request(
                _doc(cfg, "addStylesToWebProject"),
                {"input": {"webProjectId": web_id, "styles": [{"id": s.style_id, "isEmbedded": False}]}},
            ),
            skip=not web_id or not s.style_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "downloadWebProject",
            lambda: ctx.client.request(_doc(cfg, "downloadWebProject"), {"input": {"id": web_id}}),
            skip=not web_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "removeStylesFromWebProject",
            lambda: ctx.client.request(
                _doc(cfg, "removeStylesFromWebProject"),
                {"input": {"webProjectId": web_id, "styles": [s.style_id]}},
            ),
            skip=not web_id or not s.style_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "deleteAssets",
            lambda: ctx.client.request(
                _doc(cfg, "deleteAssets"),
                {"input": {"assets": [{"assetType": "WebProject", "assetIds": [web_id]}]}},
            ),
            skip=not web_id,
        )
    )


def roles_flow(ctx: FlowContext, cfg: GraphQLSimulationConfig) -> None:
    from .graphql_loader import load_graphql_documents
    from .seed_catalog import role_permission_groups

    docs = load_graphql_documents(str(cfg.project_root))
    if ctx.customer_id:
        from ..cleanup import cleanup_automation_roles

        cleanup_automation_roles(ctx.client, ctx.customer_id, docs)

    permission_groups = role_permission_groups()

    def _create_role() -> OperationResult:
        return run_operation(
            ctx,
            "createRole",
            lambda: _nextgen_request(
                ctx,
                cfg,
                "createRole",
                {
                    "input": {
                        "customerId": ctx.customer_id,
                        "name": f"automation-role-{int(time.time() * 1000)}",
                        "description": "",
                        "addProfileIds": [],
                        "permissionGroups": permission_groups,
                    }
                },
            ),
            skip=not ctx.customer_id,
        )

    def _role_id(result: OperationResult) -> str:
        if isinstance(result.response, dict):
            return (result.response.get("createRole") or {}).get("role", {}).get("id") or ""
        return ""

    # UI pattern: create a role, then delete that same role (before any update).
    create_for_delete = _create_role()
    ctx.results.append(create_for_delete)
    delete_role_id = _role_id(create_for_delete)
    _settle_after_mutation()
    ctx.results.append(
        run_operation(
            ctx,
            "deleteRoles",
            lambda rid=delete_role_id: _nextgen_request(
                ctx,
                cfg,
                "deleteRoles",
                {"input": {"customerId": ctx.customer_id, "ids": [rid]}},
            ),
            skip=not delete_role_id or not ctx.customer_id,
        )
    )

    create = _create_role()
    ctx.results.append(create)
    role_id = _role_id(create)
    _settle_after_mutation()

    ctx.results.append(
        run_operation(
            ctx,
            "getRoles",
            lambda: ctx.client.request(
                _doc(cfg, "getRoles"),
                {"input": {"customerId": ctx.customer_id, "pagination": {"skip": 0, "limit": 1}}},
            ),
            skip=not ctx.customer_id,
        )
    )

    ctx.results.append(
        run_operation(
            ctx,
            "updateRole",
            lambda rid=role_id: _nextgen_request(
                ctx,
                cfg,
                "updateRole",
                {
                    "input": {
                        "customerId": ctx.customer_id,
                        "id": rid,
                        "name": f"automation-role-updated-{int(time.time() * 1000)}",
                        "description": None,
                    }
                },
            ),
            skip=not role_id or not ctx.customer_id,
        )
    )
    _settle_after_mutation()
    ctx.results.append(
        run_operation(
            ctx,
            "deleteRoles",
            lambda rid=role_id: _nextgen_request(
                ctx,
                cfg,
                "deleteRoles",
                {"input": {"customerId": ctx.customer_id, "ids": [rid]}},
            ),
            skip=not role_id or not ctx.customer_id,
        )
    )


def teams_flow(ctx: FlowContext, cfg: GraphQLSimulationConfig) -> None:
    profile_id = ""
    get_profiles = run_operation(
        ctx,
        "getProfiles (for team update)",
        lambda: ctx.client.request(
            _doc(cfg, "getProfiles"),
            {"pagination": {"skip": 0, "limit": 5}},
        ),
    )
    ctx.results.append(get_profiles)
    if isinstance(get_profiles.response, dict):
        nodes = (get_profiles.response.get("getProfiles") or {}).get("nodes") or []
        for node in nodes:
            if node.get("id") and node["id"] != ctx.profile_id:
                profile_id = node["id"]
                break

    create = run_operation(
        ctx,
        "createTeam",
        lambda: _nextgen_request(
            ctx,
            cfg,
            "createTeam",
            {"input": {"name": f"automation-team-{int(time.time() * 1000)}", "description": ""}},
        ),
    )
    ctx.results.append(create)
    team_id = ""
    if isinstance(create.response, dict):
        team_id = (create.response.get("createTeam") or {}).get("id") or ""

    _settle_after_mutation()
    ctx.results.append(
        run_operation(
            ctx,
            "deleteTeams",
            lambda tid=team_id: _nextgen_request(
                ctx, cfg, "deleteTeams", {"input": {"ids": [tid]}}
            ),
            skip=not team_id,
        )
    )

    create_update = run_operation(
        ctx,
        "createTeam",
        lambda: _nextgen_request(
            ctx,
            cfg,
            "createTeam",
            {"input": {"name": f"automation-team-{int(time.time() * 1000)}", "description": ""}},
        ),
    )
    ctx.results.append(create_update)
    team_id = ""
    if isinstance(create_update.response, dict):
        team_id = (create_update.response.get("createTeam") or {}).get("id") or ""

    ctx.results.append(
        run_operation(
            ctx,
            "getTeams",
            lambda: ctx.client.request(
                _doc(cfg, "getTeams"),
                {"pagination": {"skip": 0, "limit": 1}, "filter": {"name": "automation-team"}},
            ),
        )
    )

    update_input: dict[str, object] = {
        "id": team_id,
        "name": f"automation-team-updated-{int(time.time() * 1000)}",
        "description": "",
    }
    if profile_id:
        update_input["addProfiles"] = [profile_id]

    ctx.results.append(
        run_operation(
            ctx,
            "updateTeam",
            lambda inp=update_input: _nextgen_request(ctx, cfg, "updateTeam", {"input": inp}),
            skip=not team_id,
        )
    )
    _settle_after_mutation()
    ctx.results.append(
        run_operation(
            ctx,
            "deleteTeams",
            lambda tid=team_id: _nextgen_request(ctx, cfg, "deleteTeams", {"input": {"ids": [tid]}}),
            skip=not team_id,
        )
    )


def invitations_flow(ctx: FlowContext, cfg: GraphQLSimulationConfig) -> None:
    from .graphql_loader import load_graphql_documents

    get_roles_doc = load_graphql_documents(str(cfg.project_root)).get("GET_ROLES", "")
    roles = run_operation(
        ctx,
        "getRoles (for invitation)",
        lambda: ctx.client.request(
            get_roles_doc,
            {"input": {"customerId": ctx.customer_id, "pagination": {"skip": 0, "limit": 1}}},
        ),
        skip=not ctx.customer_id,
    )
    ctx.results.append(roles)
    role_id = ""
    if isinstance(roles.response, dict):
        nodes = (roles.response.get("getRoles") or {}).get("nodes") or []
        if nodes:
            role_id = nodes[0].get("id") or ""

    create = run_operation(
        ctx,
        "createUserInvitations",
        lambda: _nextgen_request(
            ctx,
            cfg,
            "createUserInvitations",
            {
                "input": {
                    "customerId": ctx.customer_id,
                    "data": [
                        {
                            "emails": [f"automation-test-invite-{int(time.time() * 1000)}@automation.com"],
                            "status": 1,
                            "roleId": role_id,
                            "teamIds": [],
                            "tempUserExpiryDate": None,
                            "emailLocale": "EN",
                        }
                    ],
                }
            },
        ),
        skip=not role_id,
    )
    ctx.results.append(create)
    invitation_id = ""
    if isinstance(create.response, dict):
        data = (create.response.get("createUserInvitations") or {}).get("data") or []
        if data:
            invitation_id = data[0].get("invitationId") or ""

    ctx.results.append(
        run_operation(
            ctx,
            "getUserInvitations",
            lambda: ctx.client.request(
                _doc(cfg, "getUserInvitations"),
                {"customerId": ctx.customer_id, "page": 1, "pageSize": 10},
            ),
            skip=not ctx.customer_id,
        )
    )

    ctx.results.append(
        run_operation(
            ctx,
            "updateUserInvitations",
            lambda: _nextgen_request(
                ctx,
                cfg,
                "updateUserInvitations",
                {"input": {"customerId": ctx.customer_id, "data": [{"invitationId": invitation_id, "status": 0}]}},
            ),
            skip=not invitation_id,
        )
    )


def profiles_flow(ctx: FlowContext, cfg: GraphQLSimulationConfig) -> None:
    get_profiles = run_operation(
        ctx,
        "getProfiles",
        lambda: ctx.client.request(
            _doc(cfg, "getProfiles"),
            {"pagination": {"skip": 0, "limit": 10}},
        ),
        skip=not ctx.customer_id,
    )
    ctx.results.append(get_profiles)

    other_profiles: list[dict] = []
    if isinstance(get_profiles.response, dict):
        nodes = (get_profiles.response.get("getProfiles") or {}).get("nodes") or []
        other_profiles = [node for node in nodes if node.get("id") and node["id"] != ctx.profile_id]

    target = other_profiles[0] if other_profiles else {}
    target_id = target.get("id") or ""
    target_role_id = (target.get("role") or {}).get("id") or ""
    target_user = target.get("user") or {}
    target_first = target_user.get("firstName") or "Automation"
    target_last = target_user.get("lastName") or "User"

    ctx.results.append(
        run_operation(
            ctx,
            "updateProfile",
            lambda tid=target_id, rid=target_role_id, fn=target_first, ln=target_last: _nextgen_request(
                ctx,
                cfg,
                "updateProfile",
                {
                    "input": {
                        "id": tid,
                        "firstName": fn,
                        "lastName": ln,
                        "roleId": rid,
                        "tempUserExpiryDate": None,
                    }
                },
            ),
            skip=not target_id or not target_role_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "updateProfile (deactivate other)",
            lambda tid=target_id: _nextgen_request(
                ctx,
                cfg,
                "updateProfile",
                {"input": {"id": tid, "isActive": False}},
            ),
            skip=not target_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "updateProfile (reactivate other)",
            lambda tid=target_id: _nextgen_request(
                ctx,
                cfg,
                "updateProfile",
                {"input": {"id": tid, "isActive": True}},
            ),
            skip=not target_id,
        )
    )
    bulk_targets = [node["id"] for node in other_profiles[:3]]
    ctx.results.append(
        run_operation(
            ctx,
            "bulkUpdateProfiles",
            lambda: ctx.client.request(
                _doc(cfg, "bulkUpdateProfiles"),
                {
                    "input": {
                        "targetProfileIds": bulk_targets,
                        "action": "CHANGE_TEAMS",
                        "operation": {"teamIds": []},
                    }
                },
            ),
            skip=not bulk_targets,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "resetPassword",
            lambda: _reset_password(cfg),
            skip=True,  # accounts REST — not validated in E2E for now
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "deleteProfiles",
            lambda: ctx.client.request(_doc(cfg, "deleteProfiles"), {"input": {"profileIds": []}}),
            expected_to_fail=True,
        )
    )


def customer_flow(ctx: FlowContext, cfg: GraphQLSimulationConfig) -> None:
    ts = int(time.time() * 1000)
    trigger_create_or_update = False
    ctx.results.append(
        run_operation(
            ctx,
            "createCustomer",
            lambda: ctx.admin_client.request(
                _doc(cfg, "createCustomer"),
                {
                    "input": {
                        "name": f"Everest_Test_Customer_{ts}",
                        "invitedPrimaryContactEmail": f"everest-test-primary-{ts}@everest-test.com",
                        "isTrial": True,
                        "isTestDemo": False,
                        "productType": "ENTERPRISE",
                        "entitlement": {
                            "seatsAvailable": 1,
                            "fontBridgeSeatCount": 1,
                            "webImpressionLimit": 0,
                            "pageViewLimit": 0,
                            "productionFontCount": 0,
                            "productionFontCountOverrides": [],
                            "allowDownload": False,
                            "allowImportedFonts": False,
                            "allowClickThroughEula": False,
                            "allowWebFontAccess": False,
                            "allowDigitalAccess": False,
                            "expiryDate": "2030-01-01T00:00:00.000Z",
                            "isLicenseManagementEnabled": False,
                            "allowSelfHostingKits": False,
                            "inventories": [],
                        },
                        "settings": {
                            "allowFontFormatSelectionInFontList": False,
                            "isAnalyticsEnabled": False,
                            "isSSOEnabled": False,
                            "isWorkspaceEnabled": False,
                            "isResearchParticipationEnabled": False,
                            "isPreDeliveryEnabled": False,
                        },
                        "addOnFonts": {"associations": [], "disassociations": []},
                    }
                },
            ),
            skip=not trigger_create_or_update,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "getCustomers",
            lambda: ctx.client.request(
                _doc(cfg, "getCustomers"),
                {"pagination": {"skip": 0, "limit": 10}, "filter": {"search": "Everest_Test_Customer"}},
            ),
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "getCustomerById",
            lambda: ctx.client.request(_doc(cfg, "getCustomerById"), {"getCustomerId": ctx.customer_id}),
            skip=True,  # query-only — not validated in E2E for now
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "updateCustomer",
            lambda: _accounts_customer_update(cfg),
            skip=True,  # no NextGen GQL — use updateProfile in profiles flow
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "markOnboardingCompleted",
            lambda: ctx.client.request(_doc(cfg, "markOnboardingCompleted"), {}),
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "updateCustomerSettings",
            lambda: ctx.client.request(
                _doc(cfg, "updateCustomerSettings"),
                {"input": {"displayName": "Monotype System Admin"}},
            ),
            skip=True,
        )
    )


def production_font_flow(ctx: FlowContext, cfg: GraphQLSimulationConfig) -> None:
    s = cfg.seed
    ctx.results.append(
        run_operation(
            ctx,
            "markAsProductionFont",
            lambda: ctx.client.request(
                _doc(cfg, "markAsProductionFont"),
                {"input": {"companyId": ctx.customer_id, "styleIds": [s.style_id], "sourceContext": "LIBRARY"}},
            ),
            skip=not s.style_id or not ctx.customer_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "updateProductionFont",
            lambda: ctx.client.request(
                _doc(cfg, "updateProductionFont"),
                {"input": {"companyId": ctx.customer_id, "updateProductionFonts": [{"styleId": s.style_id, "inProduction": True}], "sourceContext": "LIBRARY"}},
            ),
            skip=not s.style_id or not ctx.customer_id,
        )
    )


def query_flow(ctx: FlowContext, cfg: GraphQLSimulationConfig) -> None:
    # Query-only ops skipped until stable seeds / services are confirmed.
    ctx.results.append(
        run_operation(ctx, "getPackageId", lambda: {}, skip=True)
    )
    ctx.results.append(
        run_operation(ctx, "getCategorizedGlyphs", lambda: {}, skip=True)
    )





def font_access_flow(ctx: FlowContext, cfg: GraphQLSimulationConfig) -> None:
    s = cfg.seed
    imported = s.imported_style_id
    ctx.results.append(
        run_operation(
            ctx,
            "requestFontAccess",
            lambda: ctx.client.request(
                _doc(cfg, "requestFontAccess"),
                {"input": {"styleIds": [imported]}},
            ),
            skip=not imported,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "approveFontAccess",
            lambda: ctx.client.request(
                _doc(cfg, "approveFontAccess"),
                {
                    "input": {
                        "reason": "Approved for testing",
                        "request": [{"requestorId": ctx.profile_id, "styleId": imported}],
                    }
                },
            ),
            skip=not imported or not ctx.profile_id,
            expected_to_fail=True,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "rejectFontAccess",
            lambda: ctx.client.request(
                _doc(cfg, "rejectFontAccess"),
                {
                    "input": {
                        "reason": "Rejected for testing",
                        "request": [{"requestorId": ctx.profile_id, "styleId": imported}],
                    }
                },
            ),
            skip=not imported or not ctx.profile_id,
            expected_to_fail=True,
        )
    )


def asset_attachments_flow(ctx: FlowContext, cfg: GraphQLSimulationConfig) -> None:
    project_id = ""
    draft_id = ""
    attachment_id = ""

    create = run_operation(
        ctx,
        "createProject (FontProject) to attach files to",
        lambda: ctx.client.request(
            _doc(cfg, "createProject"),
            {
                "input": {
                    "name": f"automation-project-{int(time.time() * 1000)}",
                    "description": "Automation test project",
                    "allowFontAdditionsByCollaborators": False,
                    "allowFontDownloadsByCollaborators": False,
                    "allowFontImportsByCollaborators": False,
                    "enableProjectLevelImportedFonts": False,
                    "autoActivateFontsForMembers": False,
                }
            },
        ),
    )
    ctx.results.append(create)
    if isinstance(create.response, dict):
        project_id = (create.response.get("createProject") or {}).get("asset", {}).get("id") or ""

    upload = run_operation(
        ctx,
        "createAssetAttachmentUpload",
        lambda: ctx.client.request(
            _doc(cfg, "createAssetAttachmentUpload"),
            {
                "input": {
                    "uploadSessionId": str(uuid.uuid4()),
                    "assetType": "FontProject",
                    "context": "FILE_ATTACHMENT",
                    "fileName": "automation-test.pdf",
                    "sizeBytes": 1024,
                    "mimeType": "application/pdf",
                    "md5": "d41d8cd98f00b204e9800998ecf8427e",
                    "assetId": project_id,
                }
            },
        ),
        skip=not project_id,
    )
    ctx.results.append(upload)
    if isinstance(upload.response, dict):
        draft_id = (upload.response.get("createAssetAttachmentUpload") or {}).get("attachmentDraftId") or ""

    finalize = run_operation(
        ctx,
        "finalizeAssetAttachments",
        lambda: ctx.client.request(
            _doc(cfg, "finalizeAssetAttachments"),
            {
                "input": {
                    "items": [{"attachmentDraftId": draft_id}] if draft_id else [],
                    "assetId": project_id,
                }
            },
        ),
        skip=not draft_id,
        expected_to_fail=True,
    )
    ctx.results.append(finalize)
    if isinstance(finalize.response, dict):
        attachments = (finalize.response.get("finalizeAssetAttachments") or {}).get("attachments") or []
        if attachments:
            attachment_id = attachments[0].get("id") or ""

    ctx.results.append(
        run_operation(
            ctx,
            "getAssetAttachments",
            lambda: ctx.client.request(
                _doc(cfg, "getAssetAttachments"),
                {"assetId": project_id, "context": "FILE_ATTACHMENT"},
            ),
            skip=not project_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "downloadAssetAttachmentSignedUrl",
            lambda: ctx.client.request(
                _doc(cfg, "downloadAssetAttachmentSignedUrl"),
                {"id": attachment_id or "00000000-0000-0000-0000-000000000000"},
            ),
            skip=not attachment_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "deleteAssetAttachment",
            lambda: ctx.client.request(
                _doc(cfg, "deleteAssetAttachment"),
                {"id": attachment_id or "00000000-0000-0000-0000-000000000000", "assetId": project_id},
            ),
            skip=not attachment_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "deleteProject (FontProject for attachment)",
            lambda: ctx.client.request(_doc(cfg, "deleteProject"), {"input": {"projectId": project_id}}),
            skip=not project_id,
        )
    )


def style_document_flow(ctx: FlowContext, cfg: GraphQLSimulationConfig) -> None:
    s = cfg.seed
    document_id = ""
    comment_id = ""

    ctx.results.append(
        run_operation(
            ctx,
            "getStyleDocuments",
            lambda: ctx.client.request(_doc(cfg, "getStyleDocuments"), {"input": {"styleId": s.style_id}}),
            skip=True,  # import-context service unavailable in PP — skip for now
        )
    )
    document_md5 = "abc123"
    upload = run_operation(
        ctx,
        "createStyleDocumentsUploadUrl",
        lambda md5=document_md5: ctx.client.request(
            _doc(cfg, "createStyleDocumentsUploadUrl"),
            {
                "input": {
                    "files": [
                        {
                            "md5": md5,
                            "fileName": "automation-doc.pdf",
                            "mimeType": "application/pdf",
                            "size": 100,
                        }
                    ]
                }
            },
        ),
        skip=not s.style_id,
        expected_to_fail=True,
    )
    ctx.results.append(upload)
    if isinstance(upload.response, dict):
        uploads = (upload.response.get("createStyleDocumentsUploadUrl") or {}).get("styleDocumentsUpload") or []
        if uploads and isinstance(uploads[0], dict):
            document_md5 = uploads[0].get("md5") or document_md5

    ctx.results.append(
        run_operation(
            ctx,
            "addStyleDocument",
            lambda md5=document_md5: ctx.client.request(
                _doc(cfg, "addStyleDocument"),
                {
                    "input": {
                        "styleId": s.style_id,
                        "documentMd5": md5,
                        "fileName": "automation-doc.pdf",
                    }
                },
            ),
            skip=not s.style_id or not document_md5,
            expected_to_fail=True,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "getStyleDocumentsDownloadUrls",
            lambda md5=document_md5: ctx.client.request(
                _doc(cfg, "getStyleDocumentsDownloadUrls"),
                {"input": {"md5s": [md5] if md5 else []}},
            ),
            skip=not document_md5,
            expected_to_fail=True,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "getStyleComments",
            lambda: ctx.client.request(_doc(cfg, "getStyleComments"), {"input": {"styleId": s.style_id}}),
            skip=True,  # import-context service unavailable in PP — skip for now
        )
    )
    add_comment = run_operation(
        ctx,
        "addStyleComment",
        lambda: ctx.client.request(
            _doc(cfg, "addStyleComment"),
            {"input": {"styleId": s.style_id, "text": "Automation test comment"}},
        ),
        skip=not s.style_id,
        expected_to_fail=True,
    )
    ctx.results.append(add_comment)
    if isinstance(add_comment.response, dict):
        comment_id = (
            (add_comment.response.get("addStyleComment") or {}).get("comment", {}).get("commentId") or ""
        )

    ctx.results.append(
        run_operation(
            ctx,
            "updateStyleComment",
            lambda: ctx.client.request(
                _doc(cfg, "updateStyleComment"),
                {
                    "input": {
                        "styleId": s.style_id,
                        "commentId": comment_id,
                        "text": "Updated automation comment",
                    }
                },
            ),
            skip=not s.style_id or not comment_id,
            expected_to_fail=True,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "deleteStyleComment",
            lambda: ctx.client.request(
                _doc(cfg, "deleteStyleComment"),
                {"input": {"styleId": s.style_id, "commentId": comment_id}},
            ),
            skip=not s.style_id or not comment_id,
            expected_to_fail=True,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "deleteStyleDocument",
            lambda: ctx.client.request(
                _doc(cfg, "deleteStyleDocument"),
                {"input": {"styleId": s.style_id, "documentId": document_id}},
            ),
            skip=not s.style_id or not document_id,
            expected_to_fail=True,
        )
    )


def license_management_flow(ctx: FlowContext, cfg: GraphQLSimulationConfig) -> None:
    s = cfg.seed
    ctx.results.append(
        run_operation(
            ctx,
            "submitFontUsageReport",
            lambda: ctx.client.request(
                _doc(cfg, "submitFontUsageReport"),
                {"input": {"companyId": ctx.customer_id, "sourceContext": "DASHBOARD"}},
            ),
            skip=not ctx.customer_id,
            expected_to_fail=True,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "updateFontsForReview",
            lambda: ctx.client.request(
                _doc(cfg, "updateFontsForReview"),
                {
                    "input": {
                        "companyId": ctx.customer_id,
                        "sourceContext": "DASHBOARD",
                        "fontsForReview": [{"styleId": s.style_id, "action": "DENY"}],
                    }
                },
            ),
            skip=not ctx.customer_id or not s.style_id,
            expected_to_fail=True,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "submitIntentForProduction",
            lambda: ctx.client.request(
                _doc(cfg, "submitIntentForProduction"),
                {
                    "input": {
                        "styleIds": [s.style_id] if s.style_id else [],
                        "comment": "Automated test comment",
                    }
                },
            ),
            skip=not s.style_id,
            expected_to_fail=True,
        )
    )


def sso_flow(ctx: FlowContext, cfg: GraphQLSimulationConfig) -> None:
    mapping_id = ""
    created_id = ""
    role_id = os.getenv("DEFAULT_ROLE_ID", "").strip()

    get_mappings = run_operation(
        ctx,
        "getSsoMappings",
        lambda: ctx.client.request(
            _doc(cfg, "getSsoMappings"),
            {"filter": {}, "pagination": {"skip": 0, "limit": 10}},
        ),
    )
    ctx.results.append(get_mappings)
    if isinstance(get_mappings.response, dict):
        mappings = (get_mappings.response.get("getSsoMappings") or {}).get("nodes") or []
        if mappings and isinstance(mappings[0], dict):
            mapping_id = mappings[0].get("id") or ""
            role_id = role_id or ((mappings[0].get("role") or {}).get("id") or "")

    match_text = f"automation-sso-{int(time.time() * 1000)}"
    create = run_operation(
        ctx,
        "createSsoMapping",
        lambda: ctx.client.request(
            _doc(cfg, "createSsoMapping"),
            {
                "input": {
                    "matchText": match_text,
                    "displayName": "Automation SSO Group",
                    "roleId": role_id,
                }
            },
        ),
        skip=not role_id,
        expected_to_fail=True,
    )
    ctx.results.append(create)
    if isinstance(create.response, dict):
        created_id = (create.response.get("createSsoMapping") or {}).get("rule", {}).get("id") or ""

    active_id = created_id or mapping_id
    ctx.results.append(
        run_operation(
            ctx,
            "updateSsoMapping",
            lambda rid=active_id, rt=match_text: ctx.client.request(
                _doc(cfg, "updateSsoMapping"),
                {
                    "input": {
                        "ruleId": rid,
                        "matchText": f"{rt}-updated",
                        "displayName": "Automation SSO Group Updated",
                        "roleId": role_id,
                    }
                },
            ),
            skip=not active_id or not role_id,
            expected_to_fail=True,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "reorderSsoMappings",
            lambda rid=active_id: ctx.client.request(
                _doc(cfg, "reorderSsoMappings"),
                {"input": {"currentGroupId": rid}},
            ),
            skip=not active_id,
            expected_to_fail=True,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "deleteSsoMappings",
            lambda rid=created_id: ctx.client.request(
                _doc(cfg, "deleteSsoMappings"),
                {"input": {"ruleIds": [rid] if rid else []}},
            ),
            skip=not created_id,
            expected_to_fail=True,
        )
    )


def company_logo_flow(ctx: FlowContext, cfg: GraphQLSimulationConfig) -> None:
    upload_url = ""
    create = run_operation(
        ctx,
        "createCompanyLogoUploadUrl",
        lambda: ctx.client.request(
            _doc(cfg, "createCompanyLogoUploadUrl"),
            {"input": {"mimeType": "image/png", "size": 1024}},
        ),
    )
    ctx.results.append(create)
    if isinstance(create.response, dict):
        logo = (create.response.get("createCompanyLogoUploadUrl") or {}).get("companyLogoUpload") or {}
        upload_url = logo.get("uploadUrl") or ""

    ctx.results.append(
        run_operation(
            ctx,
            "markCompanyLogoUploadSuccess",
            lambda: ctx.client.request(_doc(cfg, "markCompanyLogoUploadSuccess"), {}),
            skip=not upload_url,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "deleteCompanyLogo",
            lambda: ctx.client.request(_doc(cfg, "deleteCompanyLogo"), {}),
            skip=not upload_url,
        )
    )


def service_accounts_flow(ctx: FlowContext, cfg: GraphQLSimulationConfig) -> None:
    """Create / suspend / delete service accounts (enrichers active on DEV resolver)."""
    name = f"automation-sa-{int(time.time() * 1000)}"
    created = run_operation(
        ctx,
        "createServiceAccount",
        lambda: ctx.client.request(
            _doc(cfg, "createServiceAccount"),
            {
                "input": {
                    "serverName": name,
                    "serverDescription": "QA audit automation",
                    "serverType": "STAGING",
                    "customerId": ctx.customer_id,
                }
            },
        ),
        skip=not ctx.customer_id,
    )
    ctx.results.append(created)
    sa_id = ""
    if isinstance(created.response, dict):
        sa_id = str((created.response.get("createServiceAccount") or {}).get("id") or "")

    ctx.results.append(
        run_operation(
            ctx,
            "suspendServiceAccount",
            lambda: ctx.client.request(
                _doc(cfg, "suspendServiceAccount"),
                {"input": {"id": sa_id}},
            ),
            skip=not sa_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "revokeToken",
            lambda: ctx.client.request(
                _doc(cfg, "revokeToken"),
                {"input": {"id": sa_id}},
            ),
            skip=not sa_id,
            expected_to_fail=True,  # may already be suspended / schema-sensitive
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "deleteServiceAccount",
            lambda: ctx.client.request(
                _doc(cfg, "deleteServiceAccount"),
                {"input": {"id": sa_id}},
            ),
            skip=not sa_id,
        )
    )


def document_scanning_flow(ctx: FlowContext, cfg: GraphQLSimulationConfig) -> None:
    """Trigger DocumentScanning mutations that emit enrichable audit events on DEV/PP."""
    files = [
        {
            "fileName": f"automation-scan-{int(time.time() * 1000)}.pdf",
            "size": 1024,
            "contentType": "application/pdf",
        }
    ]
    created = run_operation(
        ctx,
        "createUploadSession",
        lambda: ctx.client.request(_doc(cfg, "createUploadSession"), {"files": files}),
    )
    ctx.results.append(created)
    session_id = ""
    file_id = ""
    if isinstance(created.response, dict):
        payload = created.response.get("createUploadSession") or {}
        session = payload.get("session") or {}
        session_id = str(session.get("sessionId") or session.get("id") or "")
        file_rows = session.get("files") or []
        if file_rows and isinstance(file_rows[0], dict):
            file_id = str(file_rows[0].get("fileId") or "")

    ctx.results.append(
        run_operation(
            ctx,
            "updateSessionFiles",
            lambda: ctx.client.request(
                _doc(cfg, "updateSessionFiles"),
                {"sessionId": session_id, "files": files},
            ),
            skip=not session_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "markSessionFileUploaded",
            lambda: ctx.client.request(
                _doc(cfg, "markSessionFileUploaded"),
                {"sessionId": session_id, "fileId": file_id},
            ),
            skip=not session_id or not file_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "saveDocumentMetadata",
            lambda: ctx.client.request(
                _doc(cfg, "saveDocumentMetadata"),
                {
                    "input": {
                        "sessionId": session_id,
                        "fileId": file_id,
                        "documentName": files[0]["fileName"],
                    }
                },
            ),
            skip=not session_id,
            expected_to_fail=True,  # schema variants differ; still emits when accepted
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "completeUploadSession",
            lambda: ctx.client.request(
                _doc(cfg, "completeUploadSession"), {"sessionId": session_id}
            ),
            skip=not session_id,
        )
    )
    # Query — not enrichable for generate catalog, but keeps flow parity / may enrich on DEV.
    ctx.results.append(
        run_operation(
            ctx,
            "getUploadSessionFonts",
            lambda: ctx.client.request(
                _doc(cfg, "getUploadSessionFonts"), {"sessionId": session_id}
            ),
            skip=not session_id,
        )
    )


def notifications_flow(ctx: FlowContext, cfg: GraphQLSimulationConfig) -> None:
    ops: list[tuple[str, dict, bool]] = [
        (
            "updatePreference",
            {"input": {"preferenceCode": "user_and_access", "channel": "EMAIL", "enabled": True}},
            False,
        ),
        (
            "updateSubToggle",
            {"input": {"preferenceCode": "user_and_access", "triggerCode": "B-1", "enabled": False}},
            False,
        ),
        (
            "bulkUpdatePreferences",
            {
                "input": [
                    {"preferenceCode": "user_and_access", "channel": "EMAIL", "enabled": True},
                ]
            },
            False,
        ),
        ("markNotificationRead", {"input": {"id": "00000000-0000-0000-0000-000000000000"}}, True),
        ("markAllNotificationsRead", {}, False),
        ("dismissNotification", {"input": {"id": "00000000-0000-0000-0000-000000000000"}}, True),
        (
            "bulkNotificationAction",
            {"input": {"action": "DISMISS", "ids": ["00000000-0000-0000-0000-000000000000"]}},
            True,
        ),
        ("resetPreferences", {}, True),
        ("globalEmailOptOut", {}, True),
        ("applyUnsubscribe", {"token": "automation-unsubscribe-token"}, True),
    ]
    for name, variables, expected_to_fail in ops:
        ctx.results.append(
            run_operation(
                ctx,
                name,
                lambda n=name, v=variables: ctx.client.request(_doc(cfg, n), v),
                expected_to_fail=expected_to_fail,
            )
        )


def byof_flow(ctx: FlowContext, cfg: GraphQLSimulationConfig) -> None:
    batch_id = ""
    contract_id = ""

    batch = run_operation(
        ctx,
        "createBYOFBatchAndCheckDuplicates",
        lambda: ctx.client.request(
            _doc(cfg, "createBYOFBatchAndCheckDuplicates"),
            {
                "input": {
                    "fontMD5s": [
                        {
                            "md5": cfg.seed.variation_md5 or "33f225b8f5f7d6b34a0926f58f96c1e9",
                            "fileName": "automation-test.ttf",
                            "extension": "ttf",
                            "sizeBytes": 100,
                        }
                    ]
                }
            },
        ),
    )
    ctx.results.append(batch)
    if isinstance(batch.response, dict):
        batch_id = (batch.response.get("createBYOFBatchAndCheckDuplicates") or {}).get("batchId") or ""

    style_id_int = _parse_int(cfg.seed.style_id)
    variation_id_int = _parse_int(cfg.seed.variation_id)
    ctx.results.append(
        run_operation(
            ctx,
            "publishAddOn",
            lambda: ctx.client.request(
                _doc(cfg, "publishAddOn"),
                {
                    "input": {
                        "customerId": ctx.customer_id,
                        "associations": [
                            {
                                "styleId": style_id_int,
                                "variationId": variation_id_int,
                            }
                        ],
                    }
                },
            ),
            skip=not ctx.customer_id or style_id_int is None or variation_id_int is None,
            expected_to_fail=True,
        )
    )

    contract = run_operation(
        ctx,
        "createContract",
        lambda: ctx.client.request(
            _doc(cfg, "createContract"),
            {
                "input": {
                    "styleIds": [],
                    "isFreeToUse": False,
                    "licenceType": "DESKTOP",
                    "isReviewed": False,
                    "linkedImportedFontScope": "GLOBAL",
                    "licenceName": "automation-licence",
                }
            },
        ),
    )
    ctx.results.append(contract)
    if isinstance(contract.response, dict):
        contract_id = (contract.response.get("createContract") or {}).get("contractId") or ""

    for op, variables, skip in [
        (
            "updateContract",
            {
                "input": {
                    "contractId": contract_id,
                    "changedFields": {"licenceName": "automation-updated", "isReviewed": True},
                }
            },
            not contract_id,
        ),
        ("linkContractToStyles", {"input": {"contractId": contract_id, "styleIds": []}}, not contract_id),
        ("linkDocumentsToContract", {"input": {"contractId": contract_id, "documentIds": []}}, not contract_id),
        ("unlinkDocumentsFromContract", {"input": {"contractId": contract_id, "documentIds": []}}, not contract_id),
    ]:
        ctx.results.append(
            run_operation(
                ctx,
                op,
                lambda o=op, v=variables: ctx.client.request(_doc(cfg, o), v),
                skip=skip,
                expected_to_fail=True,
            )
        )

    ctx.results.append(
        run_operation(
            ctx,
            "parseAndCreateContract",
            lambda: ctx.client.request(
                _doc(cfg, "parseAndCreateContract"),
                {
                    "sessionId": "4379e882-8edd-4267-85e8-05dd65de8a01",
                    "fileIds": ["3ab37418-8674-477d-a374-f8df509a0e59"],
                },
            ),
            expected_to_fail=True,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "unlinkStyleFromContract",
            lambda: ctx.client.request(
                _doc(cfg, "unlinkStyleFromContract"),
                {
                    "input": {
                        "contractId": contract_id,
                        "styleIds": [cfg.seed.style_id] if cfg.seed.style_id else [],
                    }
                },
            ),
            skip=not contract_id or not cfg.seed.style_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "cancelBatch",
            lambda: ctx.client.request(_doc(cfg, "cancelBatch"), {"batchId": batch_id}),
            skip=not batch_id,
            expected_to_fail=True,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "deleteContracts",
            lambda: ctx.client.request(_doc(cfg, "deleteContracts"), {"input": {"ids": [contract_id]}}),
            skip=not contract_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "exportFontTemplate",
            lambda: ctx.client.request(
                _doc(cfg, "exportFontTemplate"),
                {"input": {"columns": [{"key": "fileName", "label": "File Name"}, {"key": "projects", "label": "Projects"}]}},
            ),
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "exportUnassignedImportedFontsTemplate",
            lambda: ctx.client.request(_doc(cfg, "exportUnassignedImportedFontsTemplate"), {}),
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "deleteImportedFonts",
            lambda: ctx.client.request(
                _doc(cfg, "deleteImportedFonts"),
                {"input": {"items": [{"styleId": "wmAFokex", "md5": "33f225b8f5f7d6b34a0926f58f96c1e9"}]}},
            ),
            expected_to_fail=True,
        )
    )
    ctx.results.append(
        run_operation(ctx, "getImportedFonts", lambda: ctx.client.request(_doc(cfg, "getImportedFonts"), {"input": {"pagination": {"skip": 0, "limit": 10}}}))
    )
    active = run_operation(ctx, "getActiveBatches", lambda: ctx.client.request(_doc(cfg, "getActiveBatches"), {}))
    ctx.results.append(active)
    active_batch_id = batch_id
    if isinstance(active.response, dict):
        batches = active.response.get("getActiveBatches") or []
        if batches:
            active_batch_id = batches[0].get("batchId") or batch_id
    ctx.results.append(
        run_operation(
            ctx,
            "getBatchProgress",
            lambda: ctx.client.request(_doc(cfg, "getBatchProgress"), {"batchId": active_batch_id}),
            skip=not active_batch_id,
        )
    )

def notification_recipient_flow(ctx: FlowContext, cfg: GraphQLSimulationConfig) -> None:
    """Optional second user grants project access to the primary test user (BEARER_TOKEN)."""
    from ..auth import jwt_is_expired

    if not cfg.secondary_bearer_token or jwt_is_expired(cfg.secondary_bearer_token):
        ctx.results.append(
            run_operation(
                ctx,
                "notificationRecipient (secondary grant)",
                lambda: None,
                skip=True,
            )
        )
        return

    secondary = _secondary_client(cfg)
    context_id = customer_context_header_id(
        use_customer_context=cfg.use_customer_context,
        customer_context_id=cfg.customer_context_id,
        profile_customer_id=ctx.customer_id,
    )
    if context_id:
        secondary.set_customer_id(context_id)

    create = run_operation(
        ctx,
        "createProject (secondary)",
        lambda: secondary.request(
            _doc(cfg, "createProject"),
            {
                "input": {
                    "name": f"automation-recipient-project-{int(time.time() * 1000)}",
                    "description": "Automation notification recipient project",
                    "allowFontAdditionsByCollaborators": True,
                    "allowFontDownloadsByCollaborators": False,
                    "allowFontImportsByCollaborators": False,
                    "enableProjectLevelImportedFonts": False,
                    "autoActivateFontsForMembers": False,
                }
            },
        ),
    )
    ctx.results.append(create)
    project_id = ""
    if isinstance(create.response, dict):
        project_id = (create.response.get("createProject") or {}).get("asset", {}).get("id") or ""

    ctx.results.append(
        run_operation(
            ctx,
            "publishProject (secondary)",
            lambda: secondary.request(
                _doc(cfg, "publishProject"),
                {"input": {"projectId": project_id}},
            ),
            skip=not project_id,
        )
    )

    primary_profile_id = ctx.profile_id

    ctx.results.append(
        run_operation(
            ctx,
            "updateProfile (secondary updates primary)",
            lambda pid=primary_profile_id: secondary.request(
                _doc(cfg, "updateProfile"),
                {"input": {"id": pid, "isActive": False}},
            ),
            skip=not primary_profile_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "updateProfile (secondary reactivates primary)",
            lambda pid=primary_profile_id: secondary.request(
                _doc(cfg, "updateProfile"),
                {"input": {"id": pid, "isActive": True}},
            ),
            skip=not primary_profile_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "bulkUpdateProfiles (secondary updates primary)",
            lambda pid=primary_profile_id: secondary.request(
                _doc(cfg, "bulkUpdateProfiles"),
                {
                    "input": {
                        "targetProfileIds": [pid],
                        "action": "CHANGE_TEAMS",
                        "operation": {"teamIds": []},
                    }
                },
            ),
            skip=not primary_profile_id,
        )
    )

    folder_create = run_operation(
        ctx,
        "createAsset (secondary folder for primary)",
        lambda: secondary.request(
            _doc(cfg, "createAsset"),
            {
                "input": {
                    "name": f"automation-recipient-folder-{int(time.time() * 1000)}",
                    "assetType": "Folder",
                    "accessRight": "FullAccess",
                }
            },
        ),
    )
    ctx.results.append(folder_create)
    folder_id = ""
    if isinstance(folder_create.response, dict):
        folder_id = (folder_create.response.get("createAsset") or {}).get("asset", {}).get("id") or ""

    ctx.results.append(
        run_operation(
            ctx,
            "updateAssetSharing (GRANT primary on secondary folder)",
            lambda: secondary.request(
                _doc(cfg, "updateAssetSharing"),
                {
                    "input": {
                        "assetId": folder_id,
                        "assetType": "Folder",
                        "notify": True,
                        "data": [
                            {
                                "action": "GRANT",
                                "payload": [
                                    {
                                        "shareeType": "User",
                                        "accessIdMap": [
                                            {"accessId": 27, "shareeId": ctx.profile_id},
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                },
            ),
            skip=not folder_id or not ctx.profile_id,
        )
    )

    ctx.results.append(
        run_operation(
            ctx,
            "updateAssetSharing (REVOKE primary on secondary folder)",
            lambda: secondary.request(
                _doc(cfg, "updateAssetSharing"),
                {
                    "input": {
                        "assetId": folder_id,
                        "assetType": "Folder",
                        "notify": True,
                        "data": [
                            {
                                "action": "REVOKE",
                                "payload": [
                                    {
                                        "shareeType": "User",
                                        "accessIdMap": [{"shareeId": ctx.profile_id}],
                                    }
                                ],
                            }
                        ],
                    }
                },
            ),
            skip=not folder_id or not ctx.profile_id,
        )
    )

    ctx.results.append(
        run_operation(
            ctx,
            "updateAssetSharing (GRANT primary on secondary project)",
            lambda: secondary.request(
                _doc(cfg, "updateAssetSharing"),
                {
                    "input": {
                        "assetId": project_id,
                        "assetType": "FontProject",
                        "notify": True,
                        "data": [
                            {
                                "action": "GRANT",
                                "payload": [
                                    {
                                        "shareeType": "User",
                                        "accessIdMap": [
                                            {
                                                "accessId": cfg.seed.fontproject_contributor_access_id,
                                                "shareeId": ctx.profile_id,
                                            },
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                },
            ),
            skip=not project_id or not ctx.profile_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "updateAssetSharing (REVOKE primary on secondary project)",
            lambda: secondary.request(
                _doc(cfg, "updateAssetSharing"),
                {
                    "input": {
                        "assetId": project_id,
                        "assetType": "FontProject",
                        "notify": True,
                        "data": [
                            {
                                "action": "REVOKE",
                                "payload": [
                                    {
                                        "shareeType": "User",
                                        "accessIdMap": [{"shareeId": ctx.profile_id}],
                                    }
                                ],
                            }
                        ],
                    }
                },
            ),
            skip=not project_id or not ctx.profile_id,
        )
    )
    ctx.results.append(
        run_operation(
            ctx,
            "deleteProject (secondary recipient)",
            lambda: secondary.request(_doc(cfg, "deleteProject"), {"input": {"projectId": project_id}}),
            skip=not project_id,
        )
    )


FLOW_REGISTRY: list[tuple[str, Any]] = [
    ("tags", tags_flow),
    ("favorites", favorites_flow),
    ("assets", assets_flow),
    ("webProject", web_project_flow),
    ("fontList", font_list_flow),
    ("fontProject", font_project_flow),
    ("fontActivation", font_activation_flow),
    ("customer", customer_flow),
    ("profiles", profiles_flow),
    ("roles", roles_flow),
    ("teams", teams_flow),
    ("invitations", invitations_flow),
    ("query", query_flow),
    ("productionFont", production_font_flow),
    ("companyLogo", company_logo_flow),
    ("documentScanning", document_scanning_flow),
    ("serviceAccounts", service_accounts_flow),
    ("byof", byof_flow),
    ("notifications", notifications_flow),
    ("fontAccess", font_access_flow),
    ("assetAttachments", asset_attachments_flow),
    ("licenseManagement", license_management_flow),
    ("styleDocument", style_document_flow),
    ("sso", sso_flow),
    ("notificationRecipient", notification_recipient_flow),
]



