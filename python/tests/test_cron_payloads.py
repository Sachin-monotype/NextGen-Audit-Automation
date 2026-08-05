"""Tests for cron payload normalization."""

from __future__ import annotations

import json
from pathlib import Path

from audit_validator.cron.payloads import (
    amqp_routing_key_for_payload,
    load_cron_cases,
    normalize_cron_payload,
)

_CRON_DIR = Path(__file__).resolve().parents[1] / "audit_validator" / "data" / "cron_payloads"


def test_normalize_refreshes_static_subject_id():
    raw = {
        "routingKey": "reporting.window.final",
        "source": {"service": "license-management-service", "operation": "quarterlyReportNotification"},
        "subject": {"id": "193a9192-34ae-4e80-adb6-85112b3b07d1", "type": "entitlement"},
    }
    a = normalize_cron_payload(raw, case_id="lmsclosefinal")
    b = normalize_cron_payload(raw, case_id="lmsclosefinal")
    assert a["subject"]["id"] != "193a9192-34ae-4e80-adb6-85112b3b07d1"
    assert a["subject"]["id"] != b["subject"]["id"]


def test_normalize_sets_subject_id_when_missing():
    """Export-style payloads omit subject.id — we must mint one before raw publish."""
    raw = {
        "routingKey": "export.completed",
        "source": {"service": "mt-batch-orchestration-service", "trigger": "H-1"},
        "subject": {"operationType": "EXPORT_USERS", "rowCount": 13},
    }
    out = normalize_cron_payload(raw, case_id="exportcomplete")
    assert isinstance(out["subject"]["id"], str)
    assert out["subject"]["id"].strip()


def test_amqp_routing_key_always_raw_events():
    """Body notification routingKey must not be used as the AMQP binding key."""
    for body_rk in ("font.sync.failed", "byof.licence.expired", "export.completed", "user.invitation.accept"):
        payload = {
            "routingKey": body_rk,
            "source": {"service": "byof-license-service", "operation": "notifyByofLicenceExpiry"},
        }
        assert amqp_routing_key_for_payload(payload) == "raw.events"


def test_export_ops_expect_enrichment():
    from audit_validator.cron.payloads import CRON_NO_ENRICHER_OPERATIONS, expects_cron_enrichment

    assert "exportCompleted" not in CRON_NO_ENRICHER_OPERATIONS
    assert "exportFailed" not in CRON_NO_ENRICHER_OPERATIONS
    assert "fontSyncFailure" not in CRON_NO_ENRICHER_OPERATIONS
    assert expects_cron_enrichment("exportCompleted")
    assert expects_cron_enrichment("fontSyncFailure")


def test_cron_mapping_registry_has_export():
    from audit_validator.source_validation.cron_mappings import cron_mapping_for_operation

    rows = cron_mapping_for_operation("exportCompleted(exportcomplete)")
    assert rows
    assert any(r.source_system == "UMS" for r in rows)


def test_normalize_refreshes_byof_static_contract_when_no_live_contract():
    raw = json.loads((_CRON_DIR / "licneseexpiry.json").read_text(encoding="utf-8"))
    out = normalize_cron_payload(raw, case_id="licneseexpiry", byof_contract_id=None)
    assert out["subject"]["id"] == [out["subject"]["contract"]["contractId"]]
    assert out["subject"]["id"] != ["contract-due-1"]


def test_normalize_byof_live_contract_keeps_contract_aligned_ids():
    raw = json.loads((_CRON_DIR / "licneseexpiry.json").read_text(encoding="utf-8"))
    live = "live-contract-abc"
    out = normalize_cron_payload(raw, case_id="licneseexpiry", byof_contract_id=live)
    assert out["subject"]["id"] == [live]
    assert out["subject"]["contract"]["contractId"] == live


def test_new_export_cron_cases_load():
    cases = {c.case_id for c in load_cron_cases(_CRON_DIR)}
    assert "exportcomplete" in cases
    assert "exportfailed" in cases
    assert "lmsclosefinal" in cases


def test_normalize_export_failed_batch_id_unique():
    raw = json.loads((_CRON_DIR / "exportfailed.json").read_text(encoding="utf-8"))
    a = normalize_cron_payload(raw, case_id="exportfailed")
    b = normalize_cron_payload(raw, case_id="exportfailed")
    assert a["subject"]["batchId"] != b["subject"]["batchId"]
    assert a["subject"]["batchId"] != "039e9ba3-281b-431f-baed-bb6ad31f66c8"
    assert a["subject"]["id"] and a["subject"]["id"] != b["subject"]["id"]
