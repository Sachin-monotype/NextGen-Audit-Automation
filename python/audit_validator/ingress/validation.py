"""Validation rules for Ingress API (desktop / plugin) audit events."""

from __future__ import annotations

from ..compare.raw_enriched import compare_raw_enriched
from ..models import JsonDict, ValidationResult, ValidationStatus


# Desktop plugin / app settings events — enrichment optional in PP preprod today.
_INGRESS_ENRICHMENT_OPTIONAL: frozenset[str] = frozenset({
    "pluginPanelOpened",
    "pluginPanelClosed",
    "pluginMissingFontUnresolved",
    "pluginMissingFontResolved",
    "pluginMissingFontDetected",
    "pluginImportedFontRequested",
    "pluginFontManuallyActivated",
    "pluginFontAutoActivated",
    "pluginFontConflictDetected",
    "pluginDocumentOpened",
})


def expects_ingress_enrichment(operation: str) -> bool:
    return operation not in _INGRESS_ENRICHMENT_OPTIONAL


def validate_ingress_event_pair(
    operation: str,
    service: str,
    enriched: JsonDict,
    raw: JsonDict | None = None,
) -> ValidationResult:
    result = ValidationResult(
        operation=operation,
        service=service,
        template_id="ingress-api",
        status=ValidationStatus.PASS,
    )
    if raw is None:
        result.status = ValidationStatus.FAIL
        result.add("ingress", "raw", ValidationStatus.FAIL, "Missing raw event", "raw")
        return result

    raw_cid = str(raw.get("xCorrelationId") or "")
    enr_cid = str(enriched.get("xCorrelationId") or "")
    if raw_cid and enr_cid and raw_cid != enr_cid:
        result.add(
            "ingress",
            "xCorrelationId",
            ValidationStatus.FAIL,
            f"Correlation mismatch raw={raw_cid} enriched={enr_cid}",
            "xCorrelationId",
        )

    compare_raw_enriched(raw, enriched, result, skip_enrichment_fields=True)
    if any(c.status == ValidationStatus.FAIL for c in result.checks):
        result.status = ValidationStatus.FAIL
    return result
