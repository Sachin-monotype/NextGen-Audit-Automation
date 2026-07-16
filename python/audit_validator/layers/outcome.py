"""Layer 2 — success vs failure outcome branch validation."""

from __future__ import annotations

from ..models import JsonDict, ValidationResult, ValidationStatus


def is_failure_event(enriched: JsonDict) -> bool:
    source = enriched.get("source") or {}
    return (
        "error" in enriched
        or source.get("operationState") == "failure"
    )


def validate_outcome_branch(enriched: JsonDict, result: ValidationResult) -> str:
    """Returns branch name: successEnriched | failureMinimal."""
    if is_failure_event(enriched):
        return _validate_failure(enriched, result)
    return _validate_success(enriched, result)


def _validate_success(enriched: JsonDict, result: ValidationResult) -> str:
    actor = enriched.get("actor")
    if not isinstance(actor, dict):
        return "successEnriched"

    snap = actor.get("enrichedSnapshot")
    if not isinstance(snap, dict):
        result.add(
            "layer2-outcome",
            "actor_enrichment",
            ValidationStatus.FAIL,
            "Success event must include `actor.enrichedSnapshot`",
            "actor.enrichedSnapshot",
        )
    else:
        user = snap.get("user")
        customer = snap.get("customer")
        if not isinstance(user, dict):
            result.add(
                "layer2-outcome",
                "actor_user",
                ValidationStatus.FAIL,
                "`actor.enrichedSnapshot.user` required on success",
                "actor.enrichedSnapshot.user",
            )
        if not isinstance(customer, dict):
            result.add(
                "layer2-outcome",
                "actor_customer",
                ValidationStatus.FAIL,
                "`actor.enrichedSnapshot.customer` required on success",
                "actor.enrichedSnapshot.customer",
            )

    subject = enriched.get("subject")
    if isinstance(subject, dict):
        if not isinstance(subject.get("enrichedSnapshot"), dict):
            result.add(
                "layer2-outcome",
                "subject_enrichment",
                ValidationStatus.WARN,
                "Success event has no `subject.enrichedSnapshot` (optional for read/query ops)",
                "subject.enrichedSnapshot",
            )

    if "error" in enriched:
        result.add(
            "layer2-outcome",
            "unexpected_error",
            ValidationStatus.FAIL,
            "Success event must not contain top-level `error`",
            "error",
        )

    if result.status == ValidationStatus.PASS:
        result.add(
            "layer2-outcome",
            "branch",
            ValidationStatus.PASS,
            "Matched successEnriched branch",
        )
    return "successEnriched"


def _validate_failure(enriched: JsonDict, result: ValidationResult) -> str:
    error = enriched.get("error")
    if not isinstance(error, dict):
        result.add(
            "layer2-outcome",
            "error_block",
            ValidationStatus.FAIL,
            "Failure event must include top-level `error` object",
            "error",
        )
    else:
        for key in ("code", "classification", "message"):
            if not isinstance(error.get(key), str):
                result.add(
                    "layer2-outcome",
                    "error_field",
                    ValidationStatus.FAIL,
                    f"`error.{key}` must be a string",
                    f"error.{key}",
                )

    actor = enriched.get("actor")
    if isinstance(actor, dict) and "enrichedSnapshot" in actor:
        result.add(
            "layer2-outcome",
            "no_actor_enrichment",
            ValidationStatus.WARN,
            "Failure event usually has minimal actor without enrichedSnapshot",
            "actor.enrichedSnapshot",
        )

    subject = enriched.get("subject")
    if isinstance(subject, dict) and "enrichedSnapshot" in subject:
        result.add(
            "layer2-outcome",
            "no_subject_enrichment",
            ValidationStatus.WARN,
            "Failure event usually has no subject.enrichedSnapshot",
            "subject.enrichedSnapshot",
        )

    if result.status == ValidationStatus.PASS:
        result.add(
            "layer2-outcome",
            "branch",
            ValidationStatus.PASS,
            "Matched failureMinimal branch",
        )
    return "failureMinimal"
