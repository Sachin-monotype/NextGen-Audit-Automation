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


def test_fontbridge_auth_failed_store_clean_upgrades_to_pass():
    from backend.app.comparison_store import _clean_app_ui_be_defaults, _summary_for_rows

    raw_rows = [
        {
            "operation": "fontBridgeAuthFailed",
            "field_path": "source.platformEnvironment",
            "value_in_source": "web",
            "value_in_enriched": "fontbridge",
            "match_status": "FAIL",
            "notes": "Derived from actorUserAgent",
        },
        {
            "operation": "fontBridgeAuthFailed",
            "field_path": "actor.globalCustomerId",
            "value_in_source": "",
            "value_in_enriched": "93bbce28-5143-497c-a959-1f9eada55230",
            "match_status": "SKIP",
            "notes": "Trigger context not captured for this run",
        },
        {
            "operation": "fontBridgeAuthFailed",
            "field_path": "subject.authStatus",
            "value_in_source": "",
            "value_in_enriched": "FONTBRIDGE_AUTH_FAILED",
            "match_status": "SKIP",
            "notes": "Trigger context not captured for this run",
        },
    ]

    cleaned, changed = _clean_app_ui_be_defaults(raw_rows)
    assert changed is True
    summary = _summary_for_rows(cleaned)
    assert summary["passed"] == 3
    assert summary["failed"] == 0
    assert summary["skipped"] == 0
    for r in cleaned:
        assert r["match_status"] == "PASS"
        assert r["value_in_source"] == r["value_in_enriched"]

