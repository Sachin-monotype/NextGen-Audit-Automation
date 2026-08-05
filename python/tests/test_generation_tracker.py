"""Tests for per-case correlation tracking."""

from __future__ import annotations

import json
from pathlib import Path

from audit_validator.case_keys import cron_case_key
from audit_validator.generation_tracker import (
    get_owned_correlation,
    lookup_by_correlation,
    record_generation,
)


def test_record_generation_by_case(tmp_path: Path):
    store = tmp_path / "reports" / "generated-correlations.json"
    record_generation(
        "quarterlyReportNotification",
        "cid-lmsopen",
        project_root=tmp_path,
        kind="cron",
        case_key=cron_case_key("lmsopen"),
        meta={"case_id": "lmsopen"},
    )
    record_generation(
        "quarterlyReportNotification",
        "cid-lmsclose",
        project_root=tmp_path,
        kind="cron",
        case_key=cron_case_key("lmsclose"),
        meta={"case_id": "lmsclose"},
    )
    assert get_owned_correlation(
        "quarterlyReportNotification",
        project_root=tmp_path,
        case_key=cron_case_key("lmsopen"),
    ) == "cid-lmsopen"
    assert get_owned_correlation(
        "quarterlyReportNotification",
        project_root=tmp_path,
        case_key=cron_case_key("lmsclose"),
    ) == "cid-lmsclose"
    hit = lookup_by_correlation("cid-lmsopen", project_root=tmp_path)
    assert hit and hit.get("case_id") == "lmsopen"
    data = json.loads(store.read_text(encoding="utf-8"))
    assert "by_case" in data
    assert "by_correlation" in data
