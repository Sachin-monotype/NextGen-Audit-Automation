"""platformEnvironment from actorUserAgent; skip invented Excel Chrome UA."""

from __future__ import annotations

from audit_validator.simulation.trigger_context import (
    DEFAULT_WEB_USER_AGENT,
    build_trigger_context,
    platform_environment_from_user_agent,
)
from audit_validator.source_validation.comparison_rows import _resolve_client_fingerprint
from audit_validator.source_validation.mapping_registry import MappingField
from audit_validator.source_validation.comparison_rows import _row


ELECTRON_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) MonotypeNextGen/1.0.0 Chrome/144.0.7559.236 "
    "Electron/40.10.6 Safari/537.36"
)


def test_platform_environment_from_user_agent():
    assert platform_environment_from_user_agent(ELECTRON_UA) == "app"
    assert platform_environment_from_user_agent("MonotypeNextGen/1.0.0.0") == "app"
    assert platform_environment_from_user_agent(DEFAULT_WEB_USER_AGENT) == "web"
    assert platform_environment_from_user_agent("") is None


def test_excel_trigger_does_not_invent_chrome_web():
    ctx = build_trigger_context(
        operation="activateFamily",
        correlation_id="cid-1",
        invent_client_defaults=False,
    )
    assert not (ctx.get("source") or {}).get("actorUserAgent")
    assert not (ctx.get("source") or {}).get("platformEnvironment")
    assert ctx.get("ua_captured") is False


def test_platform_environment_derived_from_enriched_electron_ua():
    enriched = {"source": {"actorUserAgent": ELECTRON_UA, "platformEnvironment": "app"}}
    # Stale Excel trigger still has invented web+Chrome — must not win.
    live = {
        "trigger": {
            "capture_source": "playwright_script",
            "replay_mode": "playwright_script",
            "source": {
                "platformEnvironment": "web",
                "actorUserAgent": DEFAULT_WEB_USER_AGENT,
            },
            "request": {
                "platformEnvironment": "web",
                "userAgent": DEFAULT_WEB_USER_AGENT,
            },
        }
    }
    sv, note = _resolve_client_fingerprint("source.platformEnvironment", enriched, live)
    assert sv == "app"
    assert "actorUserAgent" in note


def test_actor_user_agent_skipped_when_excel_invented_chrome():
    enriched = {"source": {"actorUserAgent": ELECTRON_UA, "platformEnvironment": "app"}}
    live = {
        "trigger": {
            "capture_source": "playwright_script",
            "replay_mode": "playwright_script",
            "jwt_from_excel": True,
            "source": {"actorUserAgent": DEFAULT_WEB_USER_AGENT},
            "request": {"userAgent": DEFAULT_WEB_USER_AGENT},
        }
    }
    sv, note = _resolve_client_fingerprint("source.actorUserAgent", enriched, live)
    assert sv is None
    assert "skip" in note.lower()

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
    row = _row("activateFamily(favourite)(app)", spec, sv, ELECTRON_UA, notes=note, live=live)
    assert row.match_status == "SKIP"


def test_platform_environment_pass_when_derived_matches_enriched():
    enriched_val = "app"
    sv, note = _resolve_client_fingerprint(
        "source.platformEnvironment",
        {"source": {"actorUserAgent": ELECTRON_UA, "platformEnvironment": "app"}},
        {"trigger": {"capture_source": "playwright_script", "replay_mode": "playwright_script"}},
    )
    spec = MappingField(
        "source",
        "platformEnvironment",
        "",
        "",
        "",
        "",
        "Y",
        "source.platformEnvironment",
        "Trigger",
        "event trigger",
        "event",
    )
    row = _row(
        "activateFamily(favourite)(app)",
        spec,
        sv,
        enriched_val,
        notes=note,
        live={},
    )
    assert row.match_status == "PASS"
    assert row.value_in_source == "app"
