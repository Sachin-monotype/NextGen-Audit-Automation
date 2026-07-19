"""Execute a single (operation, touchpoint) GraphQL scenario with cleanup.

Creates list/project assets as needed, runs the trigger mutation with
schema-correct variables, then deletes created assets when cleanup is on
(default) so we don't litter PP with automation data.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from audit_validator.touchpoint.payloads import SeedIds, variables_for
from audit_validator.touchpoint.assertions import assert_raw_input_matches_touchpoint
from audit_validator.utility.operation_graphql import get_document_for_operation

log = logging.getLogger(__name__)

LogFn = Callable[[str], None]


@dataclass
class ScenarioResult:
    operation: str
    touchpoint: str
    scenario_id: str
    status: str  # PASS | FAIL | SKIP
    correlation_id: str | None = None
    error: str | None = None
    created_list_ids: list[str] = field(default_factory=list)
    created_project_ids: list[str] = field(default_factory=list)
    step_results: list[dict[str, Any]] = field(default_factory=list)


def _cleanup_enabled() -> bool:
    raw = (os.getenv("GENERATE_CLEANUP", "1") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _make_seed(cfg: Any, *, project_root: Path | None = None, operation: str = "") -> SeedIds:
    """Build seed IDs — prefer live GetFamilyStatus discovery over .env/hardcodes."""
    s = getattr(cfg, "seed", None)
    use_env = bool(os.getenv("TOUCHPOINT_USE_ENV_SEED"))
    family = ""
    style = ""
    md5 = ""

    # Dynamic discovery first (avoids stale SEED_FAMILY_ID=794981 from profile)
    if not use_env:
        try:
            from audit_validator.live_seeds import discover_font_seed

            discovered = discover_font_seed(
                operation or "",
                project_root=project_root,
                prefer_deactivated=operation in {
                    "activateFamily", "activateStyle", "activateVariation",
                    "addFavoriteFamilies", "addFavoriteStyles",
                },
                prefer_activated=operation in {
                    "deactivateFamilies", "deactivateStyle", "deactivateVariation",
                },
            )
            if discovered:
                family = str(discovered.get("family_id") or "")
                style = str(discovered.get("style_id") or "")
                md5 = str(discovered.get("md5") or "")
        except Exception:
            pass

    if not family:
        family = (
            (os.getenv("TOUCHPOINT_FAMILY_ID") or "").strip()
            or (getattr(s, "family_id", None) or "")
            or ""
        )
    if not style:
        style = (
            (os.getenv("TOUCHPOINT_STYLE_ID") or "").strip()
            or (os.getenv("SEED_STYLE_ID") or "").strip()
            or (getattr(s, "style_id", None) or "")
            or ""
        )
    if not md5:
        md5 = (
            (os.getenv("SEED_VARIATION_MD5") or "").strip()
            or (getattr(s, "variation_md5", None) or "")
            or ""
        )

    # Known-bad / inventory-missing Discovery demo id — never use unless forced
    if str(family) == "794981" and not use_env:
        family = (os.getenv("TOUCHPOINT_FAMILY_ID") or "").strip()
        if not family:
            try:
                from audit_validator.live_seeds import KNOWN_FAMILY_POOL

                family = KNOWN_FAMILY_POOL[0]
            except Exception:
                family = "910042901"

    if not family:
        try:
            from audit_validator.live_seeds import KNOWN_FAMILY_POOL

            family = KNOWN_FAMILY_POOL[4]  # Helvetica® Now
        except Exception:
            family = "910042901"
    style = style or "920374778"
    md5 = md5 or "b783215634650cf0a55e0d723123d5e0"

    fav = getattr(s, "favorite_family_id", None) or family
    ts = int(time.time())
    gcid = (
        getattr(cfg, "customer_context_id", None)
        or os.getenv("OAUTH_GCID")
        or os.getenv("GRAPHQL_CONTEXT_CUSTOMER_ID")
        or ""
    )
    return SeedIds(
        family_id=str(fav or family),
        style_id=str(style),
        md5=str(md5),
        list_name=f"QA_Gen_List_{ts}",
        project_name=f"QA_Gen_Proj_{ts}",
        customer_id=str(gcid),
        profile_id=(os.getenv("SEED_DELETE_PROFILE_ID") or "").strip(),
        notification_id=(os.getenv("SEED_NOTIFICATION_ID") or "").strip(),
        tag_id=(os.getenv("SEED_TAG_ID") or "").strip(),
        headline_style_id=(
            (os.getenv("SEED_HEADLINE_STYLE_ID") or "").strip()
            or str(getattr(s, "headline_style_id", None) or "")
            or "920142132"
        ),
        body_style_id=(
            (os.getenv("SEED_BODY_STYLE_ID") or "").strip()
            or str(getattr(s, "body_style_id", None) or "")
            or "920233774"
        ),
        role_id=(os.getenv("SEED_ROLE_ID") or "").strip(),
        team_id=(os.getenv("SEED_TEAM_ID") or "").strip(),
    )

def _request(client: Any, operation: str, variables: dict[str, Any]) -> dict[str, Any]:
    # Seed helpers that reuse createAsset GraphQL document
    doc_op = "createAsset" if operation == "createFolder" else operation
    doc = get_document_for_operation(doc_op)
    if not doc:
        raise RuntimeError(f"No GraphQL document for {operation}")
    return client.request(doc, variables) or {}


def _capture_seed_ids(seed: SeedIds, step_op: str, data: dict[str, Any]) -> None:
    """Pull created entity IDs from mutation responses into seed."""
    if step_op == "createAsset":
        asset = ((data.get("createAsset") or {}).get("asset") or {})
        lid = asset.get("id") or ""
        if lid:
            seed.list_id = lid
        return
    if step_op == "createFolder":
        asset = ((data.get("createAsset") or {}).get("asset") or {})
        fid = asset.get("id") or ""
        if fid:
            seed.folder_id = fid
        return
    if step_op == "createProject":
        asset = ((data.get("createProject") or {}).get("asset") or {})
        pid = asset.get("id") or ""
        if pid:
            seed.project_id = pid
        return
    if step_op == "createPrivateTags":
        node = data.get("createPrivateTags") or {}
        rows = node.get("data") or []
        if rows and isinstance(rows[0], dict):
            tag = (rows[0].get("tag") or {}) if isinstance(rows[0].get("tag"), dict) else {}
            tid = tag.get("id") or rows[0].get("id") or ""
            if tid:
                seed.tag_id = str(tid)
        return
    if step_op == "createUploadSession":
        session = ((data.get("createUploadSession") or {}).get("session") or {})
        sid = session.get("sessionId") or ""
        if sid:
            seed.session_id = str(sid)
        files = session.get("files") or []
        if files and isinstance(files[0], dict) and files[0].get("fileId"):
            seed.file_id = str(files[0]["fileId"])
        return
    if step_op == "createServiceAccount":
        sa = data.get("createServiceAccount") or {}
        if isinstance(sa, dict) and sa.get("id"):
            seed.service_account_id = str(sa["id"])
        return
    if step_op == "createContract":
        c = data.get("createContract") or {}
        if isinstance(c, dict) and c.get("contractId"):
            seed.contract_id = str(c["contractId"])
        return
    if step_op == "getNotifications":
        nid = _extract_notification_id(data)
        if nid:
            seed.notification_id = nid
        return
    if step_op == "getCustomerSettings":
        _capture_customer_settings(seed, data)
        return
    if step_op == "createRole":
        role = ((data.get("createRole") or {}).get("role") or {})
        rid = role.get("id") or ""
        if rid:
            seed.role_id = str(rid)
        return
    if step_op == "createTeam":
        team = data.get("createTeam") or {}
        tid = team.get("id") if isinstance(team, dict) else ""
        if tid:
            seed.team_id = str(tid)
        return
    if step_op == "getRoles":
        nodes = ((data.get("getRoles") or {}).get("nodes") or [])
        for node in nodes:
            if not isinstance(node, dict):
                continue
            # Prefer a non-system role when possible
            rid = node.get("id")
            if rid:
                seed.role_id = str(rid)
                break
        return
    if step_op == "getTeams":
        nodes = ((data.get("getTeams") or {}).get("nodes") or [])
        for node in nodes:
            if isinstance(node, dict) and node.get("id"):
                seed.team_id = str(node["id"])
                break
        return
    if step_op == "getProfiles":
        nodes = ((data.get("getProfiles") or {}).get("nodes") or [])
        self_email = (os.getenv("OAUTH_USERNAME") or os.getenv("TOKEN_EMAIL") or "").strip().lower()
        for node in nodes:
            if not isinstance(node, dict):
                continue
            user = node.get("user") or {}
            email = str((user.get("email") if isinstance(user, dict) else "") or "").lower()
            pid = node.get("id")
            if not pid:
                continue
            # Prefer another active user (not the actor) for bulk updates
            if self_email and email == self_email:
                continue
            if node.get("isActive") is False:
                continue
            seed.profile_id = str(pid)
            break
        # Fallback: first profile if none other found
        if not seed.profile_id and nodes and isinstance(nodes[0], dict) and nodes[0].get("id"):
            seed.profile_id = str(nodes[0]["id"])
        return
    if step_op == "duplicateProject":
        root = data.get("bulkCopyAssets") or data.get("duplicateProject") or {}
        results = root.get("results") if isinstance(root, dict) else None
        if isinstance(results, list):
            for row in results:
                if not isinstance(row, dict):
                    continue
                copied = row.get("copiedAsset") or {}
                cid = copied.get("id") if isinstance(copied, dict) else ""
                if cid:
                    # Stash duplicate project id for cleanup (document_id reused as temp)
                    seed.document_id = str(cid)
                    break
        return
    # Bulk ops return BatchProgress with batchId
    node = data.get(step_op)
    if isinstance(node, dict) and node.get("batchId"):
        seed.batch_id = str(node["batchId"])


# Preconditions that may already be satisfied (already favourite / already deactivated).
_SOFT_FAIL_PRECONDITIONS = frozenset(
    {
        "removeFavoriteFamilies",
        "removeFavoriteStyles",
        "removeFavoritePair",
        "deactivateFamilies",
        "deactivateStyle",
        "deactivateVariation",
        "deActivateList",
        "deActivateFontProject",
        "getNotifications",
        "activateFamily",
        "activateStyle",
        "activateVariation",
        "activateList",
        "activateFontProject",
    }
)

# Target op name → response root field when GraphQL document uses a different field
_STEP_RESPONSE_ROOT = {
    "createFolder": "createAsset",
    "duplicateProject": "bulkCopyAssets",
}


def _mutation_errors(data: dict[str, Any], operation: str) -> list[Any]:
    root = _STEP_RESPONSE_ROOT.get(operation, operation)
    node = data.get(root) if root in data else data.get(operation)
    if isinstance(node, dict):
        return list(node.get("errors") or [])
    return []


def _extract_success(data: dict[str, Any], operation: str) -> bool | None:
    """True/False when payload has ``success``; None when absent (check errors)."""
    root = _STEP_RESPONSE_ROOT.get(operation, operation)
    node = data.get(root) if root in data else data.get(operation)
    if isinstance(node, dict) and "success" in node:
        return bool(node.get("success"))
    return None


def _step_ok(data: dict[str, Any], operation: str) -> tuple[bool, str | None]:
    """Decide if a mutation step succeeded (schema payloads vary)."""
    root = _STEP_RESPONSE_ROOT.get(operation, operation)
    node = data.get(root) if root in data else data.get(operation)
    if node is None and data.get("errors"):
        return False, str(data["errors"])[:300]
    errs = _mutation_errors(data, operation)
    if errs:
        return False, str(errs)[:300]
    ok = _extract_success(data, operation)
    if ok is False:
        return False, str(errs or node)[:300]
    # activateFamily etc. omit ``success`` — empty errors + payload = pass
    if isinstance(node, dict) or node is True:
        return True, None
    if node is False:
        return False, f"{operation} returned false"
    return True, None


def _extract_notification_id(data: dict[str, Any]) -> str:
    """Pick a dismissable notification id from getNotifications response shapes."""
    root = data.get("getNotifications") or data.get("notifications") or {}
    if not isinstance(root, dict):
        return ""
    nodes = root.get("nodes") or root.get("items") or root.get("edges") or []
    if not isinstance(nodes, list):
        return ""
    for node in nodes:
        if not isinstance(node, dict):
            continue
        # Nested children (UI notification groups)
        children = node.get("children") or []
        if isinstance(children, list):
            for child in children:
                if not isinstance(child, dict):
                    continue
                if child.get("isExpired"):
                    continue
                cid = child.get("id")
                if cid:
                    return str(cid)
        nid = node.get("id") or (
            (node.get("node") or {}).get("id") if isinstance(node.get("node"), dict) else None
        )
        if nid:
            return str(nid)
    return ""


def _capture_customer_settings(seed: SeedIds, data: dict[str, Any]) -> None:
    settings = data.get("getCustomerSettings") or {}
    if not isinstance(settings, dict):
        return
    seed.customer_display_name = str(settings.get("displayName") or settings.get("name") or "")
    seed.customer_supported_language = str(settings.get("supportedLanguage") or "EN")
    primary = settings.get("primaryContact") or {}
    email = ""
    if isinstance(primary, dict):
        user = primary.get("user") or {}
        if isinstance(user, dict):
            email = str(user.get("email") or "")
        if not email:
            email = str(primary.get("id") or "")
    seed.customer_primary_contact = email
    overrides = settings.get("companySettingsOverrides") or {}
    app_settings = settings.get("settings") or {}
    flags: dict[str, Any] = {}
    if isinstance(overrides, dict):
        flags["enableDownload"] = overrides.get("enableDownload", True)
        flags["enableImportedFonts"] = overrides.get("enableImportedFonts", False)
        flags["enableFontFormatSelection"] = overrides.get(
            "enableFontFormatSelection", True
        )
        flags["enableWebFontAccess"] = overrides.get("enableWebFontAccess", False)
        flags["enableSelfHostingKit"] = overrides.get("enableSelfHostingKits", True)
    if isinstance(app_settings, dict):
        flags["shareIntentForProduction"] = app_settings.get(
            "shareIntentForProduction", False
        )
        flags["markUnmarkFontsAsProduction"] = app_settings.get(
            "markUnmarkFontsAsProduction", False
        )
    seed.customer_settings_flags = flags
    if settings.get("id"):
        seed.customer_id = str(settings["id"])


def _cleanup(
    client: Any,
    seed: SeedIds,
    created_lists: list[str],
    created_projects: list[str],
    log_fn: LogFn,
    *,
    touchpoint: str = "",
    reverse_activation: str | None = None,
) -> None:
    """Delete created assets; optionally reverse activation so PP stays clean."""
    if not _cleanup_enabled():
        log_fn("  ↷ cleanup skipped (GENERATE_CLEANUP=0)")
        return
    if reverse_activation:
        try:
            vars_ = variables_for(reverse_activation, seed, touch=touchpoint)
            _request(client, reverse_activation, vars_)
            log_fn(f"  ↺ {reverse_activation} (post-scenario)")
        except Exception as exc:  # noqa: BLE001
            log_fn(f"  ⚠ reverse {reverse_activation} failed: {exc}")
    for lid in created_lists:
        try:
            _request(
                client,
                "deleteAssets",
                {"input": {"assets": [{"assetType": "FontList", "assetIds": [lid]}]}},
            )
            log_fn(f"  🗑 deleted FontList {lid[:8]}…")
        except Exception as exc:  # noqa: BLE001
            log_fn(f"  ⚠ delete list failed: {exc}")
    for pid in created_projects:
        deleted = False
        try:
            data = _request(client, "deleteProject", {"input": {"projectId": pid}})
            node = data.get("deleteProject") or {}
            if isinstance(node, dict) and node.get("success") is False:
                raise RuntimeError(str(node.get("errors") or node)[:200])
            deleted = True
            log_fn(f"  🗑 deleted FontProject {pid[:8]}… (deleteProject)")
        except Exception as exc:  # noqa: BLE001
            log_fn(f"  ⚠ deleteProject failed, trying deleteAssets: {exc}")
        if not deleted:
            try:
                data = _request(
                    client,
                    "deleteAssets",
                    {
                        "input": {
                            "assets": [{"assetType": "FontProject", "assetIds": [pid]}]
                        }
                    },
                )
                node = data.get("deleteAssets") or {}
                if isinstance(node, dict) and node.get("success") is False:
                    log_fn(
                        f"  ⚠ deleteAssets FontProject rejected: "
                        f"{str(node.get('errors') or node)[:180]} "
                        f"(PP AMS deleteProject bug — project {pid[:8]}… left; "
                        f"name still QA_Gen_* for later cleanup)"
                    )
                else:
                    log_fn(f"  🗑 deleted FontProject {pid[:8]}… (deleteAssets)")
            except Exception as exc:  # noqa: BLE001
                log_fn(f"  ⚠ delete project failed: {exc}")


# activate* → matching deactivate* so we don't leave permanent activations
_REVERSE_ACTIVATION = {
    "activateFamily": "deactivateFamilies",
    "activateStyle": "deactivateStyle",
    "activateVariation": "deactivateVariation",
    "activateList": "deActivateList",
    "activateFontProject": "deActivateFontProject",
    "bulkActivateStyles": "bulkDeactivateStyles",
    "bulkActivateLists": "bulkDeactivateLists",
}


def run_scenario(
    *,
    client: Any,
    cfg: Any,
    operation: str,
    touchpoint: str,
    steps: list[str],
    scenario_id: str,
    log_fn: LogFn | None = None,
) -> ScenarioResult:
    """Run multi-step touchpoint flow; return correlation of the *target* operation."""
    _log = log_fn or (lambda m: None)
    project_root = getattr(cfg, "project_root", None)
    if project_root is not None:
        project_root = Path(project_root)
    seed = _make_seed(cfg, project_root=project_root, operation=operation)
    created_lists: list[str] = []
    created_projects: list[str] = []
    step_results: list[dict[str, Any]] = []
    target_cid: str | None = None
    last_error: str | None = None
    _log(
        f"▸ Scenario {operation} · {touchpoint} ({len(steps)} steps) "
        f"seed family={seed.family_id} style={seed.style_id}"
    )

    try:
        for step_op in steps:
            vars_ = variables_for(step_op, seed, touch=touchpoint)
            # Skip steps that need ids we don't have yet (shouldn't happen if order correct)
            if step_op in {"addFontListFamilies", "addFontListStyles", "activateList", "deActivateList"} and not seed.list_id:
                if step_op != "createAsset":
                    last_error = f"missing list_id before {step_op}"
                    step_results.append({"op": step_op, "status": "SKIP", "error": last_error})
                    continue
            if step_op in {"addFontProjectFamilies", "addFontProjectStyles", "activateFontProject"} and not seed.project_id:
                if step_op != "createProject":
                    last_error = f"missing project_id before {step_op}"
                    step_results.append({"op": step_op, "status": "SKIP", "error": last_error})
                    continue

            try:
                data = _request(client, step_op, vars_)
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                step_results.append({"op": step_op, "status": "FAIL", "error": last_error})
                _log(f"  ✖ {step_op}: {exc}")
                if step_op == operation:
                    return ScenarioResult(
                        operation=operation,
                        touchpoint=touchpoint,
                        scenario_id=scenario_id,
                        status="FAIL",
                        error=last_error,
                        created_list_ids=created_lists,
                        created_project_ids=created_projects,
                        step_results=step_results,
                    )
                continue

            # Pre-flight: generated variables match touchpoint input contract
            if step_op == operation:
                sent = (vars_ or {}).get("input")
                if isinstance(sent, dict):
                    shape_errs = assert_raw_input_matches_touchpoint(
                        operation, touchpoint, sent
                    )
                    for msg in shape_errs:
                        _log(f"  ⚠ input shape: {msg}")
                    if shape_errs:
                        last_error = "; ".join(shape_errs)
                        step_results.append(
                            {
                                "op": step_op,
                                "status": "FAIL",
                                "error": last_error,
                                "cid": getattr(client, "last_correlation_id", None),
                            }
                        )
                        _cleanup(
                            client,
                            seed,
                            created_lists,
                            created_projects,
                            _log,
                            touchpoint=touchpoint,
                        )
                        return ScenarioResult(
                            operation=operation,
                            touchpoint=touchpoint,
                            scenario_id=scenario_id,
                            status="FAIL",
                            correlation_id=getattr(client, "last_correlation_id", None),
                            error=last_error,
                            created_list_ids=created_lists,
                            created_project_ids=created_projects,
                            step_results=step_results,
                        )

            cid = getattr(client, "last_correlation_id", None)
            if step_op == operation:
                target_cid = cid

            check_op = "createAsset" if step_op == "createFolder" else step_op
            ok, err_msg = _step_ok(data, check_op)
            # Queries: treat presence of data as success
            if step_op in {
                "getNotifications",
                "getCustomerSettings",
                "getProfiles",
                "getTeams",
                "getRoles",
            } and (data.get(step_op) is not None or ok):
                ok = True
                err_msg = None
            if (
                not ok
                and step_op != operation
                and step_op in _SOFT_FAIL_PRECONDITIONS
            ):
                _log(f"  ↷ {step_op} precondition soft-fail (continuing): {err_msg}")
                step_results.append(
                    {
                        "op": step_op,
                        "status": "SOFT",
                        "error": err_msg,
                        "cid": cid,
                    }
                )
                _capture_seed_ids(seed, step_op, data)
                continue
            if not ok and step_op not in {"createAsset", "createProject", "createFolder"}:
                last_error = err_msg or str(data)[:300]
                step_results.append(
                    {"op": step_op, "status": "FAIL", "error": last_error, "cid": cid}
                )
                _log(f"  ✖ {step_op} {last_error}")
                if step_op == operation:
                    _cleanup(
                        client,
                        seed,
                        created_lists,
                        created_projects,
                        _log,
                        touchpoint=touchpoint,
                        reverse_activation=_REVERSE_ACTIVATION.get(operation),
                    )
                    return ScenarioResult(
                        operation=operation,
                        touchpoint=touchpoint,
                        scenario_id=scenario_id,
                        status="FAIL",
                        correlation_id=cid,
                        error=last_error,
                        created_list_ids=created_lists,
                        created_project_ids=created_projects,
                        step_results=step_results,
                    )
                continue

            # Fail dismiss/mark when getNotifications yielded no id
            if step_op in {"dismissNotification", "markNotificationRead"} and not (
                seed.notification_id or ""
            ).strip():
                last_error = "no notification id from getNotifications"
                step_results.append(
                    {"op": step_op, "status": "FAIL", "error": last_error, "cid": cid}
                )
                _log(f"  ✖ {step_op} {last_error}")
                if step_op == operation:
                    return ScenarioResult(
                        operation=operation,
                        touchpoint=touchpoint,
                        scenario_id=scenario_id,
                        status="FAIL",
                        correlation_id=cid,
                        error=last_error,
                        created_list_ids=created_lists,
                        created_project_ids=created_projects,
                        step_results=step_results,
                    )
                continue

            if step_op == "bulkUpdateProfiles" and (
                not (seed.profile_id or "").strip() or not (seed.team_id or "").strip()
            ):
                last_error = (
                    f"bulkUpdateProfiles needs profile+team "
                    f"(profile={seed.profile_id or '?'}, team={seed.team_id or '?'})"
                )
                step_results.append(
                    {"op": step_op, "status": "FAIL", "error": last_error, "cid": cid}
                )
                _log(f"  ✖ {step_op} {last_error}")
                if step_op == operation:
                    return ScenarioResult(
                        operation=operation,
                        touchpoint=touchpoint,
                        scenario_id=scenario_id,
                        status="FAIL",
                        correlation_id=cid,
                        error=last_error,
                        created_list_ids=created_lists,
                        created_project_ids=created_projects,
                        step_results=step_results,
                    )
                continue

            if step_op == "createUserInvitations" and not (seed.role_id or "").strip():
                last_error = "createUserInvitations needs roleId from getRoles"
                step_results.append(
                    {"op": step_op, "status": "FAIL", "error": last_error, "cid": cid}
                )
                _log(f"  ✖ {step_op} {last_error}")
                if step_op == operation:
                    return ScenarioResult(
                        operation=operation,
                        touchpoint=touchpoint,
                        scenario_id=scenario_id,
                        status="FAIL",
                        correlation_id=cid,
                        error=last_error,
                        created_list_ids=created_lists,
                        created_project_ids=created_projects,
                        step_results=step_results,
                    )
                continue

            # Capture created ids
            _capture_seed_ids(seed, step_op, data)
            if step_op == "createAsset":
                if not seed.list_id:
                    last_error = f"createAsset returned no id: {str(data)[:200]}"
                    step_results.append(
                        {"op": step_op, "status": "FAIL", "error": last_error, "cid": cid}
                    )
                    _log(f"  ✖ {step_op} {last_error}")
                    continue
                created_lists.append(seed.list_id)
                _log(f"  ✓ createAsset list={seed.list_id[:8]}…")
            elif step_op == "createFolder":
                if not seed.folder_id:
                    last_error = f"createFolder returned no id: {str(data)[:200]}"
                    step_results.append(
                        {"op": step_op, "status": "FAIL", "error": last_error, "cid": cid}
                    )
                    _log(f"  ✖ {step_op} {last_error}")
                    continue
                created_lists.append(seed.folder_id)
                _log(f"  ✓ createFolder folder={seed.folder_id[:8]}…")
            elif step_op == "createProject":
                if not seed.project_id:
                    last_error = f"createProject returned no id: {str(data)[:200]}"
                    step_results.append(
                        {"op": step_op, "status": "FAIL", "error": last_error, "cid": cid}
                    )
                    _log(f"  ✖ {step_op} {last_error}")
                    continue
                created_projects.append(seed.project_id)
                _log(f"  ✓ createProject project={seed.project_id[:8]}…")
            elif step_op == "duplicateProject":
                dup_id = (seed.document_id or "").strip()
                if dup_id and dup_id not in created_projects:
                    created_projects.append(dup_id)
                _log(
                    f"  ✓ duplicateProject source={(seed.project_id or '')[:8]}… "
                    f"copy={(dup_id or '?')[:8]}…"
                )
            elif step_op == "createRole":
                _log(f"  ✓ createRole id={seed.role_id or '?'}")
            elif step_op == "createTeam":
                _log(f"  ✓ createTeam id={seed.team_id or '?'}")
            elif step_op == "getProfiles":
                _log(f"  ✓ getProfiles profile={seed.profile_id or '?'}")
            elif step_op == "getTeams":
                _log(f"  ✓ getTeams team={seed.team_id or '?'}")
            elif step_op == "getRoles":
                _log(f"  ✓ getRoles role={seed.role_id or '?'}")
            elif step_op == "createPrivateTags":
                _log(f"  ✓ createPrivateTags tag={seed.tag_id or '?'}")
            elif step_op == "createUploadSession":
                _log(
                    f"  ✓ createUploadSession session={(seed.session_id or '')[:8]}…"
                )
            elif step_op == "getNotifications":
                _log(f"  ✓ getNotifications id={seed.notification_id or '?'}")
            elif step_op == "getCustomerSettings":
                _log(
                    f"  ✓ getCustomerSettings display={seed.customer_display_name or '?'} "
                    f"lang={seed.customer_supported_language or '?'}"
                )
            else:
                _log(
                    f"  ✓ {step_op} cid={(cid or '')[:8]} "
                    f"success={_extract_success(data, step_op)}"
                )

            step_results.append(
                {
                    "op": step_op,
                    "status": "PASS",
                    "cid": cid,
                    "success": _extract_success(data, step_op),
                    **(
                        {
                            "input": (vars_ or {}).get("input"),
                            "response": data,
                        }
                        if step_op == operation
                        else {}
                    ),
                }
            )

        status = "PASS" if not last_error or target_cid else "FAIL"
        # If target step ran without hard fail, PASS
        target_steps = [s for s in step_results if s.get("op") == operation]
        if target_steps and target_steps[-1].get("status") == "PASS":
            status = "PASS"
        elif target_steps and target_steps[-1].get("status") == "FAIL":
            status = "FAIL"
        elif not target_steps:
            status = "FAIL"
            last_error = last_error or f"target op {operation} not executed"

        reverse = _REVERSE_ACTIVATION.get(operation) if status == "PASS" else None
        _cleanup(
            client,
            seed,
            created_lists,
            created_projects,
            _log,
            touchpoint=touchpoint,
            reverse_activation=reverse,
        )
        return ScenarioResult(
            operation=operation,
            touchpoint=touchpoint,
            scenario_id=scenario_id,
            status=status,
            correlation_id=target_cid,
            error=last_error if status == "FAIL" else None,
            created_list_ids=created_lists,
            created_project_ids=created_projects,
            step_results=step_results,
        )
    except Exception as exc:  # noqa: BLE001
        _cleanup(
            client,
            seed,
            created_lists,
            created_projects,
            _log,
            touchpoint=touchpoint,
        )
        return ScenarioResult(
            operation=operation,
            touchpoint=touchpoint,
            scenario_id=scenario_id,
            status="FAIL",
            error=str(exc),
            created_list_ids=created_lists,
            created_project_ids=created_projects,
            step_results=step_results,
        )
