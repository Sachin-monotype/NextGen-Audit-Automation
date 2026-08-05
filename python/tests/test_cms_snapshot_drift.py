"""Resolver/Typesense source classification + Validation=N enricher constants."""

from __future__ import annotations

from audit_validator.source_validation.comparison_rows import _row
from audit_validator.source_validation.mapping_registry import MappingField, _parse_source


def test_parse_source_middleware_discovery_is_resolver_not_typesense() -> None:
    sys, _ = _parse_source("Source: Resolver → mt-connect-middleware-discovery")
    assert sys == "Resolver"


def test_parse_source_discovery_catalog_still_typesense() -> None:
    sys, _ = _parse_source("Discovery POST /v1/styles → mtc_families_data.id")
    assert sys == "Typesense"


def test_cms_display_name_mismatch_stays_fail() -> None:
    """Live CMS ≠ enriched snapshot is a real mismatch — never demote to SKIP."""
    spec = MappingField(
        field="displayName",
        node="customer",
        sub_node="",
        attribute="",
        data_mapping="",
        notes="",
        validate="Y",
        enriched_path="actor.enrichedSnapshot.customer.displayName",
        source_system="CMS",
        source_api="GET customer",
        layer="actor",
    )
    live = {
        "cms_customer": {
            "id": "a4175cbf-1419-4a30-aa21-12109bf942f6",
            "displayName": "Audit Co 10210",
        },
        "jwt_identity": {"gcid": "a4175cbf-1419-4a30-aa21-12109bf942f6"},
    }
    row = _row("activateList(list)", spec, "Audit Co 10210", "Audit Co 97204", live=live)
    assert row.match_status == "FAIL"


def test_subject_enriched_snapshot_source_validation_n_is_pass() -> None:
    spec = MappingField(
        field="source",
        node="",
        sub_node="",
        attribute="",
        data_mapping="",
        notes="",
        validate="N",
        enriched_path="subject.enrichedSnapshot.source",
        source_system="Resolver",
        source_api="enricher constant",
        layer="subject",
    )
    row = _row(
        "activateList(list)",
        spec,
        None,
        "mt-connect-middleware-discovery",
        live={},
    )
    assert row.match_status == "PASS"
