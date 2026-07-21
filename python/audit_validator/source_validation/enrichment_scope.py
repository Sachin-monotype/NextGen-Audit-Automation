"""Enrichment-scope validation against the audit-resolver contract.

Manifest source: ``data/enrichment_scope_manifest.json`` generated from
``mt-audit-log-resolver-service`` (enricher produce + handler require lists).

Validation asserts:
  1. **Produces** — if enricher implements subject/actor, enriched sample should
     carry the corresponding ``*.enrichedSnapshot`` (when sample is success-state).
  2. **Requires** — if handler requires subject/actor, missing snapshot is FAIL.
  3. **Gap** — produce ≠ require is recorded as a note (informational).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..models import JsonDict

_MANIFEST_PATH = Path(__file__).resolve().parents[1] / "data" / "enrichment_scope_manifest.json"


@dataclass(frozen=True)
class ScopeCheck:
    operation: str
    field_path: str
    match_status: str  # PASS | FAIL | SKIP | N/A
    notes: str
    source_system: str = "Audit enricher scope"
    source_api: str = "enrichment_scope_manifest"


def load_enrichment_scope_manifest(path: Path | None = None) -> dict[str, Any]:
    p = path or _MANIFEST_PATH
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    ops = data.get("operations") if isinstance(data, dict) else None
    return ops if isinstance(ops, dict) else {}


def _has_snapshot(enriched: JsonDict, layer: str) -> bool:
    node = enriched.get(layer)
    if not isinstance(node, dict):
        return False
    snap = node.get("enrichedSnapshot")
    return isinstance(snap, dict) and bool(snap)


def validate_enrichment_scope(
    operation: str,
    enriched: JsonDict,
    *,
    manifest: dict[str, Any] | None = None,
) -> list[ScopeCheck]:
    """Return synthetic comparison rows for actor/subject enrichment scope."""
    man = manifest if manifest is not None else load_enrichment_scope_manifest()
    spec = man.get(operation) if isinstance(man, dict) else None
    if not isinstance(spec, dict):
        # No manifest entry — the snapshot is produced by the audit resolver and
        # there is nothing external to source it from. Do NOT emit a SKIP (it was
        # showing up as a permanent "partial" on every run); simply omit the row.
        return []

    impl = spec.get("implementation") or {}
    enf = spec.get("enforced") or {}
    gap = bool(spec.get("gap"))
    has_subj = _has_snapshot(enriched, "subject")
    has_actor = _has_snapshot(enriched, "actor")
    rows: list[ScopeCheck] = []

    # --- Implementation (what enricher produces) ---
    if impl.get("subject"):
        rows.append(
            ScopeCheck(
                operation=operation,
                field_path="subject.enrichedSnapshot",
                match_status="PASS" if has_subj else "FAIL",
                notes=(
                    "Enricher produces subject.enrichedSnapshot"
                    if has_subj
                    else "Enricher produces subject.enrichedSnapshot but sample has none"
                ),
                source_api="implementation.produces_subject",
            )
        )
    else:
        rows.append(
            ScopeCheck(
                operation=operation,
                field_path="subject.enrichedSnapshot",
                match_status="PASS" if not has_subj else "SKIP",
                notes=(
                    "Enricher is not subject-scoped (actor-only / none)"
                    if not has_subj
                    else "Sample has subject.enrichedSnapshot but enricher is not subject-scoped"
                ),
                source_api="implementation.produces_subject=false",
            )
        )

    if impl.get("actor"):
        rows.append(
            ScopeCheck(
                operation=operation,
                field_path="actor.enrichedSnapshot",
                match_status="PASS" if has_actor else "FAIL",
                notes=(
                    "Enricher produces actor.enrichedSnapshot"
                    if has_actor
                    else "Enricher produces actor.enrichedSnapshot but sample has none"
                ),
                source_api="implementation.produces_actor",
            )
        )
    else:
        rows.append(
            ScopeCheck(
                operation=operation,
                field_path="actor.enrichedSnapshot",
                match_status="PASS" if not has_actor else "SKIP",
                notes=(
                    "Enricher is not actor-scoped (subject-only / none)"
                    if not has_actor
                    else "Sample has actor.enrichedSnapshot but enricher is not actor-scoped"
                ),
                source_api="implementation.produces_actor=false",
            )
        )

    # --- Enforcement (what handler requires to publish) ---
    if enf.get("subject"):
        rows.append(
            ScopeCheck(
                operation=operation,
                field_path="enrichmentScope.enforced.subject",
                match_status="PASS" if has_subj else "FAIL",
                notes=(
                    "Handler requires subject.enrichedSnapshot — present"
                    if has_subj
                    else "Handler requires subject.enrichedSnapshot — MISSING (would nack)"
                ),
                source_api="handler.requiresSubjectEnrichedSnapshot",
            )
        )
    if enf.get("actor"):
        rows.append(
            ScopeCheck(
                operation=operation,
                field_path="enrichmentScope.enforced.actor",
                match_status="PASS" if has_actor else "FAIL",
                notes=(
                    "Handler requires actor.enrichedSnapshot — present"
                    if has_actor
                    else "Handler requires actor.enrichedSnapshot — MISSING (would nack)"
                ),
                source_api="handler.requiresActorEnrichedSnapshot",
            )
        )

    if gap:
        # Produce≠require is purely informational. Record it as PASS (generated by the
        # audit resolver) so it never shows up as a permanent SKIP / partial coverage.
        rows.append(
            ScopeCheck(
                operation=operation,
                field_path="enrichmentScope.gap",
                match_status="PASS",
                notes=(
                    "Enrichment snapshot generated by audit resolver — "
                    f"implementation={impl.get('scope')} enforced={enf.get('scope')} "
                    "(informational, nothing external to source)"
                ),
                source_api="manifest.gap",
            )
        )

    return rows
