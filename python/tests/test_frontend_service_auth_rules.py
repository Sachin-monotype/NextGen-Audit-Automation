"""Frontend service / auth-state / UA header expectations for app + GQL events."""

from __future__ import annotations

from audit_validator.source_validation.comparison_rows import (
    _expected_authentication_state,
    _expected_source_service,
    _normalize_app_ui_trigger_field,
)


ELECTRON_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) MonotypeNextGen/1.0.0 Chrome/144.0.7559.236 "
    "Electron/40.10.6 Safari/537.36"
)


def test_gql_frontend_service_is_mtconnect_api():
    enriched = {"source": {"service": "mtconnect-api", "operation": "activateVariation"}}
    assert _expected_source_service("activateVariation(global)(app)", enriched) == "mtconnect-api"
    assert _expected_source_service("bulkTagStyles(global)(app)", enriched) == "mtconnect-api"


def test_cron_service_not_forced_to_mtconnect_api():
    enriched = {"source": {"service": "audit-cron-service"}}
    assert _expected_source_service("quarterlyReportNotification(lmsopen)", enriched) is None


def test_app_shell_and_font_similar_use_mtconnect_ui():
    assert (
        _expected_source_service(
            "appCacheCleared(preferences)(app)",
            {"source": {"service": "mtconnect-ui"}},
        )
        == "mtconnect-ui"
    )
    assert (
        _expected_source_service(
            "fontSimilarViewed(global)",
            {"source": {"service": "mtconnect-ui"}},
        )
        == "mtconnect-ui"
    )
    assert (
        _expected_source_service(
            "fontPairsViewed(global)",
            {"source": {"service": "mtconnect-ui"}},
        )
        == "mtconnect-ui"
    )


def test_connect_login_keeps_connect_service():
    enriched = {"source": {"service": "MonotypeNextGenConnectService"}}
    assert (
        _expected_source_service("userLoginInitiatedApp(login)(app)", enriched)
        == "MonotypeNextGenConnectService"
    )


def test_auth_state_authenticated_when_gcid_and_guid():
    enriched = {
        "actor": {
            "globalCustomerId": "gcid-1",
            "globalUserId": "guid-1",
            "authenticationState": "anonymous",
        }
    }
    assert _expected_authentication_state(enriched) == "authenticated"


def test_auth_state_anonymous_without_ids():
    enriched = {"actor": {"authenticationState": "anonymous", "machineId": "m1"}}
    assert _expected_authentication_state(enriched) == "anonymous"


def test_normalize_picks_ua_and_platform_from_headers():
    trigger = {
        "operation": "activateVariation",
        "request_headers": {
            "User-Agent": ELECTRON_UA,
            "X-Unified-Version": "1.0.0",
        },
        "source": {"service": "mtconnect-api"},
    }
    enriched = {
        "source": {
            "service": "mtconnect-api",
            "platformVersion": "1.0.0",
            "actorUserAgent": ELECTRON_UA,
        },
        "actor": {
            "globalCustomerId": "gc",
            "globalUserId": "gu",
            "authenticationState": "authenticated",
        },
    }
    assert (
        _normalize_app_ui_trigger_field(
            "source.actorUserAgent",
            None,
            enriched=enriched,
            trigger=trigger,
            operation="activateVariation(global)(app)",
        )
        == ELECTRON_UA
    )
    assert (
        _normalize_app_ui_trigger_field(
            "source.platformVersion",
            "1.0.0.0",
            enriched=enriched,
            trigger=trigger,
            operation="activateVariation(global)(app)",
        )
        == "1.0.0"
    )
    assert (
        _normalize_app_ui_trigger_field(
            "source.service",
            "mtconnect-ui",
            enriched=enriched,
            trigger=trigger,
            operation="activateVariation(global)(app)",
        )
        == "mtconnect-api"
    )
    assert (
        _normalize_app_ui_trigger_field(
            "actor.authenticationState",
            "",
            enriched=enriched,
            trigger=trigger,
            operation="activateVariation(global)(app)",
        )
        == "authenticated"
    )
