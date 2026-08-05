"""HeadlessChrome / browser-version UA noise → PASS with note."""

from __future__ import annotations

from audit_validator.source_validation.comparison_rows import _row
from audit_validator.source_validation.mapping_registry import MappingField
from audit_validator.source_validation.value_match import (
    CLIENT_UA_NOISE_NOTE,
    user_agents_equivalent,
    values_equivalent,
)

HEADLESS = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) HeadlessChrome/151.0.7922.34 Safari/537.36"
)
CHROME = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)
DIFFERENT_OS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)


def test_headless_vs_chrome_version_equivalent():
    assert user_agents_equivalent(HEADLESS, CHROME)
    assert values_equivalent(HEADLESS, CHROME, field_path="source.actorUserAgent")


def test_different_os_not_equivalent():
    assert not user_agents_equivalent(HEADLESS, DIFFERENT_OS)


def test_actor_user_agent_row_passes_with_note():
    spec = MappingField(
        "source",
        "actorUserAgent",
        "",
        "",
        "",
        "",
        "Y",
        "source.actorUserAgent",
        "Trigger",
        "event trigger",
        "event",
    )
    row = _row(
        "createProject(project)",
        spec,
        CHROME,
        HEADLESS,
        notes="GraphQL curl / event trigger",
        live={},
    )
    assert row.match_status == "PASS"
    assert row.value_in_enriched == HEADLESS
    assert row.value_in_source == CHROME
    assert row.notes == CLIENT_UA_NOISE_NOTE


def test_chrome_version_only_diff_passes():
    a = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
    )
    b = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
    )
    assert user_agents_equivalent(a, b)
    spec = MappingField(
        "source",
        "actorUserAgent",
        "",
        "",
        "",
        "",
        "Y",
        "source.actorUserAgent",
        "Trigger",
        "event trigger",
        "event",
    )
    row = _row("getAllAccessRequests(global)", spec, b, a, notes="", live={})
    assert row.match_status == "PASS"
    assert row.notes == CLIENT_UA_NOISE_NOTE
    assert row.value_in_enriched == a
    assert row.value_in_source == b


def test_store_cleanup_upgrades_skip_ua_noise():
    from backend.app.comparison_store import _clean_benign_client_ua_rows

    a = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
    )
    b = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
    )
    rows = [
        {
            "field_path": "source.actorUserAgent",
            "match_status": "SKIP",
            "value_in_enriched": a,
            "value_in_source": b,
            "notes": "",
        }
    ]
    cleaned, changed = _clean_benign_client_ua_rows(rows)
    assert changed
    assert cleaned[0]["match_status"] == "PASS"
    assert cleaned[0]["notes"] == CLIENT_UA_NOISE_NOTE
    assert cleaned[0]["value_in_enriched"] == a
    assert cleaned[0]["value_in_source"] == b
