"""Mirror buildActorEnrichedSnapshot from actor-resolution.service.ts."""

from __future__ import annotations

from typing import Any

from ...models import JsonDict
from .types import CmsLike, UmsLike


def build_actor_enriched_snapshot(
    *,
    global_user_id: str,
    global_customer_id: str,
    correlation_id: str,
    ums: UmsLike,
    cms: CmsLike,
) -> JsonDict:
    profile = ums.get_profile_by_id(
        global_user_id, global_customer_id, correlation_id=correlation_id
    )
    if not profile:
        raise ValueError(f"Actor profile {global_user_id} not found in UMS")

    customer = cms.get_customer_by_id(global_customer_id, correlation_id=correlation_id)
    if not customer:
        raise ValueError(f"Customer {global_customer_id} not found in CMS")

    role_id = None
    role_obj = profile.get("role")
    if isinstance(role_obj, dict):
        role_id = role_obj.get("id")
    role = (
        ums.get_role_by_id(str(role_id), global_customer_id, correlation_id=correlation_id)
        if role_id
        else None
    )
    if not role:
        raise ValueError(f"Actor role {role_id} not found in UMS")

    return {
        "user": {
            "source": "user-management-service",
            "profile": _profile_payload(profile),
            "role": role,
            "teams": profile.get("teams") or [],
        },
        "customer": {
            "source": "customer-management-service",
            "id": customer.get("id"),
            "name": customer.get("name"),
            "displayName": customer.get("displayName"),
            "metaData": customer.get("metaData"),
            "subscription": customer.get("subscription"),
        },
    }


def _profile_payload(profile: dict[str, Any]) -> dict[str, Any]:
    out = dict(profile)
    if "profile" in out and isinstance(out["profile"], dict):
        nested = dict(out.pop("profile"))
        nested.update({k: v for k, v in out.items() if k not in {"role", "teams", "team"}})
        return nested
    return out
