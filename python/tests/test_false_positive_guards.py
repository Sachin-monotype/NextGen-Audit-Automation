"""Guards against known false-positive compare regressions."""

from __future__ import annotations

from audit_validator.source_validation.comparison_rows import (
    _is_deleted_asset_context,
    _private_tag_value,
    _row,
)
from audit_validator.source_validation.mapping_registry import MappingField
from audit_validator.source_validation.runner import (
    _looks_like_uuid,
    _subject_profile_id_from_enriched,
    _subject_team_id_from_enriched,
)


def test_subject_profile_id_ignores_numeric_team_id():
    enriched = {
        "subject": {
            "id": ["60284"],
            "enrichedSnapshot": {
                "team": {"id": 60284, "name": "audit-team"},
            },
        }
    }
    assert _subject_profile_id_from_enriched(enriched) is None
    assert _subject_team_id_from_enriched(enriched) == "60284"


def test_subject_profile_id_keeps_profile_uuid():
    pid = "a4175cbf-1419-4a30-aa21-12109bf942f6"
    enriched = {
        "subject": {
            "id": [pid],
            "enrichedSnapshot": {
                "user": {"profile": {"id": pid}},
            },
        }
    }
    assert _looks_like_uuid(pid)
    assert _subject_profile_id_from_enriched(enriched) == pid


def test_private_tag_nested_association_path():
    tag = {
        "id": "t1",
        "associations": [
            {
                "id": "5760427",
                "font_name": "Helvetica Now Display Regular",
                "is_imported_font": False,
                "mtc_families_data": {"family_name": "Helvetica", "id": 910042901},
            }
        ],
    }
    assert (
        _private_tag_value(
            "subject.enrichedSnapshot.tags[0].associations[0].font_name", tag
        )
        == "Helvetica Now Display Regular"
    )
    assert (
        _private_tag_value("subject.enrichedSnapshot.tags[0].associations[0].id", tag)
        == "5760427"
    )


def test_delete_assets_ams_miss_passes():
    spec = MappingField(
        "assetType",
        "asset",
        "",
        "",
        "",
        "",
        "Y",
        "subject.enrichedSnapshot.asset.assetType",
        "AMS",
        "GET asset",
        "subject",
    )
    assert _is_deleted_asset_context("deleteAssets(global)", spec.enriched_path)
    row = _row(
        "deleteAssets(global)",
        spec,
        None,
        "FontSet",
        notes="AMS asset e3130fda-378c-4a7c-9b93-a734379b6fd4 not found",
        live={"ams_error": "AMS asset e3130fda not found", "ams_asset": None},
    )
    assert row.match_status == "PASS"
    assert row.value_in_source == "FontSet"
