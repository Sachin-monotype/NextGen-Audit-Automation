"""Mappings for License Management production-intent ops (resolver-backed)."""

from __future__ import annotations

from audit_validator.rabbitmq.resolver_routing_map import expected_routing_key
from audit_validator.source_validation.mapping_registry import get_operation_mapping
from audit_validator.utility.operation_graphql import get_export_for_operation


def _paths(op: str) -> set[str]:
    return {f.enriched_path for f in get_operation_mapping(op)}


def test_submit_intent_mapping_uses_discovery_font_details():
    paths = _paths("submitIntentForProduction")
    assert "subject.enrichedSnapshot.fontDetails[0].styles[0].id" in paths
    assert "subject.metadata.input.styleId" in paths
    assert "actor.enrichedSnapshot.user.profile.id" in paths
    assert expected_routing_key("submitIntentForProduction") == (
        "font.licence.intent_for_production_submitted"
    )
    assert get_export_for_operation("submitIntentForProduction") == (
        "SUBMIT_INTENT_FOR_PRODUCTION"
    )


def test_bulk_submit_intent_mapping_uses_intents_input():
    paths = _paths("bulkSubmitIntentForProduction")
    assert "subject.enrichedSnapshot.fontDetails[0].family.id" in paths
    assert "subject.metadata.input.intents[0].styleId" in paths
    assert "subject.metadata.input.styleId" not in paths
    assert expected_routing_key("bulkSubmitIntentForProduction") == (
        "font.licence.bulk_intent_for_production_submitted"
    )
    assert get_export_for_operation("bulkSubmitIntentForProduction") == (
        "BULK_SUBMIT_INTENT_FOR_PRODUCTION"
    )


def test_deny_intent_mapping_uses_lms_requests():
    paths = _paths("denyIntentForProduction")
    assert "subject.enrichedSnapshot.requests[0].styleId" in paths
    assert "subject.enrichedSnapshot.requests[0].status" in paths
    assert "subject.metadata.input.requestIds[0]" in paths
    assert "subject.enrichedSnapshot.fontDetails[0].styles[0].id" not in paths
    assert expected_routing_key("denyIntentForProduction") == (
        "font.licence.intent_for_production_denied"
    )
    assert get_export_for_operation("denyIntentForProduction") == (
        "DENY_INTENT_FOR_PRODUCTION"
    )
