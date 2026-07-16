"""Main validation orchestrator — 3 layers + raw vs enriched compare."""

from __future__ import annotations

import json
from pathlib import Path

from .compare.raw_enriched import compare_raw_enriched
from .layers.domain import validate_domain_template
from .layers.envelope import validate_base_envelope
from .layers.outcome import validate_outcome_branch
from .models import JsonDict, ValidationResult, ValidationStatus
from .template_registry import get_template, parse_filename


def load_json(path: Path) -> JsonDict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def validate_event_pair(
    operation: str,
    service: str,
    enriched: JsonDict,
    raw: JsonDict | None = None,
    *,
    structure_only: bool = False,
    enriched_path: str | None = None,
    raw_path: str | None = None,
) -> ValidationResult:
    from .cron.payloads import is_scheduler_passthrough, validate_cron_event_pair

    if raw and is_scheduler_passthrough(raw, enriched):
        return validate_cron_event_pair(operation, service, enriched, raw)

    template = get_template(operation)
    template_id = template.id if template else "unknown"

    result = ValidationResult(
        operation=operation,
        service=service,
        template_id=template_id,
        status=ValidationStatus.PASS,
        enriched_path=enriched_path,
        raw_path=raw_path,
    )

    # Layer 1 — base envelope
    validate_base_envelope(enriched, result)

    # Layer 2 — outcome branch
    validate_outcome_branch(enriched, result)

    # Layer 3 — domain template
    if template:
        validate_domain_template(enriched, template, result)
    else:
        result.add(
            "layer3-domain",
            "template",
            ValidationStatus.WARN,
            f"No template registered for operation `{operation}`",
        )

    # Raw vs enriched
    if raw is not None and not structure_only:
        compare_raw_enriched(raw, enriched, result)
    elif raw is None and not structure_only:
        result.add(
            "raw-vs-enriched",
            "missing_raw",
            ValidationStatus.WARN,
            "No raw event file — skipped raw vs enriched comparison",
        )

    return result


def discover_event_files(directory: Path) -> dict[str, Path]:
    """Map `{operation}-{service}` stem → file path."""
    files: dict[str, Path] = {}
    if not directory.is_dir():
        return files
    for path in directory.glob("*.json"):
        parsed = parse_filename(path.name)
        if parsed:
            operation, service = parsed
            key = f"{operation}-{service}"
            existing = files.get(key)
            if existing is None or path.stat().st_mtime > existing.stat().st_mtime:
                files[key] = path
    return files


def validate_all(
    enriched_dir: Path,
    raw_dir: Path | None = None,
    *,
    structure_only: bool = False,
) -> list[ValidationResult]:
    enriched_files = discover_event_files(enriched_dir)
    raw_files = discover_event_files(raw_dir) if raw_dir else {}

    results: list[ValidationResult] = []
    for stem, enriched_path in enriched_files.items():
        parsed = parse_filename(enriched_path.name)
        if not parsed:
            continue
        operation, service = parsed

        enriched = load_json(enriched_path)
        raw_path = raw_files.get(stem)
        raw = load_json(raw_path) if raw_path else None

        results.append(
            validate_event_pair(
                operation,
                service,
                enriched,
                raw,
                structure_only=structure_only or raw is None,
                enriched_path=str(enriched_path),
                raw_path=str(raw_path) if raw_path else None,
            )
        )

    # Report enriched files without pairs when raw_dir provided
    if raw_dir:
        for stem, raw_path in raw_files.items():
            if stem not in enriched_files:
                parsed = parse_filename(raw_path.name)
                if not parsed:
                    continue
                operation, service = parsed
                r = ValidationResult(
                    operation=operation,
                    service=service,
                    template_id="n/a",
                    status=ValidationStatus.SKIP,
                    raw_path=str(raw_path),
                )
                r.add(
                    "raw-vs-enriched",
                    "missing_enriched",
                    ValidationStatus.SKIP,
                    "Raw event has no matching enriched file",
                )
                results.append(r)

    return results
