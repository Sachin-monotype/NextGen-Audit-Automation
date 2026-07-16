"""Layer 1 — base enriched envelope validation (all events)."""

from __future__ import annotations

from typing import Any

from ..models import JsonDict, ValidationResult, ValidationStatus

REQUIRED_STRINGS = (
    "xCorrelationId",
    "eventId",
    "occurredAt",
    "enrichedEventId",
    "enrichedAt",
)

REQUIRED_NUMBERS = ("eventVersion", "enrichmentVersion")


def _require_type(
    result: ValidationResult,
    obj: JsonDict,
    path: str,
    expected_type: type,
) -> Any | None:
    parts = path.split(".")
    cur: Any = obj
    for part in parts:
        if not isinstance(cur, dict) or part not in cur:
            result.add(
                "layer1-envelope",
                "required_field",
                ValidationStatus.FAIL,
                f"Missing required field `{path}`",
                path,
            )
            return None
        cur = cur[part]
    if not isinstance(cur, expected_type):
        result.add(
            "layer1-envelope",
            "field_type",
            ValidationStatus.FAIL,
            f"`{path}` expected {expected_type.__name__}, got {type(cur).__name__}",
            path,
        )
        return None
    return cur


def validate_base_envelope(enriched: JsonDict, result: ValidationResult) -> None:
    for field in REQUIRED_STRINGS:
        val = enriched.get(field)
        if not isinstance(val, str) or not val.strip():
            result.add(
                "layer1-envelope",
                "required_string",
                ValidationStatus.FAIL,
                f"Missing or empty string field `{field}`",
                field,
            )

    for field in REQUIRED_NUMBERS:
        val = enriched.get(field)
        if not isinstance(val, int):
            result.add(
                "layer1-envelope",
                "required_number",
                ValidationStatus.FAIL,
                f"Missing or invalid numeric field `{field}`",
                field,
            )

    source = enriched.get("source")
    if not isinstance(source, dict):
        result.add(
            "layer1-envelope",
            "source",
            ValidationStatus.FAIL,
            "`source` must be an object",
            "source",
        )
    else:
        for key in ("operation", "service", "operationState"):
            if not isinstance(source.get(key), str):
                result.add(
                    "layer1-envelope",
                    "source_field",
                    ValidationStatus.FAIL,
                    f"`source.{key}` must be a non-empty string",
                    f"source.{key}",
                )

    actor = enriched.get("actor")
    if not isinstance(actor, dict):
        result.add(
            "layer1-envelope",
            "actor",
            ValidationStatus.FAIL,
            "`actor` must be an object",
            "actor",
        )
    else:
        for key in ("globalUserId", "globalCustomerId"):
            if not isinstance(actor.get(key), str):
                result.add(
                    "layer1-envelope",
                    "actor_field",
                    ValidationStatus.FAIL,
                    f"`actor.{key}` must be a string",
                    f"actor.{key}",
                )

    subject = enriched.get("subject")
    if not isinstance(subject, dict):
        result.add(
            "layer1-envelope",
            "subject",
            ValidationStatus.FAIL,
            "`subject` must be an object",
            "subject",
        )
    else:
        if not isinstance(subject.get("type"), str):
            result.add(
                "layer1-envelope",
                "subject_type",
                ValidationStatus.FAIL,
                "`subject.type` must be a string",
                "subject.type",
            )
        subject_id = subject.get("id")
        if not isinstance(subject_id, list):
            result.add(
                "layer1-envelope",
                "subject_id",
                ValidationStatus.FAIL,
                "`subject.id` must be an array",
                "subject.id",
            )

    _ = _require_type  # reserved for nested checks
