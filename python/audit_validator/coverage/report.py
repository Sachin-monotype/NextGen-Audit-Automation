"""Coverage report formatting."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .matrix import (
    CoverageMatrix,
    OperationCoverageRow,
    PipelineStage,
    RoutingKeyCoverageRow,
    find_orphan_correlations,
)


def print_coverage_report(
    matrix: CoverageMatrix,
    *,
    skip_routing_key_coverage: bool = False,
    validate_captured_only: bool = False,
) -> None:
    orphans = find_orphan_correlations(matrix)
    total_ops = len(matrix.operations)
    raw_ok = sum(1 for r in matrix.operations if r.stages.get(PipelineStage.RAW_QUEUE))
    enriched_ok = sum(1 for r in matrix.operations if r.stages.get(PipelineStage.ENRICHED_QUEUE))
    structure_ok = sum(1 for r in matrix.operations if r.stages.get(PipelineStage.STRUCTURE_VALID))
    match_ok = sum(1 for r in matrix.operations if r.stages.get(PipelineStage.RAW_ENRICHED_MATCH))
    complete = matrix.operations_complete

    print("\n" + "=" * 72)
    title = (
        "  CAPTURED EVENT COVERAGE (raw ↔ enriched)"
        if validate_captured_only
        else "  FULL PIPELINE COVERAGE (audit log operations)"
    )
    print(title)
    print("=" * 72)
    if validate_captured_only:
        print(f"  Correlation pairs captured  : {raw_ok} raw / {enriched_ok} enriched")
    else:
        print(f"  Expected operations     : {total_ops}")
    print(f"  Raw on queue            : {raw_ok}" + (f" / {total_ops}" if not validate_captured_only else ""))
    print(f"  Enriched on queue       : {enriched_ok}" + (f" / {total_ops}" if not validate_captured_only else ""))
    print(f"  Structure valid         : {structure_ok}" + (f" / {total_ops}" if not validate_captured_only else ""))
    print(f"  Raw ↔ enriched match    : {match_ok}" + (f" / {total_ops}" if not validate_captured_only else ""))
    if not validate_captured_only:
        print(f"  Fully complete (no gaps): {complete} / {total_ops}")
    print(f"  Orphan correlation IDs  : {len(orphans)} (raw without enriched)")

    gaps = matrix.operations_with_gaps
    if gaps:
        print(f"\n  GAPS ({len(gaps)} operations):")
        for row in gaps[:30]:
            gap = "; ".join(row.gaps)
            print(f"    ✗ {row.operation}: {gap}")
        if len(gaps) > 30:
            print(f"    … and {len(gaps) - 30} more")

    if skip_routing_key_coverage:
        print("\n" + "=" * 72 + "\n")
        return

    rk_total = len(matrix.routing_keys)
    rk_recv = matrix.routing_keys_received
    rk_miss = matrix.routing_keys_missing

    print("\n" + "=" * 72)
    print("  RESOLVER ROUTING KEY COVERAGE (outbound-routing-map.json)")
    print("=" * 72)
    print(f"  Expected routing keys   : {rk_total}")
    print(f"  Received on queue       : {rk_recv} / {rk_total}")
    print(f"  UI verified             : 0 / {rk_total}  (not implemented — see docs)")
    if rk_miss:
        print(f"\n  Missing routing keys ({len(rk_miss)}):")
        for rk in rk_miss[:20]:
            print(f"    - {rk}")
        if len(rk_miss) > 20:
            print(f"    … and {len(rk_miss) - 20} more")

    print("\n" + "=" * 72)
    print("  UI DISPLAY")
    print("=" * 72)
    print("  Status: NOT AUTOMATED in this project yet.")
    print("  Next: query audit-log / notification read API or browser automation.")
    print("=" * 72 + "\n")


def write_coverage_json(matrix: CoverageMatrix, path: Path) -> None:
    orphans = find_orphan_correlations(matrix)
    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "operations_expected": len(matrix.operations),
            "operations_raw_queue": sum(
                1 for r in matrix.operations if r.stages.get(PipelineStage.RAW_QUEUE)
            ),
            "operations_enriched_queue": sum(
                1 for r in matrix.operations if r.stages.get(PipelineStage.ENRICHED_QUEUE)
            ),
            "operations_structure_valid": sum(
                1 for r in matrix.operations if r.stages.get(PipelineStage.STRUCTURE_VALID)
            ),
            "operations_raw_enriched_match": sum(
                1 for r in matrix.operations if r.stages.get(PipelineStage.RAW_ENRICHED_MATCH)
            ),
            "operations_complete": matrix.operations_complete,
            "orphan_correlation_ids": len(orphans),
            "routing_keys_expected": len(matrix.routing_keys),
            "routing_keys_received": matrix.routing_keys_received,
            "routing_keys_ui_verified": 0,
        },
        "orphan_correlation_ids": orphans,
        "operations": [asdict(r) for r in matrix.operations],
        "routing_keys": [asdict(r) for r in matrix.routing_keys],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_coverage_json(path: Path) -> CoverageMatrix | None:
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    operations: list[OperationCoverageRow] = []

    def _stage_key(key: str) -> PipelineStage | None:
        try:
            return PipelineStage(key)
        except ValueError:
            if key in PipelineStage.__members__:
                return PipelineStage[key]
        return None

    for row in data.get("operations") or []:
        stages_raw = row.get("stages") or {}
        stages = {
            stage: bool(v)
            for k, v in stages_raw.items()
            if (stage := _stage_key(str(k))) is not None
        }
        operations.append(
            OperationCoverageRow(
                operation=str(row.get("operation") or ""),
                template_id=str(row.get("template_id") or ""),
                stages=stages,
                x_correlation_id=row.get("x_correlation_id"),
                enriched_routing_key=row.get("enriched_routing_key"),
                expected_routing_key=row.get("expected_routing_key"),
                enrichment_expected=bool(row.get("enrichment_expected", True)),
                validation_status=row.get("validation_status"),
                gaps=list(row.get("gaps") or []),
            )
        )
    routing_keys: list[RoutingKeyCoverageRow] = []
    for row in data.get("routing_keys") or []:
        routing_keys.append(
            RoutingKeyCoverageRow(
                routing_key=str(row.get("routing_key") or ""),
                queue_received=bool(row.get("queue_received")),
                ui_displayed=bool(row.get("ui_displayed")),
                payload_has_correlation=bool(row.get("payload_has_correlation")),
                mapped_operations=list(row.get("mapped_operations") or []),
            )
        )
    return CoverageMatrix(
        operations=operations,
        routing_keys=routing_keys,
        correlation_ids_raw=dict(data.get("correlation_ids_raw") or {}),
        correlation_ids_enriched=dict(data.get("correlation_ids_enriched") or {}),
    )
