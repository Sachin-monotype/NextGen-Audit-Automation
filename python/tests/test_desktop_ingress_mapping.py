"""Desktop / app audit ingress Excel Response → trigger → compare SOURCE."""

from __future__ import annotations

import json
from io import BytesIO

import pandas as pd

from audit_validator.simulation.trigger_context import build_trigger_context
from audit_validator.source_validation.comparison_rows import (
    _audit_envelope_from_trigger,
    _trigger_value,
)
from audit_validator.ui_script_import import (
    _ingress_source_from_response,
    _unwrap_audit_envelope,
    parse_ui_script_excel,
)


def _envelope() -> dict:
    return {
        "xCorrelationId": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "eventId": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "eventVersion": 1,
        "source": {
            "service": "mtconnect-ui",
            "operation": "fontActivationTypeSwitched",
            "platformVersion": "1.0.0.0",
            "platformEnvironment": "app",
            "actorUserAgent": "MonotypeNextGen/1.0.0.0 Electron/40",
            "osName": "mac",
            "cpuArch": "arm64",
            "type": ["App", "Font temp activation"],
        },
        "actor": {
            "machineId": "M1",
            "uniqueId": "U1",
            "authenticationState": "authenticated",
            "globalUserId": "gu",
            "globalCustomerId": "gc",
        },
        "subject": {
            "type": "fontFamily",
            "id": ["910"],
            "counts": {"styleCount": 1, "variationCount": 1},
            "styles": [{"id": "920", "familyId": "910"}],
        },
    }


def test_unwrap_top_level_and_op_wrapped_envelope():
    env = _envelope()
    assert _unwrap_audit_envelope(env)["source"]["service"] == "mtconnect-ui"
    wrapped = {"fontActivationTypeSwitched": env}
    assert _ingress_source_from_response(wrapped)["osName"] == "mac"
    assert _unwrap_audit_envelope(wrapped)["actor"]["machineId"] == "M1"


def test_parse_excel_extracts_full_ingress_not_graphql():
    env = _envelope()
    df = pd.DataFrame(
        [
            {
                "event_name": "fontActivationTypeSwitched",
                "scenario": "font",
                "target": "app",
                "correlation_id": env["xCorrelationId"],
                "status": "OK",
                "response": json.dumps(env),
            }
        ]
    )
    buf = BytesIO()
    df.to_excel(buf, index=False)
    rows = parse_ui_script_excel(buf.getvalue(), target="app")
    assert len(rows) == 1
    assert rows[0]["ingress_source"]["service"] == "mtconnect-ui"
    assert rows[0]["ingress_actor"]["machineId"] == "M1"
    assert rows[0]["ingress_subject"]["type"] == "fontFamily"
    assert rows[0]["graphql_response"] is None


def test_trigger_and_compare_prefer_ingress_over_be_defaults():
    env = _envelope()
    ctx = build_trigger_context(
        operation="fontActivationTypeSwitched",
        correlation_id=env["xCorrelationId"],
        invent_client_defaults=False,
        user_agent=env["source"]["actorUserAgent"],
        platform_environment="app",
        ingress_source=env["source"],
        ingress_actor=env["actor"],
        ingress_subject=env["subject"],
        ingress_headers={
            "xCorrelationId": env["xCorrelationId"],
            "eventId": env["eventId"],
            "eventVersion": env["eventVersion"],
        },
    )
    assert ctx["source"]["service"] == "mtconnect-ui"
    assert ctx["source"]["platformVersion"] == "1.0.0.0"
    assert ctx["source"]["osName"] == "mac"
    assert _trigger_value("source.service", ctx, {}) == "mtconnect-ui"
    assert _trigger_value("actor.machineId", ctx, {}) == "M1"
    assert _trigger_value("actor.globalUserId", ctx, {}) == "gu"
    assert _trigger_value("actor.globalCustomerId", ctx, {}) == "gc"
    assert _trigger_value("subject.id[0]", ctx, {}) == "910"
    assert _trigger_value("subject.styles[0].id", ctx, {}) == "920"

    from audit_validator.source_validation.comparison_rows import _resolve_source_value
    from audit_validator.source_validation.mapping_registry import MappingField

    for path, expected in (
        ("actor.globalUserId", "gu"),
        ("actor.globalCustomerId", "gc"),
    ):
        spec = MappingField(
            path, "", "", "", "", "", "Y", path, "Bearer token", "JWT claim", "actor"
        )
        val, note = _resolve_source_value(
            spec,
            {"actor": {"globalUserId": "gu", "globalCustomerId": "gc"}, "source": {"service": "mtconnect-ui", "platformEnvironment": "app"}},
            live={"trigger": ctx},
            operation="fontTempActivated",
        )
        assert val == expected
        assert "ingress" in note.lower()

    legacy = {
        "source": {"service": "mtconnect-api", "platformVersion": "1.0.0"},
        "graphql_response": {"fontActivationTypeSwitched": env},
        "replay_mode": "playwright_script",
        "capture_source": "playwright_script",
    }
    assert _audit_envelope_from_trigger(legacy)["source"]["service"] == "mtconnect-ui"
    assert _trigger_value("source.service", legacy, {}) == "mtconnect-ui"
    assert str(_trigger_value("source.actorUserAgent", legacy, {})).startswith(
        "MonotypeNextGen"
    )
