"""Validation-mapping authoring coverage across all tracked audit operations.

Distinct from ``coverage.matrix`` (which measures *runtime* pipeline stages: raw ↔
enriched ↔ structure ↔ match). This module answers an *authoring* question: for
every operation the pipeline knows about, do we have (a) a domain validation
template, (b) a subject/actor field mapping, and (c) a known subject source API?

Operations flagged here need a mapping authored — see
``.cursor/rules/audit-event-mapping.mdc``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..operation_registry import tracked_operations
from ..rabbitmq.resolver_routing_map import RESOLVER_MAPPED_OPERATIONS
from ..simulation.operation_registry import simulated_operations
from ..template_registry import get_template

_EXTERNAL_SOURCES = {"UMS", "CMS", "Typesense", "AMS", "UMS/Search", "Discovery"}


@dataclass
class OperationMappingCoverage:
    operation: str
    has_template: bool = False
    has_event_spec: bool = False
    has_subject_api: bool = False
    has_field_mapping: bool = False
    has_routing_key: bool = False
    simulated: bool = False
    subject_apis: str = ""
    status: str = "unmapped"  # complete | needs_mapping | needs_template | unmapped
    gaps: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "operation": self.operation,
            "has_template": self.has_template,
            "has_event_spec": self.has_event_spec,
            "has_subject_api": self.has_subject_api,
            "has_field_mapping": self.has_field_mapping,
            "has_routing_key": self.has_routing_key,
            "simulated": self.simulated,
            "subject_apis": self.subject_apis,
            "status": self.status,
            "gaps": self.gaps,
        }


def _event_spec_for(operation: str):
    try:
        from ..source_validation.audit_events_registry import (
            DEFAULT_AUDIT_EVENTS_XLSX,
            events_by_operation,
        )

        return events_by_operation(str(DEFAULT_AUDIT_EVENTS_XLSX)).get(operation)
    except Exception:
        return None


def _has_external_mapping(operation: str) -> bool:
    try:
        from ..source_validation.mapping_registry import get_operation_mapping

        for spec in get_operation_mapping(operation):
            if spec.validate == "Y" and spec.source_system in _EXTERNAL_SOURCES:
                return True
    except Exception:
        return False
    return False


def operation_coverage(
    operation: str, *, simulated: set[str] | None = None
) -> OperationMappingCoverage:
    sim = simulated if simulated is not None else set(simulated_operations())
    cov = OperationMappingCoverage(operation=operation)

    template = get_template(operation)
    cov.has_template = template is not None

    spec = _event_spec_for(operation)
    cov.has_event_spec = spec is not None
    if spec is not None:
        cov.subject_apis = str(getattr(spec, "subject_apis", "") or "")
        cov.has_subject_api = bool(cov.subject_apis.strip()) and bool(
            getattr(spec, "enriches_subject", False)
        )
    if not cov.has_subject_api and template is not None:
        cov.has_subject_api = bool(getattr(template, "requires_subject_enrichment", False))

    cov.has_field_mapping = _has_external_mapping(operation)
    cov.has_routing_key = operation in RESOLVER_MAPPED_OPERATIONS
    cov.simulated = operation in sim

    gaps: list[str] = []
    if not cov.has_template:
        gaps.append("no validation template")
    if not cov.has_field_mapping:
        gaps.append("no source field mapping")
    if not cov.has_subject_api and not cov.has_event_spec:
        gaps.append("no subject/enrichment spec")
    if not cov.has_routing_key:
        gaps.append("no outbound routing key")
    cov.gaps = gaps

    if cov.has_template and cov.has_field_mapping:
        cov.status = "complete"
    elif cov.has_template or cov.has_event_spec:
        cov.status = "needs_mapping"
    elif cov.has_routing_key or cov.simulated:
        cov.status = "needs_template"
    else:
        cov.status = "unmapped"
    return cov


def mapping_coverage_report(operations: list[str] | None = None) -> dict[str, object]:
    ops = operations or tracked_operations()
    sim = set(simulated_operations())
    rows = [operation_coverage(op, simulated=sim).as_dict() for op in ops]

    def _count(status: str) -> int:
        return sum(1 for r in rows if r["status"] == status)

    return {
        "total": len(rows),
        "summary": {
            "complete": _count("complete"),
            "needs_mapping": _count("needs_mapping"),
            "needs_template": _count("needs_template"),
            "unmapped": _count("unmapped"),
            "with_template": sum(1 for r in rows if r["has_template"]),
            "with_field_mapping": sum(1 for r in rows if r["has_field_mapping"]),
        },
        "operations": rows,
    }


def write_mapping_coverage_report(
    path: Path, operations: list[str] | None = None
) -> dict[str, object]:
    import json

    report = mapping_coverage_report(operations)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
