"""Tests for Playwright UI script Excel import."""

from __future__ import annotations

import base64
import json
from io import BytesIO
from pathlib import Path

import pandas as pd

from audit_validator.ui_script_import import (
    _jwt_identity_for_row,
    list_ui_script_catalog,
    parse_ui_script_excel,
    resolve_ui_script_datasource_path,
)


def _xlsx(sheets: dict[str, pd.DataFrame]) -> bytes:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name, index=False)
    return buf.getvalue()


def _fake_jwt(**claims: object) -> str:
    def b64(obj: dict) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{b64({'alg': 'none'})}.{b64(claims)}.x"


def test_parse_ui_script_excel_columns():
    df = pd.DataFrame(
        [
            {
                "event_name": "activateFamily",
                "scenario": "global",
                "correlation_id": "11111111-1111-1111-1111-111111111111",
                "status": "OK",
                "response": json.dumps({"activateFamily": {"success": True}}),
            }
        ]
    )
    buf = BytesIO()
    df.to_excel(buf, index=False)
    rows = parse_ui_script_excel(buf.getvalue())
    assert len(rows) == 1
    assert rows[0]["operation"] == "activateFamily"
    assert rows[0]["touchpoint"] == "global"
    assert rows[0]["correlation_id"].startswith("11111111")
    assert rows[0]["graphql_response"]["activateFamily"]["success"] is True
    assert rows[0]["auth_token"] == ""


def test_parse_skips_error_and_ffills_event():
    df = pd.DataFrame(
        [
            {
                "event_name": "activateVariation",
                "scenario": "global",
                "correlation_id": "22222222-2222-2222-2222-222222222222",
                "status": "OK",
            },
            {
                "event_name": None,
                "scenario": "Favourite",
                "correlation_id": "33333333-3333-3333-3333-333333333333",
                "status": "OK",
            },
            {
                "event_name": "activateFontProject",
                "scenario": "project",
                "correlation_id": "",
                "status": "ERROR",
            },
            {
                "event_name": "skipMe",
                "scenario": "global",
                "correlation_id": "not-a-uuid",
                "status": "OK",
            },
        ]
    )
    buf = BytesIO()
    df.to_excel(buf, index=False)
    rows = parse_ui_script_excel(buf.getvalue())
    assert len(rows) == 2
    assert rows[0]["operation"] == "activateVariation"
    assert rows[0]["touchpoint"] == "global"
    assert rows[1]["operation"] == "activateVariation"
    assert rows[1]["touchpoint"] == "favourite"


def test_parse_web_app_sheets_and_auth_token_filter():
    token = _fake_jwt(
        **{
            "https://api.monotype.com/gcid": "cust-from-token",
            "https://api.monotype.com/email": "token-user@example.com",
            "sub": "auth0|token-user",
        }
    )
    web = pd.DataFrame(
        [
            {
                "event_name": "activateFamily",
                "scenario": "global",
                "target": "web",
                "correlation_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "auth_token": "",
                "status": "OK",
            },
            {
                "event_name": "bulkActivateLists",
                "scenario": "list",
                "target": "web",
                "correlation_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "auth_token": token,
                "status": "OK",
            },
            {
                "event_name": "activateList",
                "scenario": "project",
                "target": "web",
                "correlation_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
                "auth_token": "",
                "status": "OK",
            },
        ]
    )
    app = pd.DataFrame(
        [
            {
                "event_name": "activateFamily",
                "scenario": "favourite",
                "target": "app",
                "correlation_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
                "auth_token": "",
                "status": "OK",
            },
        ]
    )
    content = _xlsx({"datasource - Web": web, "datasource - App": app})

    web_rows = parse_ui_script_excel(content, target="web")
    assert len(web_rows) == 3
    assert {r["event_name"] for r in web_rows} == {
        "activateFamily",
        "bulkActivateLists",
        "activateList",
    }

    filtered = parse_ui_script_excel(
        content, target="web", events=["bulkActivateLists"], scenarios=["list"]
    )
    assert len(filtered) == 1
    assert filtered[0]["auth_token"].startswith("eyJ")

    pair_only = parse_ui_script_excel(
        content,
        target="web",
        pairs=[{"event_name": "activateFamily", "scenario": "global"}],
    )
    assert len(pair_only) == 1
    assert pair_only[0]["event_name"] == "activateFamily"

    app_rows = parse_ui_script_excel(content, target="app")
    assert len(app_rows) == 1
    assert app_rows[0]["scenario"] == "favourite"


