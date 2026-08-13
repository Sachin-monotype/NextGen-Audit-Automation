"""Test FontBridge event mapping rules and platformEnvironment / service validation."""

from __future__ import annotations

from audit_validator.simulation.trigger_context import build_trigger_context
from audit_validator.source_validation.comparison_rows import build_comparison_rows


def test_fontbridge_trigger_context():
    ctx = build_trigger_context(
        operation="fontSyncFailure(fontbridge)",
        correlation_id="test-corr-id-123",
        invent_client_defaults=True,
    )
    assert ctx["source"]["service"] == "MonotypeFontBridge"
    assert ctx["source"]["platformEnvironment"] == "fontbridge"


def test_fontbridge_comparison_rows_pass():
    enriched = {
        "xCorrelationId": "test-corr-id-123",
        "source": {
            "service": "MonotypeFontBridge",
            "operation": "fontSyncFailure",
            "platform": "nextGen",
            "platformEnvironment": "fontbridge",
        },
    }
    live = {
        "trigger": {
            "source": {
                "service": "MonotypeFontBridge",
                "operation": "fontSyncFailure",
                "platform": "nextGen",
                "platformEnvironment": "app",
            }
        }
    }

    rows = build_comparison_rows("fontSyncFailure(fontbridge)", enriched, live=live)
    row_env = next(r for r in rows if r.field_path == "source.platformEnvironment")
    row_svc = next(r for r in rows if r.field_path == "source.service")

    assert row_env.match_status == "PASS"
    assert row_env.value_in_enriched == "fontbridge"
    assert row_svc.match_status == "PASS"
    assert row_svc.value_in_enriched == "MonotypeFontBridge"
