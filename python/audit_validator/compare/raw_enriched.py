"""Raw vs enriched field preservation and enrichment delta checks."""

from __future__ import annotations

from typing import Any

from ..models import JsonDict, ValidationResult, ValidationStatus

ENRICHED_ONLY_ROOT_KEYS = frozenset(
    {"enrichedEventId", "enrichmentVersion", "enrichedAt"}
)

PRESERVED_TOP_LEVEL = frozenset(
    {
        "xCorrelationId",
        "eventId",
        "eventVersion",
        "occurredAt",
        "source",
        "actor",
        "subject",
        "error",
    }
)


def strip_enrichment(enriched: JsonDict) -> JsonDict:
    """Simulate expected raw payload by removing enrichment-only fields."""
    import copy

    raw = copy.deepcopy(enriched)
    for key in ENRICHED_ONLY_ROOT_KEYS:
        raw.pop(key, None)

    actor = raw.get("actor")
    if isinstance(actor, dict):
        actor.pop("enrichedSnapshot", None)

    subject = raw.get("subject")
    if isinstance(subject, dict):
        subject.pop("enrichedSnapshot", None)

    return raw


def compare_raw_enriched(
    raw: JsonDict,
    enriched: JsonDict,
    result: ValidationResult,
    *,
    skip_enrichment_fields: bool = False,
) -> None:
    _compare_preserved_top_level(raw, enriched, result)
    _compare_actor(raw.get("actor"), enriched.get("actor"), result)
    _compare_subject(raw.get("subject"), enriched.get("subject"), result)
    if not skip_enrichment_fields:
        _assert_enrichment_additions(enriched, result)

    if result.status == ValidationStatus.PASS:
        result.add(
            "raw-vs-enriched",
            "preservation",
            ValidationStatus.PASS,
            "Raw fields preserved in enriched event",
        )


def _compare_preserved_top_level(
    raw: JsonDict,
    enriched: JsonDict,
    result: ValidationResult,
) -> None:
    for key in PRESERVED_TOP_LEVEL:
        if key not in raw:
            continue
        if key not in enriched:
            result.add(
                "raw-vs-enriched",
                "missing_preserved",
                ValidationStatus.FAIL,
                f"Enriched missing preserved top-level field `{key}`",
                key,
            )
            continue
        if key in ("source", "subject", "actor", "error"):
            continue
        if raw[key] != enriched[key]:
            result.add(
                "raw-vs-enriched",
                "top_level_mismatch",
                ValidationStatus.FAIL,
                f"Mismatch on `{key}`: raw={raw[key]!r} enriched={enriched[key]!r}",
                key,
            )

    source_raw = raw.get("source")
    source_enriched = enriched.get("source")
    scheduler = str(enriched.get("eventSource") or "") == "scheduler"
    if isinstance(source_raw, dict) and isinstance(source_enriched, dict):
        for sk in ("operation", "service", "operationState", "operationIndex"):
            if scheduler and sk == "operation" and source_raw.get("trigger") and not source_raw.get("operation"):
                continue
            if scheduler and sk in {"operation", "operationState", "operationIndex"}:
                continue
            if sk in source_raw and source_raw.get(sk) != source_enriched.get(sk):
                result.add(
                    "raw-vs-enriched",
                    "source_mismatch",
                    ValidationStatus.FAIL,
                    f"source.{sk} mismatch",
                    f"source.{sk}",
                )


def _compare_actor(raw_actor: Any, enriched_actor: Any, result: ValidationResult) -> None:
    if not isinstance(raw_actor, dict) or not isinstance(enriched_actor, dict):
        return
    for key in ("globalUserId", "globalCustomerId", "customerOrgId"):
        if key in raw_actor and raw_actor.get(key) != enriched_actor.get(key):
            result.add(
                "raw-vs-enriched",
                "actor_mismatch",
                ValidationStatus.FAIL,
                f"actor.{key} mismatch",
                f"actor.{key}",
            )
    _assert_subset(raw_actor, enriched_actor, "actor", result, skip_keys={"enrichedSnapshot"})


