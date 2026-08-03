"""Discovery auth + Typesense scoring: 401 must FAIL, M2M must not be used."""

from __future__ import annotations

from audit_validator.source_validation.comparison_rows import (
    ComparisonRow,
    MappingField,
    _is_auth_error,
    _is_unreachable_error,
    _row,
)


def test_401_is_auth_not_unreachable():
    note = "Discovery/Typesense error: 401 Client Error: Unauthorized for url: https://x/v1/styles"
    assert _is_auth_error(note)
    assert not _is_unreachable_error(note)


def test_typesense_401_scores_fail_not_na():
    spec = MappingField(
        field="family name",
        node="subject",
        sub_node="enrichedSnapshot",
        attribute="name_en",
        data_mapping="Typesense",
        notes="",
        validate="Y",
        enriched_path="subject.enrichedSnapshot.fontDetails[0].family.catalog.name_en",
        source_system="Typesense",
        source_api="POST /v1/styles",
        layer="subject",
    )
    live = {
        "discovery_error": (
            "Discovery/Typesense error: 401 Client Error: Unauthorized for url: "
            "https://mtc-middleware-discovery.monotype-pp.com/v1/styles?skipInventoryCheck=true"
        )
    }
    row = _row(
        "activateFamily",
        spec,
        None,
        "001 Sans Serif Family",
        live=live,
    )
    assert isinstance(row, ComparisonRow)
    assert row.match_status == "FAIL"
    assert "401" in (row.notes or "") or "Unauthorized" in (row.notes or "")


def test_vpn_timeout_still_na():
    spec = MappingField(
        field="family name",
        node="subject",
        sub_node="enrichedSnapshot",
        attribute="name_en",
        data_mapping="Typesense",
        notes="",
        validate="Y",
        enriched_path="subject.enrichedSnapshot.fontDetails[0].family.catalog.name_en",
        source_system="Typesense",
        source_api="POST /v1/styles",
        layer="subject",
    )
    live = {"discovery_error": "Discovery/Typesense error: HTTPSConnectionPool timed out"}
    row = _row("activateFamily", spec, None, "Name", live=live)
    assert row.match_status == "N/A"
