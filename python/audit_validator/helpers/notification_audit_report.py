"""Build Notification and audit.csv — full operation catalog with UI, cURL, queue JSON."""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

from ..csv_report import (
    _enriched_status,
    _load_flows_simulation,
    _raw_status,
    resolve_e2e_outcome,
)
from ..coverage.matrix import CoverageMatrix, OperationCoverageRow, PipelineStage
from ..utility.operation_meta import build_curl_resolved, load_curl_context, ui_navigation
from ..rabbitmq.resolver_routing_map import expected_routing_key
from ..operation_registry import tracked_operations
from ..template_registry import OPERATION_TEMPLATE_MAP

log = logging.getLogger(__name__)

EXCEL_CELL_MAX = 32700


def _json_cell(payload: dict | None) -> str:
    if not payload:
        return ""
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if len(text) > EXCEL_CELL_MAX:
        return text[: EXCEL_CELL_MAX - 20] + "\n... [truncated]"
    return text


def _load_coverage(project_root: Path) -> dict[str, OperationCoverageRow]:
    path = project_root / "reports" / "e2e" / "coverage-matrix.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, OperationCoverageRow] = {}
    for row in data.get("operations", []):
        stages = {PipelineStage(k): v for k, v in row["stages"].items()}
        out[row["operation"]] = OperationCoverageRow(
            operation=row["operation"],
            template_id=row["template_id"],
            stages=stages,
            x_correlation_id=row.get("x_correlation_id"),
            enriched_routing_key=row.get("enriched_routing_key"),
            expected_routing_key=row.get("expected_routing_key"),
            enrichment_expected=row.get("enrichment_expected", True),
            validation_status=row.get("validation_status"),
            gaps=row.get("gaps", []),
        )
    return out


def _index_enriched_by_correlation(enriched_dir: Path) -> dict[str, dict]:
    index: dict[str, dict] = {}
    if not enriched_dir.is_dir():
        return index
    for path in enriched_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        cid = payload.get("xCorrelationId")
        if isinstance(cid, str) and cid:
            index[cid] = payload
    return index


def _index_dl_by_correlation(dl_dir: Path) -> dict[str, dict]:
    return _index_enriched_by_correlation(dl_dir)


def _load_raw_payload(
    raw_dir: Path,
    operation: str,
    correlation_id: str | None,
) -> dict | None:
    if not raw_dir.is_dir():
        return None

    if correlation_id:
        short = correlation_id[:8]
        exact = raw_dir / f"{operation}-mtconnect-api-{short}.json"
        if exact.is_file():
            return json.loads(exact.read_text(encoding="utf-8"))

    matches = sorted(
        raw_dir.glob(f"{operation}-mtconnect-api*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in matches:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return None


def _load_enriched_payload(
    *,
    enriched_dir: Path,
    dl_dir: Path,
    enriched_index: dict[str, dict],
    dl_index: dict[str, dict],
    operation: str,
    correlation_id: str | None,
    routing_key: str | None,
) -> dict | None:
    if correlation_id:
        if correlation_id in enriched_index:
            return enriched_index[correlation_id]
        if correlation_id in dl_index:
            return dl_index[correlation_id]

    if enriched_dir.is_dir():
        op_path = enriched_dir / f"{operation}-mtconnect-api.json"
        if op_path.is_file():
            try:
                return json.loads(op_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        if routing_key:
            rk_path = enriched_dir / f"{routing_key}.json"
            if rk_path.is_file():
                try:
                    payload = json.loads(rk_path.read_text(encoding="utf-8"))
                    if not correlation_id or payload.get("xCorrelationId") == correlation_id:
                        return payload
                except Exception:
                    pass

    return None


def write_notification_audit_csv(*, path: Path, project_root: Path) -> None:
    """Populate reports/Notification and audit.csv for all template operations."""
    ctx = load_curl_context(project_root)
    cov_by_op = _load_coverage(project_root)
    flows_path = project_root / "reports" / "e2e" / "flows-results.json"
    if not flows_path.is_file():
        flows_path = project_root / "reports" / "gql" / "flows-results.json"
    sim, sim_errors = _load_flows_simulation(flows_path) if flows_path.is_file() else ({}, {})

    raw_dir = project_root / "payload" / "raw"
    enriched_dir = project_root / "payload" / "enrich"
    dl_dir = project_root / "dl_events"
    enriched_index = _index_enriched_by_correlation(enriched_dir)
    dl_index = _index_dl_by_correlation(dl_dir)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "operation",
                "UI_Navigation",
                "cURL",
                "raw_json",
                "enriched_json",
                "x_correlation_id",
                "raw_queue",
                "enriched_queue",
                "expected_routing_key",
                "simulation",
                "overall",
            ]
        )

        for op in tracked_operations():
            cov = cov_by_op.get(op)
            stages = cov.stages if cov else {}
            cid = cov.x_correlation_id if cov else None

            raw_payload = _load_raw_payload(raw_dir, op, cid)
            if raw_payload and not cid:
                cid = raw_payload.get("xCorrelationId")

            rk = expected_routing_key(op) or (cov.expected_routing_key if cov else "")
            enriched_payload = _load_enriched_payload(
                enriched_dir=enriched_dir,
                dl_dir=dl_dir,
                enriched_index=enriched_index,
                dl_index=dl_index,
                operation=op,
                correlation_id=str(cid) if cid else None,
                routing_key=rk,
            )

            ui = ui_navigation(op)
            curl = build_curl_resolved(op, project_root, ctx)
            simulation = sim.get(op, "NOT_RUN")
            raw_q = _raw_status(stages) if cov else ("YES" if raw_payload else "NO")
            enriched_q = _enriched_status(stages, op) if cov else (
                "YES" if enriched_payload else "NO"
            )
            if cov and stages.get(PipelineStage.DEAD_LETTER) and enriched_q != "YES":
                enriched_q = "DEAD_LETTER"
            overall = resolve_e2e_outcome(
                op,
                simulation=simulation,
                sim_error=sim_errors.get(op, ""),
                raw=raw_q,
                enriched=enriched_q,
                val=None,
            ).status

            writer.writerow(
                [
                    op,
                    ui,
                    curl,
                    _json_cell(raw_payload),
                    _json_cell(enriched_payload),
                    cid or "",
                    raw_q,
                    enriched_q,
                    rk,
                    simulation,
                    overall,
                ]
            )

    log.info("Wrote notification audit report to %s", path)