def _compare_subject(raw_subject: Any, enriched_subject: Any, result: ValidationResult) -> None:
    if not isinstance(raw_subject, dict) or not isinstance(enriched_subject, dict):
        return

    for key in ("type", "name"):
        if key in raw_subject and raw_subject.get(key) != enriched_subject.get(key):
            result.add(
                "raw-vs-enriched",
                "subject_mismatch",
                ValidationStatus.FAIL,
                f"subject.{key} mismatch",
                f"subject.{key}",
            )

    raw_ids = raw_subject.get("id")
    enriched_ids = enriched_subject.get("id")
    if raw_ids is not None and raw_ids != enriched_ids:
        result.add(
            "raw-vs-enriched",
            "subject_id_mismatch",
            ValidationStatus.FAIL,
            f"subject.id mismatch: raw={raw_ids} enriched={enriched_ids}",
            "subject.id",
        )

    _assert_subset(
        raw_subject,
        enriched_subject,
        "subject",
        result,
        skip_keys={"enrichedSnapshot"},
    )


def _assert_subset(
    raw_obj: JsonDict,
    enriched_obj: JsonDict,
    prefix: str,
    result: ValidationResult,
    *,
    skip_keys: frozenset[str] | None = None,
) -> None:
    skip = skip_keys or frozenset()
    for key, raw_val in raw_obj.items():
        if key in skip:
            continue
        path = f"{prefix}.{key}"
        if key not in enriched_obj:
            result.add(
                "raw-vs-enriched",
                "missing_field",
                ValidationStatus.FAIL,
                f"Enriched missing raw field `{path}`",
                path,
            )
            continue
        enriched_val = enriched_obj[key]
        _assert_value_equal(raw_val, enriched_val, path, result)


def _assert_value_equal(raw_val: Any, enriched_val: Any, path: str, result: ValidationResult) -> None:
    if isinstance(raw_val, dict) and isinstance(enriched_val, dict):
        _assert_subset(raw_val, enriched_val, path, result)
        return
    if isinstance(raw_val, list) and isinstance(enriched_val, list):
        if len(raw_val) != len(enriched_val):
            result.add(
                "raw-vs-enriched",
                "array_length",
                ValidationStatus.FAIL,
                f"Array length mismatch at `{path}`",
                path,
            )
            return
        for i, (rv, ev) in enumerate(zip(raw_val, enriched_val)):
            _assert_value_equal(rv, ev, f"{path}[{i}]", result)
        return
    if raw_val != enriched_val:
        result.add(
            "raw-vs-enriched",
            "value_mismatch",
            ValidationStatus.FAIL,
            f"Value mismatch at `{path}`: raw={raw_val!r} enriched={enriched_val!r}",
            path,
        )


def _assert_enrichment_additions(enriched: JsonDict, result: ValidationResult) -> None:
    from ..layers.outcome import is_failure_event

    for key in ENRICHED_ONLY_ROOT_KEYS:
        if key not in enriched:
            result.add(
                "raw-vs-enriched",
                "enrichment_field",
                ValidationStatus.FAIL,
                f"Enriched event missing `{key}`",
                key,
            )

    if is_failure_event(enriched):
        return

    actor = enriched.get("actor")
    if isinstance(actor, dict) and "enrichedSnapshot" not in actor:
        result.add(
            "raw-vs-enriched",
            "actor_enrichment_added",
            ValidationStatus.FAIL,
            "Success enriched event must add actor.enrichedSnapshot",
            "actor.enrichedSnapshot",
        )
    subject = enriched.get("subject")
    if isinstance(subject, dict) and "enrichedSnapshot" not in subject:
        result.add(
            "raw-vs-enriched",
            "subject_enrichment_added",
            ValidationStatus.FAIL,
            "Success enriched event must add subject.enrichedSnapshot",
            "subject.enrichedSnapshot",
        )
