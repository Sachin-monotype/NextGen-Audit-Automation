"""Tests for Connect service log capture workflow."""

from __future__ import annotations

from pathlib import Path

from audit_validator.ingress.connect_log_capture import (
    ConnectLogBaseline,
    correlations_for_selection,
    expected_operations_from_selection,
    parse_events_since_baseline,
    prepare_connect_log_baseline,
    selection_uses_connect_log_capture,
    wait_for_connect_log_events,
)

SAMPLE = """\
07-29 09:14:07.629 +05:45 [INF] [Mtf.HttpClient.Core.CurlLoggingDelegatingHandler] [CurlDebug] curl -X POST 'https://example.com/v1/audit-events' -d '[{"xCorrelationId":"aaa11111-1111-4111-8111-111111111111","eventId":"e1","occurredAt":"2026-07-29T03:24:06Z","source":{"operation":"userLogoutApp","service":"MonotypeNextGenConnectService","operationState":"success"}},{"xCorrelationId":"bbb22222-2222-4222-8222-222222222222","eventId":"e2","occurredAt":"2026-07-29T03:25:21Z","source":{"operation":"appSettingsAutoPerformanceEnabled","service":"mtconnect-ui","operationState":"success"}}]'
"""


def test_selection_uses_connect_log_capture() -> None:
    assert selection_uses_connect_log_capture(
        [{"id": "ingress:app_settings_auto_performance_enabled", "operation": "appSettingsAutoPerformanceEnabled"}]
    )
    assert not selection_uses_connect_log_capture(
        [{"id": "activateFamily", "operation": "activateFamily", "touchpoint": "global"}]
    )


def test_prepare_and_parse_since_baseline(tmp_path: Path, monkeypatch) -> None:
    log_dir = tmp_path / "service"
    log_dir.mkdir()
    log = log_dir / "file-20260729.log"
    log.write_text("old noise\n", encoding="utf-8")
    monkeypatch.setenv("CONNECT_LOG_PREPARE", "offset")
    baseline = prepare_connect_log_baseline(log_dir=log_dir)
    assert baseline.byte_offset == len("old noise\n")
    log.write_text("old noise\n" + SAMPLE, encoding="utf-8")
    events = parse_events_since_baseline(baseline)
    ops = {e.operation for e in events}
    assert "appSettingsAutoPerformanceEnabled" in ops
    assert "userLogoutApp" in ops


def test_correlations_for_selection(tmp_path: Path) -> None:
    log = tmp_path / "file.log"
    log.write_text(SAMPLE, encoding="utf-8")
    baseline = ConnectLogBaseline(path=str(log), byte_offset=0)
    events = parse_events_since_baseline(baseline)
    selection = [
        {
            "id": "ingress:app_settings_auto_performance_enabled",
            "operation": "appSettingsAutoPerformanceEnabled",
            "touchpoint": "Desktop App",
        }
    ]
    rows = correlations_for_selection(events, selection)
    assert len(rows) == 1
    assert rows[0]["correlation_id"] == "bbb22222-2222-4222-8222-222222222222"
    assert rows[0]["source"] == "connect_service_log"


def test_wait_for_connect_log_events_times_out(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "file.log"
    log.write_text("", encoding="utf-8")
    baseline = ConnectLogBaseline(path=str(log), byte_offset=0)
    monkeypatch.setenv("CONNECT_LOG_SETTLE_SEC", "1")
    monkeypatch.setenv("CONNECT_LOG_POLL_SEC", "1")
    selection = [{"operation": "appSettingsAutoPerformanceEnabled", "touchpoint": "Desktop App"}]
    result = wait_for_connect_log_events(baseline, selection)
    assert result.status == "timeout"
    assert result.correlations == []


def test_expected_operations_from_selection() -> None:
    sel = [
        {"operation": "appClosed"},
        {"operation": "appClosed"},
        {"operation": "userLogoutApp"},
    ]
    assert expected_operations_from_selection(sel) == ["appClosed", "userLogoutApp"]


def test_parse_supplemental_log_when_primary_empty(tmp_path: Path) -> None:
    """After truncate the primary file may stay 0 bytes; scan sibling/archive logs."""
    log_dir = tmp_path / "service"
    log_dir.mkdir()
    primary = log_dir / "file-20260729.log"
    primary.write_text("", encoding="utf-8")
    archive = log_dir / "file-20260729.log.pre-audit.log"
    archive.write_text(SAMPLE, encoding="utf-8")
    baseline = ConnectLogBaseline(
        path=str(primary),
        byte_offset=0,
        captured_at="2026-07-29T03:20:00+00:00",
        mode="truncate",
        archive_path=str(archive),
    )
    events = parse_events_since_baseline(baseline)
    ops = {e.operation for e in events}
    assert "appSettingsAutoPerformanceEnabled" in ops
