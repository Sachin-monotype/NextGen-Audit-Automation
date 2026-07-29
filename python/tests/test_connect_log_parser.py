"""Tests for Connect service log parsing."""

from __future__ import annotations

from pathlib import Path

from audit_validator.ingress.connect_log_parser import (
    group_by_operation,
    parse_connect_service_log,
)


SAMPLE = """\
07-29 09:14:07.629 +05:45 [INF] [Mtf.HttpClient.Core.CurlLoggingDelegatingHandler] [CurlDebug] curl -X POST 'https://example.com/v1/audit-events' -d '[{"xCorrelationId":"aaa11111-1111-4111-8111-111111111111","eventId":"e1","occurredAt":"2026-07-29T03:24:06Z","source":{"operation":"userLogoutApp","service":"MonotypeNextGenConnectService","operationState":"success"}},{"xCorrelationId":"bbb22222-2222-4222-8222-222222222222","eventId":"e2","occurredAt":"2026-07-29T03:25:21Z","source":{"operation":"appSettingsAutoPerformanceEnabled","service":"mtconnect-ui","operationState":"success"}}]'
07-29 09:14:08.571 +05:45 [INF] [Mtf.HttpClient.Core.CurlLoggingDelegatingHandler] [CurlDebug] curl -X POST 'https://example.com/v1/audit-events' -d '[{"xCorrelationId":"bbb22222-2222-4222-8222-222222222222","eventId":"e3","occurredAt":"2026-07-29T03:30:00Z","source":{"operation":"appSettingsAutoPerformanceEnabled","service":"mtconnect-ui","operationState":"success"}}]'
"""


def test_parse_groups_unique_cids_per_operation(tmp_path: Path) -> None:
    log = tmp_path / "file.log"
    log.write_text(SAMPLE, encoding="utf-8")
    events = parse_connect_service_log(log)
    groups = group_by_operation(events)
    assert groups["userLogoutApp"].correlation_ids == ["aaa11111-1111-4111-8111-111111111111"]
    assert groups["appSettingsAutoPerformanceEnabled"].correlation_ids == [
        "bbb22222-2222-4222-8222-222222222222"
    ]
    assert len(groups["appSettingsAutoPerformanceEnabled"].events) == 2
