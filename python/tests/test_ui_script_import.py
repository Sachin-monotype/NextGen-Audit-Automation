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
