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


def _make_seed(cfg: Any) -> SeedIds:
    s = getattr(cfg, "seed", None)
    family = getattr(s, "family_id", None) or os.getenv("TOUCHPOINT_FAMILY_ID") or "910130168"
    # Prefer UI-proven family when SEED is the flaky Discovery one
    if str(family) == "794981" and not os.getenv("TOUCHPOINT_USE_ENV_SEED"):
        family = os.getenv("TOUCHPOINT_FAMILY_ID") or "910130168"
    style = (
        getattr(s, "style_id", None)
        or os.getenv("TOUCHPOINT_STYLE_ID")
        or os.getenv("SEED_STYLE_ID")
        or "920374778"
    )
    md5 = (
        getattr(s, "variation_md5", None)
        or os.getenv("SEED_VARIATION_MD5")
        or "b783215634650cf0a55e0d723123d5e0"
    )
    fav = getattr(s, "favorite_family_id", None) or family
    ts = int(time.time())
    gcid = (
        getattr(cfg, "customer_context_id", None)
        or os.getenv("OAUTH_GCID")
        or os.getenv("GRAPHQL_CONTEXT_CUSTOMER_ID")
        or ""
    )
    seed = SeedIds(
        family_id=str(fav or family),
        style_id=str(style),
        md5=str(md5),
        list_name=f"QA_Gen_List_{ts}",
        project_name=f"QA_Gen_Proj_{ts}",
        customer_id=str(gcid),
    )
    return seed


def _request(client: Any, operation: str, variables: dict[str, Any]) -> dict[str, Any]:
    doc = get_document_for_operation(operation)
    if not doc:
        raise RuntimeError(f"No GraphQL document for {operation}")
    return client.request(doc, variables) or {}


def _extract_success(data: dict[str, Any], operation: str) -> bool | None:
    """True/False when payload has ``success``; None when absent (check errors)."""
    node = data.get(operation)
    if isinstance(node, dict) and "success" in node:
        return bool(node.get("success"))
    return None


def _mutation_errors(data: dict[str, Any], operation: str) -> list[Any]:
    node = data.get(operation)
    if isinstance(node, dict):
        return list(node.get("errors") or [])
    return []


def _step_ok(data: dict[str, Any], operation: str) -> tuple[bool, str | None]:
    """Decide if a mutation step succeeded (schema payloads vary)."""
    node = data.get(operation)
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
    seed = _make_seed(cfg)
    created_lists: list[str] = []
    created_projects: list[str] = []
    step_results: list[dict[str, Any]] = []
    target_cid: str | None = None
    last_error: str | None = None

    _log(f"▸ Scenario {operation} · {touchpoint} ({len(steps)} steps)")

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

            ok, err_msg = _step_ok(data, step_op)
            if not ok and step_op not in {"createAsset", "createProject"}:
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

            # Capture created ids
            if step_op == "createAsset":
                asset = ((data.get("createAsset") or {}).get("asset") or {})
                lid = asset.get("id") or ""
                if not lid:
                    last_error = f"createAsset returned no id: {str(data)[:200]}"
                    step_results.append(
                        {"op": step_op, "status": "FAIL", "error": last_error, "cid": cid}
                    )
                    _log(f"  ✖ {step_op} {last_error}")
                    continue
                seed.list_id = lid
                created_lists.append(lid)
                _log(f"  ✓ createAsset list={lid[:8]}…")
            elif step_op == "createProject":
                asset = ((data.get("createProject") or {}).get("asset") or {})
                pid = asset.get("id") or ""
                if not pid:
                    last_error = f"createProject returned no id: {str(data)[:200]}"
                    step_results.append(
                        {"op": step_op, "status": "FAIL", "error": last_error, "cid": cid}
                    )
                    _log(f"  ✖ {step_op} {last_error}")
                    continue
                seed.project_id = pid
                created_projects.append(pid)
                _log(f"  ✓ createProject project={pid[:8]}…")
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
                        {"input": (vars_ or {}).get("input")}
                        if step_op == operation and isinstance((vars_ or {}).get("input"), dict)
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
