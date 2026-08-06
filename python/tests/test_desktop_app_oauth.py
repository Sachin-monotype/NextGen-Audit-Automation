"""Smoke tests for desktop OAuth helpers (no live Electron)."""

from __future__ import annotations

from audit_validator.desktop.app_oauth import _AUTH_URL_RE, _normalize_deeplink
from audit_validator.desktop.navigation import DesktopEvent, UiStep
from audit_validator.desktop.ui_runner import _AUTH_OPS, _sort_auth_events


def test_authorization_url_regex():
    sample = r'AuthorizationUrl: {"AuthorizationUrl":"https:\/\/secure.example\/authorize?x=1"}'
    # Also the JSON form used in console capture
    sample2 = '"AuthorizationUrl": "https:\\/\\/secure.example\\/authorize?x=1"'
    m = _AUTH_URL_RE.search(sample2)
    assert m is not None
    assert "secure.example" in m.group(1).replace("\\/", "/")


def test_normalize_deeplink_unescapes_and_keeps_triple_slash():
    raw = "mtfngpp:///auth?code=ABC&amp;state=eyJ4"
    out = _normalize_deeplink(raw)
    assert out.startswith("mtfngpp:///auth")
    assert "&state=" in out
    assert "&amp;" not in out


def test_sort_auth_events_order():
    def ev(op: str) -> DesktopEvent:
        return DesktopEvent(
            case_id=op,
            event_name=op,
            operation=op,
            category="login" if op in _AUTH_OPS else "other",
            navigation=[],
            trigger_hint="",
            automatable=True,
            steps=[UiStep(action="app_oauth_x", selector="", xpath="", description="", value="")],
            remarks="",
        )

    mixed = [
        ev("appCacheCleared"),
        ev("userLogoutApp"),
        ev("identityLinked"),
        ev("userLoginFailureApp"),
        ev("userSwitchWorkspaceApp"),
        ev("userLoginInitiatedApp"),
    ]
    ordered = _sort_auth_events(mixed)
    ops = [e.operation for e in ordered]
    assert ops == [
        "userLoginFailureApp",
        "userLoginInitiatedApp",
        "identityLinked",
        "userSwitchWorkspaceApp",
        "appCacheCleared",
        "userLogoutApp",
    ]
