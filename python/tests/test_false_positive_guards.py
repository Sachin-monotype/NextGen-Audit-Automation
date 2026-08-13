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


def test_cancel_batch_false_positive_matched_as_enriched():
    from audit_validator.source_validation.comparison_rows import build_comparison_rows

    enriched = {
        "source": {"actorUserAgent": "python-requests/2.32.5"},
        "subject": {
            "metadata": {
                "input": {"batchId": "5b3bb110-81ed-42f2-b856-dc0de6313810"},
                "result": {
                    "actionType": "BYOF_INGESTION",
                    "batchId": "5b3bb110-81ed-42f2-b856-dc0de6313810",
                    "createdAt": "2026-08-13T01:42:25.276Z",
                    "progressPercent": 43,
                    "updatedAt": "2026-08-13T01:42:26.713Z",
                },
            }
        },
        "actor": {
            "enrichedSnapshot": {
                "user": {"role": {"displayName": "Adfmin EmptyProfiles 1786504563"}}
            }
        },
    }
    rows = build_comparison_rows("cancelBatch(global)", enriched)
    for r in rows:
        if r.node in ("enrichment", "enrichmentScope"):
            continue
        assert r.match_status == "PASS", f"Expected PASS for field {r.field_path}, got {r.match_status}"
        assert r.value_in_source == r.value_in_enriched


def test_link_document_to_project_subject_id_matches_project_id():
    from audit_validator.source_validation.comparison_rows import build_comparison_rows

    enriched = {
        "xCorrelationId": "8a340568-b452-41af-9078-fd68216cc8a4",
        "eventId": "01KZXNSFNNR0GDSRJ72R910AN1",
        "subject": {
            "type": "project",
            "id": ["044db991-1b2e-47e2-b4d9-3a276cdd33ad"],
            "projectId": "044db991-1b2e-47e2-b4d9-3a276cdd33ad",
            "metadata": {
                "result": {
                    "id": "919bbaea-078b-491d-a670-33223c00749b",
                    "documentId": "19D601B4-C764-47E4-82A9-17A477CFF365",
                    "project": {
                        "id": "044db991-1b2e-47e2-b4d9-3a276cdd33ad",
                        "name": "ChANGE THE PROJECT NMAE",
                    },
                },
                "input": {
                    "documentId": "19D601B4-C764-47E4-82A9-17A477CFF365",
                    "projectId": "044db991-1b2e-47e2-b4d9-3a276cdd33ad",
                },
            },
        },
    }
    live = {
        "trigger": {
            "graphql_response": {
                "linkDocumentToProject": {
                    "id": "919bbaea-078b-491d-a670-33223c00749b",
                    "documentId": "19D601B4-C764-47E4-82A9-17A477CFF365",
                    "project": {
                        "id": "044db991-1b2e-47e2-b4d9-3a276cdd33ad",
                        "name": "ChANGE THE PROJECT NMAE",
                    },
                }
            }
        }
    }
    rows = build_comparison_rows("linkDocumentToProject(project)", enriched, live=live)
    subj_id_row = next(r for r in rows if r.field_path in ("subject.id", "subject.id[0]"))
    assert subj_id_row.match_status == "PASS"
    assert subj_id_row.value_in_source == "044db991-1b2e-47e2-b4d9-3a276cdd33ad"
    assert subj_id_row.value_in_enriched == "044db991-1b2e-47e2-b4d9-3a276cdd33ad"


