"""Generate simulation testing sheet (CSV) and companion markdown guide."""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from ..operation_catalog import expects_enriched_event
from ..utility.operation_meta import (
    build_simulation_curl,
    execute_simulation_preview,
    load_curl_context,
    ui_navigation,
)
from ..rabbitmq.resolver_routing_map import expected_routing_key
from ..simulation.flow_catalog import audit_operation, flow_operations
from ..template_registry import get_template

log = logging.getLogger(__name__)

QUERY_PREFIXES = ("get", "fetch")


@dataclass(frozen=True)
class SimulationRow:
    flow: str
    simulation_name: str
    graphql_operation: str
    ui_navigation: str
    expected_routing_key: str
    enriched_snapshot: str
    ui_verify: str
    curl: str
    curl_status: str
    curl_detail: str
    uses_secondary_token: bool
    skipped_by_default: bool


def _load_testing_config(project_root: Path) -> dict[str, dict]:
    path = project_root / "config" / "simulation_testing.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _curated_simulations(project_root: Path) -> frozenset[str]:
    path = project_root / "config" / "simulation_testing.json"
    if not path.is_file():
        return frozenset()
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data.get("_curated_simulations", [])
    return frozenset(str(x) for x in raw)


def _default_ui_verify(label: str, graphql_op: str) -> str:
    lower = label.lower()
    if graphql_op.startswith(QUERY_PREFIXES) or graphql_op.startswith("get"):
        return "NO"
    if any(x in lower for x in ("grant", "revoke", "notification", "invitation", "publish")):
        return "YES"
    if graphql_op in {
        "updateAssetSharing",
        "createUserInvitations",
        "publishProject",
        "updateProfile",
        "bulkUpdateProfiles",
    }:
        return "YES"
    if graphql_op.startswith(("activate", "deactivate", "add", "remove", "create", "update", "delete")):
        return "OPTIONAL"
    return "NO"


def _enriched_snapshot_summary(graphql_op: str, override: str | None) -> str:
    if override:
        return override
    tpl = get_template(graphql_op)
    if not tpl:
        if expects_enriched_event(graphql_op):
            return "Pair raw + enriched by xCorrelationId; check source.operation and subject.metadata"
        return "Raw event only (query or unmapped mutation)"
    parts: list[str] = []
    if tpl.requires_actor_enrichment:
        parts.append("actor.enrichedSnapshot.user.profile + customer.subscription")
    if tpl.requires_subject_enrichment and tpl.subject_snap_keys:
        keys = ", ".join(sorted(tpl.subject_snap_keys))
        parts.append(f"subject.enrichedSnapshot ({keys})")
    if tpl.requires_font_details:
        parts.append("fontDetails catalog (family/styles/variations)")
    if tpl.requires_asset_sharing:
        parts.append("sharingInfo accessId + sharee")
    return "; ".join(parts) if parts else "Envelope + source.operationState success"


def _ui_for_label(project_root: Path, label: str, graphql_op: str, cfg: dict) -> str:
    if label in cfg and cfg[label].get("ui_navigation"):
        return cfg[label]["ui_navigation"]
    return ui_navigation(graphql_op) or ui_navigation(label) or ""


