"""End-to-end coverage matrix — nothing missed across pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..models import JsonDict, ValidationResult, ValidationStatus
from ..operation_catalog import expects_enriched_event
from ..rabbitmq.collector import QueueEventCollector
from ..rabbitmq.resolver_routing_map import expected_routing_key
from ..operation_registry import e2e_expected_operations
from ..template_registry import OPERATION_TEMPLATE_MAP
from .correlation_selection import (
    best_correlation_per_operation,
    best_validation_per_operation,
    operation_from_raw,
    reconcile_correlation_pairs,
)


class PipelineStage(str, Enum):
    EXPECTED = "expected"
    RAW_QUEUE = "raw_queue"
    ENRICHED_QUEUE = "enriched_queue"
    DEAD_LETTER = "dead_letter"
    STRUCTURE_VALID = "structure_valid"
    RAW_ENRICHED_MATCH = "raw_enriched_match"
    UI_DISPLAYED = "ui_displayed"


@dataclass
class OperationCoverageRow:
    operation: str
    template_id: str
    stages: dict[PipelineStage, bool] = field(default_factory=dict)
    x_correlation_id: str | None = None
    enriched_routing_key: str | None = None
    expected_routing_key: str | None = None
    enrichment_expected: bool = True
    validation_status: str | None = None
    gaps: list[str] = field(default_factory=list)

    def is_complete(self, *, require_ui: bool = False) -> bool:
        required = [
            PipelineStage.RAW_QUEUE,
            PipelineStage.STRUCTURE_VALID,
            PipelineStage.RAW_ENRICHED_MATCH,
        ]
        if self.enrichment_expected:
            required.insert(1, PipelineStage.ENRICHED_QUEUE)
        if require_ui:
            required.append(PipelineStage.UI_DISPLAYED)
        return all(self.stages.get(s) for s in required)


@dataclass
class RoutingKeyCoverageRow:
    routing_key: str
    queue_received: bool = False
    ui_displayed: bool = False
    payload_has_correlation: bool = False
    mapped_operations: list[str] = field(default_factory=list)


@dataclass
class CoverageMatrix:
    operations: list[OperationCoverageRow]
    routing_keys: list[RoutingKeyCoverageRow]
    correlation_ids_raw: dict[str, str]
    correlation_ids_enriched: dict[str, str]

    @property
    def operations_complete(self) -> int:
        return sum(1 for r in self.operations if r.is_complete())

    @property
    def operations_with_gaps(self) -> list[OperationCoverageRow]:
        return [r for r in self.operations if r.gaps]

    @property
    def routing_keys_received(self) -> int:
        return sum(1 for r in self.routing_keys if r.queue_received)

    @property
    def routing_keys_missing(self) -> list[str]:
        return [r.routing_key for r in self.routing_keys if not r.queue_received]


def _correlation_index(payloads: dict[str, JsonDict]) -> dict[str, str]:
    index: dict[str, str] = {}
    for key, payload in payloads.items():
        cid = payload.get("xCorrelationId")
        if isinstance(cid, str) and cid:
            op = key.split("-mtconnect-api")[0] if "-mtconnect-api" in key else key.rsplit("-", 1)[0]
            index[cid] = op
    return index


def _operation_from_raw(raw_payload: JsonDict) -> str:
    return operation_from_raw(raw_payload)


def build_coverage_matrix(
    collector: QueueEventCollector,
    validation_results: list[ValidationResult],
    *,
    captured_only: bool = False,
) -> CoverageMatrix:
    """Build coverage matrix from correlation-indexed queue captures."""

    with collector._lock:  # noqa: SLF001
        raw_by_cid = dict(collector._raw_by_correlation)
        enriched_by_cid = dict(collector._enriched_by_correlation)
        rk_snap = dict(collector._enriched_by_routing_key)
        rk_for_cid = dict(collector._enriched_routing_key_for_correlation)
        dl_by_cid = dict(collector._dead_letter_by_correlation)

    val_by_op = best_validation_per_operation(validation_results)

    reconciled = reconcile_correlation_pairs(raw_by_cid, enriched_by_cid)
    op_to_cid = best_correlation_per_operation(
        raw_by_cid, enriched_by_cid, dl_by_cid=dl_by_cid
    )

    operations: list[OperationCoverageRow] = []
    if captured_only:
        captured_ops: set[str] = set()
        for raw_payload in raw_by_cid.values():
            captured_ops.add(_operation_from_raw(raw_payload))
        for enriched_payload in enriched_by_cid.values():
            source = enriched_payload.get("source") or {}
            captured_ops.add(str(source.get("operation") or "unknown"))
        expected_ops = sorted(op for op in captured_ops if op != "unknown")
    else:
        expected_ops = sorted(e2e_expected_operations())

    for op in expected_ops:
        template_id = OPERATION_TEMPLATE_MAP.get(op, "unknown")
        enrichment_expected = expects_enriched_event(op)
        row = OperationCoverageRow(
            operation=op,
            template_id=template_id,
            stages={PipelineStage.EXPECTED: True},
            enrichment_expected=enrichment_expected,
            expected_routing_key=expected_routing_key(op),
        )

        cid = op_to_cid.get(op)
        raw_payload, enriched_payload = reconciled.get(cid, (None, None)) if cid else (None, None)
        dl_payload = dl_by_cid.get(cid) if cid else None
        val = val_by_op.get(op)

        row.stages[PipelineStage.RAW_QUEUE] = raw_payload is not None
        row.stages[PipelineStage.ENRICHED_QUEUE] = enriched_payload is not None
        row.stages[PipelineStage.DEAD_LETTER] = dl_payload is not None

        if cid:
            row.x_correlation_id = cid
        enr_cid = (
            str(enriched_payload.get("xCorrelationId"))
            if enriched_payload and enriched_payload.get("xCorrelationId")
            else cid
        )
        row.enriched_routing_key = rk_for_cid.get(enr_cid) if enr_cid else None

        if val:
            row.validation_status = val.status.value
            structure_ok = not any(
                c.status == ValidationStatus.FAIL
                for c in val.checks
                if c.layer.startswith("layer")
            )
            raw_match_ok = not any(
                c.status == ValidationStatus.FAIL
                for c in val.checks
                if c.layer == "raw-vs-enriched"
            )
            row.stages[PipelineStage.STRUCTURE_VALID] = structure_ok and val.status in (
                ValidationStatus.PASS,
                ValidationStatus.WARN,
            )
            row.stages[PipelineStage.RAW_ENRICHED_MATCH] = raw_match_ok and val.status == ValidationStatus.PASS
        else:
            row.stages[PipelineStage.STRUCTURE_VALID] = False
            row.stages[PipelineStage.RAW_ENRICHED_MATCH] = False

        row.stages[PipelineStage.UI_DISPLAYED] = False

        if not row.stages[PipelineStage.RAW_QUEUE]:
            if not captured_only:
                row.gaps.append("GQL op did not produce raw event on queue")
        elif not enrichment_expected:
            if not row.stages[PipelineStage.STRUCTURE_VALID]:
                row.stages[PipelineStage.STRUCTURE_VALID] = True
                row.stages[PipelineStage.RAW_ENRICHED_MATCH] = True
        elif row.stages[PipelineStage.DEAD_LETTER] and not row.stages[
            PipelineStage.ENRICHED_QUEUE
        ]:
            row.gaps.append("Event dead-lettered — enrichment failed")
        elif not row.stages[PipelineStage.ENRICHED_QUEUE]:
            row.gaps.append("Raw received but no enriched event (timeout)")
        elif not row.stages[PipelineStage.STRUCTURE_VALID]:
            row.gaps.append("Enriched structure validation failed")
        elif not row.stages[PipelineStage.RAW_ENRICHED_MATCH]:
            row.gaps.append("Raw vs enriched field mismatch")

        operations.append(row)

    routing_keys: list[RoutingKeyCoverageRow] = []
    if not captured_only:
        from ..rabbitmq.resolver_routing_map import operations_for_routing_key

        for rk in sorted(collector._rmq.enriched_routing_keys):  # noqa: SLF001
            payload = rk_snap.get(rk)
            routing_keys.append(
                RoutingKeyCoverageRow(
                    routing_key=rk,
                    queue_received=payload is not None,
                    ui_displayed=False,
                    payload_has_correlation=bool(
                        isinstance(payload, dict) and payload.get("xCorrelationId")
                    ),
                    mapped_operations=operations_for_routing_key(rk),
                )
            )

    return CoverageMatrix(
        operations=operations,
        routing_keys=routing_keys,
        correlation_ids_raw=_correlation_index(
            {f"{_operation_from_raw(v)}-mtconnect-api": v for v in raw_by_cid.values()}
        ),
        correlation_ids_enriched=_correlation_index(
            {
                f"{_operation_from_raw(v)}-mtconnect-api": v
                for v in enriched_by_cid.values()
            }
        ),
    )


def find_orphan_correlations(matrix: CoverageMatrix) -> list[str]:
    """Correlation IDs in raw but not in enriched (broken chain)."""
    raw_ids = set(matrix.correlation_ids_raw)
    enriched_ids = set(matrix.correlation_ids_enriched)
    return sorted(raw_ids - enriched_ids)