def test_parse_unwraps_graphql_data_wrapper_and_auth():
    token = _fake_jwt(
        **{
            "https://api.monotype.com/gcid": "cust-wrap",
            "https://api.monotype.com/email": "wrap@example.com",
            "sub": "auth0|wrap",
        }
    )
    df = pd.DataFrame(
        [
            {
                "event_name": "activateFamily",
                "scenario": "favourite",
                "correlation_id": "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
                "auth_token": token,
                "Response": json.dumps(
                    {"data": {"activateFamily": {"actionCounts": {"activated": 1}}}}
                ),
                "status": "OK",
            }
        ]
    )
    buf = BytesIO()
    df.to_excel(buf, index=False)
    rows = parse_ui_script_excel(buf.getvalue())
    assert len(rows) == 1
    assert rows[0]["auth_token"].startswith("eyJ")
    assert "data" not in rows[0]["graphql_response"]
    assert rows[0]["graphql_response"]["activateFamily"]["actionCounts"]["activated"] == 1
    from audit_validator.ui_script_import import _normalize_graphql_response

    norm = _normalize_graphql_response("activateFamily", rows[0]["graphql_response"])
    assert "activateFamily" in norm


def test_jwt_identity_from_excel_auth_token():
    token = _fake_jwt(
        **{
            "https://api.monotype.com/gcid": "cust-xyz",
            "https://api.monotype.com/email": "excel@example.com",
            "org_id": "org-1",
            "sub": "auth0|excel",
        }
    )
    ident = _jwt_identity_for_row(token)
    assert ident["gcid"] == "cust-xyz"
    assert ident["email"] == "excel@example.com"
    assert "Excel auth_token" in ident["_source_note"]


def test_resolve_datasource_path_env(tmp_path: Path, monkeypatch):
    fake = tmp_path / "datasource-latest.xlsx"
    fake.write_bytes(b"not-real")
    monkeypatch.setenv("UI_SCRIPT_DATASOURCE_PATH", str(fake))
    assert resolve_ui_script_datasource_path() == fake.resolve()


def test_list_catalog_against_real_datasource_if_present():
    try:
        path = resolve_ui_script_datasource_path()
    except FileNotFoundError:
        return
    cat = list_ui_script_catalog(target="web", path=path)
    assert cat["target"] == "web"
    assert cat["count"] > 0
    assert "activateFamily" in cat["events"]
    app = list_ui_script_catalog(target="app", path=path)
    assert app["count"] > 0


def test_parse_prefers_graphql_op_and_body_correlation_id():
    """Excel label/CID often differ from Response body — trust the body."""
    body_cid = "324360a1-d167-437c-a2f4-43f0ee49924b"
    col_cid = "176127d3-0000-0000-0000-000000000001"
    df = pd.DataFrame(
        [
            {
                "event_name": "updateAssets",
                "scenario": "global",
                "correlation_id": "33a3c631-1111-1111-1111-111111111111",
                "status": "OK",
                "response": json.dumps(
                    {"data": {"updateAsset": {"success": True, "id": "x"}}}
                ),
            },
            {
                "event_name": "fontSimilarViewed",
                "scenario": "global",
                "correlation_id": col_cid,
                "status": "OK",
                "response": json.dumps(
                    {
                        "status": "ok",
                        "xCorrelationId": body_cid,
                        "operation": "fontSimilarViewed",
                    }
                ),
            },
            {
                "event_name": "getActiveBatches",
                "scenario": "global",
                "correlation_id": "81bf0470-1111-1111-1111-111111111111",
                "status": "OK",
                "response": json.dumps(
                    {
                        "errors": [{"message": "bad enum"}],
                        "data": None,
                    }
                ),
            },
        ]
    )
    buf = BytesIO()
    df.to_excel(buf, index=False)
    rows = parse_ui_script_excel(buf.getvalue())
    by_excel = {r["excel_event_name"]: r for r in rows}
    assert by_excel["updateAssets"]["operation"] == "updateAsset"
    assert by_excel["fontSimilarViewed"]["correlation_id"] == body_cid
    assert by_excel["getActiveBatches"]["graphql_failed"] is True


def test_parse_payload_column_maps_ua_and_app_version():
    payload = {
        "userAgent": "Mozilla/5.0 (Macintosh) MonotypeNextGen/1.0.0 Electron/40",
        "appVersion": "1.0.0",
        "correlationId": "44444444-4444-4444-4444-444444444444",
        "jwtClaims": {"gcid": "a4175cbf-1419-4a30-aa21-12109bf942f6", "appName": "Next Gen Native App"},
        "variables": {"input": {"familyIds": ["910130168"]}},
    }
    df = pd.DataFrame(
        [
            {
                "event_name": "ActivateFamilies",
                "scenario": "default",
                "target": "app",
                "correlation_id": "44444444-4444-4444-4444-444444444444",
                "status": "OK",
                "response": json.dumps(
                    {"data": {"activateFamilies": {"errors": [], "families": {"nodes": []}}}}
                ),
                "payload": json.dumps(payload),
            }
        ]
    )
    buf = BytesIO()
    df.to_excel(buf, index=False)
    rows = parse_ui_script_excel(buf.getvalue(), target="app")
    assert len(rows) == 1
    row = rows[0]
    assert row["target"] == "app"
    assert row["request_payload"]["appVersion"] == "1.0.0"
    headers = row["ingress_headers"] or {}
    assert "Electron" in headers.get("User-Agent", "")
    assert headers.get("X-Unified-Version") == "1.0.0"
