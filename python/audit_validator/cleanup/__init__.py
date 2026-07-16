"""Post-run cleanup — automation test data and notification DB rows."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from ..auth import customer_context_header_id
from ..simulation.client import GraphQLClient
from ..simulation.config import GraphQLSimulationConfig, load_simulation_config
from ..simulation.graphql_loader import load_graphql_documents

log = logging.getLogger(__name__)

GET_PROFILE = """
query GetProfile {
  getProfile {
    id
    customer { id }
  }
}
"""

_ROLE_PREFIX = "automation-role-"
_TEAM_PREFIX = "automation-team-"
_ASSET_NAME_PREFIXES = (
    "TEST_FontList_",
    "TEST_Folder_",
    "automation-folder",
    "automation-project",
)


@dataclass
class CleanupResult:
    roles_deleted: int = 0
    teams_deleted: int = 0
    assets_deleted: int = 0
    projects_deleted: int = 0
    notifications_deleted: int = 0
    errors: list[str] | None = None


def _bool_env(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def cleanup_automation_roles(client: GraphQLClient, customer_id: str, docs: dict[str, str]) -> int:
    if not customer_id:
        return 0

    get_roles = docs.get("GET_ROLES", "").strip()
    delete_roles = docs.get("DELETE_ROLES", "").strip()
    if not get_roles or not delete_roles:
        log.warning("GET_ROLES / DELETE_ROLES documents missing — skipping role cleanup")
        return 0

    to_delete: list[str] = []
    skip = 0
    while True:
        resp = client.request(
            get_roles,
            {
                "input": {
                    "customerId": customer_id,
                    "pagination": {"skip": skip, "limit": 50},
                }
            },
        )
        if not isinstance(resp, dict):
            log.warning("getRoles returned non-dict — stopping role cleanup")
            break
        nodes = ((resp.get("getRoles") or {}).get("nodes")) or []
        if not nodes:
            break
        for role in nodes:
            name = str(role.get("name") or "")
            if name.startswith(_ROLE_PREFIX) and role.get("id"):
                to_delete.append(str(role["id"]))
        skip += len(nodes)
        if len(nodes) < 50:
            break

    if not to_delete:
        return 0

    deleted = 0
    chunk_size = 20
    for i in range(0, len(to_delete), chunk_size):
        chunk = to_delete[i : i + chunk_size]
        resp = client.request(
            delete_roles,
            {"input": {"customerId": customer_id, "ids": chunk}},
        )
        payload = resp.get("deleteRoles")
        if isinstance(payload, dict):
            deleted += len(payload.get("deletedIds") or [])
            for err in payload.get("errors") or []:
                log.warning("deleteRoles error: %s", err)
        elif payload:
            deleted += len(chunk)

    log.info("Deleted %d automation role(s)", deleted)
    return deleted


def cleanup_automation_teams(client: GraphQLClient, docs: dict[str, str]) -> int:
    get_teams = docs.get("GET_TEAMS", "").strip()
    delete_teams = docs.get("DELETE_TEAMS", "").strip()
    if not get_teams or not delete_teams:
        return 0

    resp = client.request(
        get_teams,
        {"pagination": {"skip": 0, "limit": 200}},
    )
    nodes = ((resp.get("getTeams") or {}).get("nodes")) or []
    ids = [
        str(t["id"])
        for t in nodes
        if t.get("id") and str(t.get("name", "")).startswith(_TEAM_PREFIX)
    ]
    if not ids:
        return 0

    resp = client.request(delete_teams, {"input": {"ids": ids}})
    payload = resp.get("deleteTeams")
    if isinstance(payload, dict):
        deleted = len(payload.get("deletedIds") or [])
    else:
        deleted = len(ids) if payload else 0
    log.info("Deleted %d automation team(s)", deleted)
    return deleted


def _is_automation_asset_name(name: str) -> bool:
    return any(name.startswith(prefix) for prefix in _ASSET_NAME_PREFIXES)


def _iter_automation_assets(client: GraphQLClient, docs: dict[str, str]):
    """Paginate getAssets (My Library) — where E2E font lists/folders are created."""
    query = docs.get("GET_ASSETS", "").strip() or docs.get("GET_COMPANY_ASSETS", "").strip()
    root_field = "getAssets" if docs.get("GET_ASSETS") else "getCompanyAssets"
    if not query:
        return
    skip = 0
    limit = 100
    while True:
        resp = client.request(
            query,
            {"input": {"accessRights": ["FullAccess"], "pagination": {"skip": skip, "limit": limit}}},
        )
        nodes = ((resp.get(root_field) or {}).get("nodes")) or []
        if not nodes:
            break
        yield from nodes
        skip += len(nodes)
        if len(nodes) < limit:
            break


def cleanup_automation_assets(client: GraphQLClient, docs: dict[str, str]) -> tuple[int, int]:
    """
    Remove leftover automation assets (font lists, folders, projects) by name prefix.
    Sweeps historical leaks from prior E2E runs as well as the current run.
    """
    delete_assets = docs.get("DELETE_ASSETS", "").strip()
    delete_project = docs.get("DELETE_PROJECT", "").strip()
    if not delete_assets and not delete_project:
        log.warning("DELETE_ASSETS / DELETE_PROJECT missing — skipping asset cleanup")
        return 0, 0

    asset_ids: dict[str, list[str]] = {}
    project_ids: list[str] = []
    for node in _iter_automation_assets(client, docs):
        name = str(node.get("name") or "")
        if not _is_automation_asset_name(name):
            continue
        asset_id = str(node.get("id") or "")
        if not asset_id:
            continue
        asset_type = str(node.get("assetType") or node.get("__typename") or "")
        if asset_type == "FontProject":
            project_ids.append(asset_id)
        elif asset_type in {"Folder", "FontList", "WebProject"}:
            asset_ids.setdefault(asset_type, []).append(asset_id)

    assets_deleted = 0
    for asset_type, ids in asset_ids.items():
        for i in range(0, len(ids), 20):
            chunk = ids[i : i + 20]
            if not delete_assets:
                break
            resp = client.request(
                delete_assets,
                {"input": {"assets": [{"assetType": asset_type, "assetIds": chunk}]}},
            )
            payload = resp.get("deleteAssets")
            if isinstance(payload, dict) and payload.get("success"):
                assets_deleted += len(chunk)
            elif payload:
                assets_deleted += len(chunk)

    projects_deleted = 0
    if delete_project:
        for project_id in project_ids:
            try:
                resp = client.request(delete_project, {"input": {"projectId": project_id}})
                if resp.get("deleteProject"):
                    projects_deleted += 1
            except Exception as exc:
                log.warning("deleteProject %s failed: %s", project_id, exc)

    if assets_deleted or projects_deleted:
        log.info(
            "Deleted automation assets: %d asset(s), %d project(s)",
            assets_deleted,
            projects_deleted,
        )
    return assets_deleted, projects_deleted


def resolve_notification_user_id(client: GraphQLClient) -> str:
    explicit = os.getenv("NOTIFICATION_CLEANUP_USER_ID", "").strip()
    if explicit:
        return explicit
    profile = client.request(GET_PROFILE.strip())
    return str((profile.get("getProfile") or {}).get("id") or "")


def run_pre_run_cleanup(project_root) -> CleanupResult:
    """Clear notification DB rows before E2E so the UI run starts from a clean slate."""
    from .notifications_db import delete_notifications_for_user

    result = CleanupResult(errors=[])
    if not _bool_env("CLEANUP_NOTIFICATIONS_BEFORE_E2E", False):
        log.info("Pre-run notification cleanup disabled (notifications kept for DB/UI verification)")
        return result

    cfg = load_simulation_config(project_root)
    client = GraphQLClient(cfg)

    try:
        user_id = resolve_notification_user_id(client)
        if user_id:
            result.notifications_deleted = delete_notifications_for_user(user_id)
        else:
            result.errors.append("Could not resolve notification user_id for DB cleanup")
    except Exception as exc:
        log.error("Pre-run notification cleanup failed: %s", exc)
        result.errors.append(str(exc))

    return result


def run_post_run_cleanup(project_root) -> CleanupResult:
    """Remove automation test data after E2E (roles/teams/assets; notifications kept)."""
    result = CleanupResult(errors=[])
    cfg = load_simulation_config(project_root)
    docs = load_graphql_documents(str(project_root))
    client = GraphQLClient(cfg)

    try:
        profile = client.request(GET_PROFILE.strip())
        customer_id = str(((profile.get("getProfile") or {}).get("customer") or {}).get("id") or "")
        context_id = customer_context_header_id(
            use_customer_context=cfg.use_customer_context,
            customer_context_id=cfg.customer_context_id,
            profile_customer_id=customer_id,
        )
        if context_id:
            client.set_customer_id(context_id)

        if _bool_env("CLEANUP_AUTOMATION_ASSETS", True):
            assets, projects = cleanup_automation_assets(client, docs)
            result.assets_deleted = assets
            result.projects_deleted = projects
        else:
            log.info("Asset cleanup disabled (CLEANUP_AUTOMATION_ASSETS=false)")

        if _bool_env("CLEANUP_AFTER_E2E", True):
            result.roles_deleted = cleanup_automation_roles(client, customer_id, docs)
            result.teams_deleted = cleanup_automation_teams(client, docs)
        else:
            log.info("Role/team cleanup disabled (CLEANUP_AFTER_E2E=false)")
    except Exception as exc:
        log.error("Post-run cleanup failed: %s", exc)
        result.errors.append(str(exc))

    return result
