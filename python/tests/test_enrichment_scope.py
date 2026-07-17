"""Unit tests for enrichment-scope validation."""

from __future__ import annotations

from audit_validator.source_validation.enrichment_scope import (
    load_enrichment_scope_manifest,
    validate_enrichment_scope,
)


def test_manifest_loads_activate_family():
    man = load_enrichment_scope_manifest()
    assert "activateFamily" in man
    spec = man["activateFamily"]
    assert spec["implementation"]["subject"] is True
    assert spec["implementation"]["actor"] is True
    assert spec["enforced"]["subject"] is True
    assert spec["enforced"]["actor"] is False
    assert spec["gap"] is True


def test_validate_activate_family_both_snapshots():
    enriched = {
        "subject": {"enrichedSnapshot": {"fontDetails": []}},
        "actor": {"enrichedSnapshot": {"user": {}}},
    }
    rows = validate_enrichment_scope("activateFamily", enriched)
    statuses = {r.field_path: r.match_status for r in rows}
    assert statuses["subject.enrichedSnapshot"] == "PASS"
    assert statuses["actor.enrichedSnapshot"] == "PASS"
    assert statuses["enrichmentScope.enforced.subject"] == "PASS"
    assert "enrichmentScope.enforced.actor" not in statuses  # not required
    assert statuses["enrichmentScope.gap"] == "SKIP"


def test_validate_activate_family_missing_subject_fails():
    enriched = {"subject": {}, "actor": {"enrichedSnapshot": {"user": {}}}}
    rows = validate_enrichment_scope("activateFamily", enriched)
    by_path = {r.field_path: r for r in rows}
    assert by_path["subject.enrichedSnapshot"].match_status == "FAIL"
    assert by_path["enrichmentScope.enforced.subject"].match_status == "FAIL"


def test_actor_only_set_language():
    man = load_enrichment_scope_manifest()
    if "setLanguagePreference" not in man:
        return
    enriched = {"actor": {"enrichedSnapshot": {"user": {}}}, "subject": {}}
    rows = validate_enrichment_scope("setLanguagePreference", enriched)
    by_path = {r.field_path: r for r in rows}
    assert by_path["actor.enrichedSnapshot"].match_status == "PASS"
    assert by_path["subject.enrichedSnapshot"].match_status == "PASS"
