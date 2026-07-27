"""Tests for dynamic ingress runtime context."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from audit_validator.ingress.payloads import normalize_ingress_payload
from audit_validator.ingress.runtime_context import (
    IngressRuntimeContext,
    apply_ingress_runtime_context,
    clear_ingress_runtime_context_cache,
)


@pytest.fixture(autouse=True)
def _clear_runtime_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_ingress_runtime_context_cache()
    monkeypatch.delenv("INGRESS_MACHINE_ID", raising=False)
    monkeypatch.delenv("INGRESS_UNIQUE_ID", raising=False)
    monkeypatch.delenv("INGRESS_DEVICE_FILE", raising=False)


def test_apply_runtime_context_patches_actor_source_and_subject() -> None:
    payload = {
        "source": {
            "operation": "userSwitchWorkspaceApp",
            "osName": "old",
            "osVersion": "0.0.0",
            "cpuArch": "x86",
        },
        "actor": {
            "globalCustomerId": "stale-gcid",
            "globalUserId": "stale-user",
            "machineId": "stale-machine",
        },
        "subject": {"type": "machine", "id": ["stale-machine"]},
    }
    ctx = IngressRuntimeContext(
        gcid="gcid-live",
        profile_id="profile-live",
        machine_id="machine-live",
        unique_id="unique-live",
        os_name="mac",
        os_version="26.5.1",
        cpu_arch="arm64",
        app_version="2.2.2",
        actor_user_agent="MonotypeNextGen/2.2.2",
    )
    apply_ingress_runtime_context(payload, ctx=ctx)

    assert payload["actor"]["globalCustomerId"] == "gcid-live"
    assert payload["actor"]["globalUserId"] == "profile-live"
    assert payload["actor"]["machineId"] == "machine-live"
    assert payload["actor"]["uniqueId"] == "unique-live"
    assert payload["source"]["osName"] == "mac"
    assert payload["source"]["osVersion"] == "26.5.1"
    assert payload["source"]["cpuArch"] == "arm64"
    assert payload["source"]["platformVersion"] == "2.2.2"
    assert payload["subject"]["id"] == ["machine-live"]
    assert payload["subject"]["targetWorkspaceId"] == "profile-live"
    assert payload["subject"]["sourceWorkspaceId"] == "gcid-live"


def test_normalize_ingress_payload_refreshes_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    sample = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "audit_validator/data/ingress_payloads/app_feedback_submitted.json"
        ).read_text()
    )
    old_cid = sample["xCorrelationId"]
    monkeypatch.setenv("INGRESS_MACHINE_ID", "TEST-MACHINE")
    monkeypatch.setenv("INGRESS_UNIQUE_ID", "TEST-UNIQUE")
    clear_ingress_runtime_context_cache()

    out = normalize_ingress_payload(sample, case_id="app_feedback_submitted")

    assert out["xCorrelationId"] != old_cid
    assert out["actor"]["machineId"] == "TEST-MACHINE"
    assert out["actor"]["uniqueId"] == "TEST-UNIQUE"
    assert out["source"]["osName"] == "mac"
    assert out["source"]["cpuArch"] in {"arm64", "x86_64"}
