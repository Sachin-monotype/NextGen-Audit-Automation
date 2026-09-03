"""Tests for resolver payload-dumps fallback helpers."""

from __future__ import annotations

import gzip
import json
from unittest.mock import MagicMock, patch

from audit_validator.payload_dumps import (
    dump_type_for_tab,
    fetch_payload_dump,
    payload_dumps_base_url,
)


def test_dump_type_for_tab():
    assert dump_type_for_tab("raw") == "inbound"
    assert dump_type_for_tab("enriched") == "outbound"
    assert dump_type_for_tab("dlq") is None


def test_payload_dumps_base_url_uat_default(monkeypatch):
    monkeypatch.delenv("PAYLOAD_DUMPS_URL", raising=False)
    monkeypatch.delenv("PAYLOAD_DUMPS_URL_UAT", raising=False)
    monkeypatch.delenv("INGRESS_API_URL", raising=False)
    monkeypatch.setenv("AUDIT_TARGET", "uat")
    url = payload_dumps_base_url()
    assert url == "https://mt-audit-log-resolver-service.monotype-uat.com/v1/payload-dumps"


def test_payload_dumps_base_url_from_ingress(monkeypatch):
    monkeypatch.setenv(
        "INGRESS_API_URL",
        "https://mt-audit-log-resolver-service-qa.monotype-pp.com/v1/audit-events",
    )
    monkeypatch.delenv("PAYLOAD_DUMPS_URL", raising=False)
    monkeypatch.delenv("PAYLOAD_DUMPS_URL_QA", raising=False)
    monkeypatch.setenv("AUDIT_TARGET", "qa")
    assert (
        payload_dumps_base_url()
        == "https://mt-audit-log-resolver-service-qa.monotype-pp.com/v1/payload-dumps"
    )


def test_fetch_payload_dump_decodes_gzip(monkeypatch):
    envelope = {
        "xCorrelationId": "41b762c3-eba3-4369-90b0-85c28ea2435e",
        "source": {"operation": "addFavoriteFamilies"},
    }
    body = gzip.compress(json.dumps(envelope).encode("utf-8"))
    resp = MagicMock()
    resp.status_code = 200
    resp.content = body
    with patch("audit_validator.payload_dumps.requests.get", return_value=resp) as get:
        out = fetch_payload_dump(
            "41b762c3-eba3-4369-90b0-85c28ea2435e",
            tab="raw",
            base_url="https://example.test/v1/payload-dumps",
        )
    assert out == envelope
    assert get.call_args.kwargs["params"]["type"] == "inbound"
