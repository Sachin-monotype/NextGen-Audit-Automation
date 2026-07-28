"""AUDIT_GRAPHQL capture parsing for CasePilot UI runs."""

from audit_validator.ui_trigger import (
    _extract_json_after_marker,
    _normalize_graphql_capture,
    extract_audit_details_from_casepilot_result,
)


def test_extract_json_after_marker():
    text = 'AUDIT_GRAPHQL {"input":{"familyIds":["1"]},"response":{"activateFamily":{"success":true}}}'
    blob = _extract_json_after_marker(text, "AUDIT_GRAPHQL")
    assert blob is not None
    assert blob["input"]["familyIds"] == ["1"]
    assert blob["response"]["activateFamily"]["success"] is True


def test_normalize_graphql_capture_wraps_bare_response():
    inp, resp = _normalize_graphql_capture(
        "activateFamily",
        {"input": {"familyIds": ["9"]}, "response": {"success": True, "errors": []}},
    )
    assert inp["familyIds"] == ["9"]
    assert "activateFamily" in resp
    assert resp["activateFamily"]["success"] is True


def test_extract_audit_details_pairs_result_with_graphql():
    notes = """
    AUDIT_RESULT|operation=activateFamily|correlation_id=6268fbc3-19ba-45f2-b936-6f53bf28da2d|touchpoint=project
    AUDIT_GRAPHQL {"input":{"familyIds":["910130168"],"projectId":"ddda2d3b-fa2f-4f5a-aa30-4057228492e3"},"response":{"activateFamily":{"success":true}}}
    """
    rows = extract_audit_details_from_casepilot_result(
        {"result": {"notes": notes}},
        default_operation="activateFamily",
        default_touchpoint="Project",
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["correlation_id"] == "6268fbc3-19ba-45f2-b936-6f53bf28da2d"
    assert row["graphql_input"]["familyIds"] == ["910130168"]
    assert row["graphql_response"]["activateFamily"]["success"] is True
