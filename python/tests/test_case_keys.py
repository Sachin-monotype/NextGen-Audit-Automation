"""Tests for cron/ingress case key helpers."""

from __future__ import annotations

from audit_validator.case_keys import (
    cron_case_key,
    cron_display_operation,
    cron_staging_stem,
    mapping_lookup_variants,
    parse_display_operation,
)


def test_cron_display_and_stem():
    assert cron_display_operation("quarterlyReportNotification", "lmsopen") == (
        "quarterlyReportNotification(lmsopen)"
    )
    assert cron_staging_stem("quarterlyReportNotification", "lmsopen") == (
        "quarterlyReportNotification__lmsopen"
    )


def test_parse_display_operation_roundtrip():
    label = cron_display_operation("exportCompleted", "exportcomplete")
    base, case = parse_display_operation(label)
    assert base == "exportCompleted"
    assert case == "exportcomplete"


def test_cron_case_key_prefix():
    assert cron_case_key("lmsopen") == "cron:lmsopen"
    assert cron_case_key("cron:lmsopen") == "cron:lmsopen"


def test_mapping_lookup_variants_peels_app_channel():
    assert mapping_lookup_variants("activateFamily(default)(app)") == [
        "activateFamily(default)(app)",
        "activateFamily(default)",
        "activateFamily",
    ]
    assert mapping_lookup_variants("bulkCopyAssets(global)(app)") == [
        "bulkCopyAssets(global)(app)",
        "bulkCopyAssets(global)",
        "bulkCopyAssets",
    ]
    assert mapping_lookup_variants("activateFamily(global)") == [
        "activateFamily(global)",
        "activateFamily",
    ]