def build_simulation_rows(
    project_root: Path,
    *,
    validate_curls: bool = True,
    only_working: bool = False,
) -> list[SimulationRow]:
    cfg = _load_testing_config(project_root)
    ctx = load_curl_context(project_root)
    raw_env = {}
    env_path = project_root / ".env"
    if env_path.is_file():
        from dotenv import dotenv_values

        raw_env = {k: v for k, v in dotenv_values(env_path).items() if v}
    secondary = raw_env.get("BEARER_TOKEN_SECONDARY", "")

    rows: list[SimulationRow] = []
    for op in flow_operations():
        label = op.label
        graphql_op = op.graphql_operation
        curl = build_simulation_curl(
            label,
            project_root,
            ctx,
            bearer_token=secondary if op.uses_secondary_token else None,
        )
        curl_status = "SKIP"
        curl_detail = ""
        if validate_curls and not op.skipped_by_default:
            token = secondary if op.uses_secondary_token else None
            status, detail = execute_simulation_preview(
                label, project_root, ctx, bearer_token=token
            )
            if status.startswith("HTTP") and "ERROR" not in detail:
                curl_status = "OK"
            elif status == "SKIP":
                curl_status = "SKIP"
            else:
                curl_status = "FAIL"
            curl_detail = detail[:300]
        elif op.skipped_by_default:
            curl_status = "SKIP"
            curl_detail = "skipped by default in automation"
        else:
            curl_status = "NOT_RUN"

        if only_working and curl_status not in {"OK", "SKIP", "NOT_RUN"}:
            continue

        entry = cfg.get(label, {})
        rows.append(
            SimulationRow(
                flow=op.flow,
                simulation_name=label,
                graphql_operation=graphql_op,
                ui_navigation=_ui_for_label(project_root, label, graphql_op, cfg),
                expected_routing_key=expected_routing_key(graphql_op) or "",
                enriched_snapshot=_enriched_snapshot_summary(
                    graphql_op, entry.get("enriched_notes")
                ),
                ui_verify=entry.get("ui_verify") or _default_ui_verify(label, graphql_op),
                curl=curl,
                curl_status=curl_status,
                curl_detail=curl_detail,
                uses_secondary_token=op.uses_secondary_token,
                skipped_by_default=op.skipped_by_default,
            )
        )
    return rows


def write_testing_sheet_csv(
    *,
    path: Path,
    project_root: Path,
    validate_curls: bool = True,
    rows: list[SimulationRow] | None = None,
) -> int:
    rows = rows or build_simulation_rows(project_root, validate_curls=validate_curls)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "flow",
                "simulation_name",
                "graphql_operation",
                "ui_navigation",
                "expected_routing_key",
                "enriched_snapshot",
                "ui_verify",
                "curl_status",
                "curl_detail",
                "uses_secondary_token",
                "skipped_by_default",
                "cURL",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.flow,
                    row.simulation_name,
                    row.graphql_operation,
                    row.ui_navigation,
                    row.expected_routing_key,
                    row.enriched_snapshot,
                    row.ui_verify,
                    row.curl_status,
                    row.curl_detail,
                    "yes" if row.uses_secondary_token else "no",
                    "yes" if row.skipped_by_default else "no",
                    row.curl,
                ]
            )
    ok = sum(1 for r in rows if r.curl_status == "OK")
    log.info("Wrote %s (%d simulations, %d curl OK)", path, len(rows), ok)
    return ok


def write_working_sheet_csv(
    *,
    path: Path,
    project_root: Path,
    validate_curls: bool = True,
    rows: list[SimulationRow] | None = None,
) -> int:
    """Subset sheet — simulations with OK cURL plus curated picks from config."""
    all_rows = rows or build_simulation_rows(project_root, validate_curls=validate_curls)
    cfg = _load_testing_config(project_root)
    curated = _curated_simulations(project_root)
    rows_out = [
        r
        for r in all_rows
        if r.curl_status == "OK" or r.simulation_name in curated
    ]
    # de-dupe while preserving order
    seen: set[str] = set()
    unique: list[SimulationRow] = []
    for r in rows_out:
        if r.simulation_name in seen:
            continue
        seen.add(r.simulation_name)
        unique.append(r)
    rows_out = unique
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "flow",
                "simulation_name",
                "graphql_operation",
                "ui_navigation",
                "expected_routing_key",
                "enriched_snapshot",
                "ui_verify",
                "cURL",
            ]
        )
        for row in rows_out:
            writer.writerow(
                [
                    row.flow,
                    row.simulation_name,
                    row.graphql_operation,
                    row.ui_navigation,
                    row.expected_routing_key,
                    row.enriched_snapshot,
                    row.ui_verify,
                    row.curl,
                ]
            )
    log.info("Wrote working sheet %s (%d rows)", path, len(rows_out))
    return len(rows_out)
