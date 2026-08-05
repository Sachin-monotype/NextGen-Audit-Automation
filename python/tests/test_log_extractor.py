"""Tests for ConnectService CurlDebug payload extraction."""

from __future__ import annotations

from audit_validator.desktop.log_extractor import _extract_payload_from_curl


def test_extract_nested_font_activation_payload():
    # Nested subject.styles[].variations[] previously broke non-greedy [{…}] regex.
    curl = (
        "curl -X POST 'https://mt-audit-log-resolver-service-preprod.monotype-pp.com/v1/audit-events' "
        "-H 'Content-Type: application/json' "
        "-d '[{"
        '"xCorrelationId":"fc4076eb-454f-47f7-aa62-f8f7c16f3465",'
        '"eventId":"0a8833bb-1e6b-46c8-ba00-f31f4aa1b5c2",'
        '"eventVersion":1,'
        '"occurredAt":"2026-08-05T03:50:26.1450000Z",'
        '"source":{"type":["App","Font activation"],"service":"mtconnect-ui",'
        '"operation":"fontActivationTypeSwitched","operationIndex":0,'
        '"operationState":"success","platform":"nextGen","platformEnvironment":"app",'
        '"platformVersion":"1.0.0.0","actorUserAgent":"MonotypeNextGen/1.0.0 Electron/40.10.6",'
        '"osName":"mac","osVersion":"26.5.1","cpuArch":"arm64"},'
        '"actor":{"authenticationState":"authenticated",'
        '"machineId":"Z0DGQF85CNYAKYPVJCW51JTSM291NED8E60NC9YDXCVZWD605Y60",'
        '"uniqueId":"7DC9A6DE16142DF572607138",'
        '"globalUserId":"c6fdbd53-876d-11f1-ac0d-0e0a04e472ab",'
        '"globalCustomerId":"a4175cbf-1419-4a30-aa21-12109bf942f6"},'
        '"subject":{"type":"fontFamily","id":["910044682"],'
        '"styles":[{"familyId":"910044682","id":"1246206",'
        '"variations":[{"id":"1246207","md5":"b0ea66753942c1028f90a0b27ea2dd17",'
        '"activationState":"ACTIVATED"}]}]}'
        "}]'"
    )
    payload = _extract_payload_from_curl(curl)
    assert payload is not None
    assert payload["xCorrelationId"] == "fc4076eb-454f-47f7-aa62-f8f7c16f3465"
    assert payload["source"]["operation"] == "fontActivationTypeSwitched"
    assert payload["subject"]["styles"][0]["variations"][0]["md5"] == (
        "b0ea66753942c1028f90a0b27ea2dd17"
    )


def test_extract_batch_payloads_all_cids():
    from audit_validator.desktop.log_extractor import _extract_payloads_from_curl

    curl = (
        "curl -X POST 'https://mt-audit-log-resolver-service-qa.monotype-pp.com/v1/audit-events' "
        "-H 'Content-Type: application/json' "
        "-d '["
        '{"xCorrelationId":"aaa-1","source":{"operation":"fontActivationTypeSwitched"}},'
        '{"xCorrelationId":"bbb-2","source":{"operation":"fontActivationTypeSwitched"}},'
        '{"xCorrelationId":"ccc-3","source":{"operation":"fontActivationTypeSwitched"}}'
        "]'"
    )
    payloads = _extract_payloads_from_curl(curl)
    assert [p["xCorrelationId"] for p in payloads] == ["aaa-1", "bbb-2", "ccc-3"]
