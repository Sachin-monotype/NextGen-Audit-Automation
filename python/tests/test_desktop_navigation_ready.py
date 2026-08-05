"""Desktop navigation catalog readiness for all 23 events."""

from __future__ import annotations

from audit_validator.desktop.navigation import load_desktop_events


def test_all_desktop_events_have_steps() -> None:
    events = load_desktop_events(automatable_only=False)
    assert len(events) >= 22, f"expected ~23 catalog rows, got {len(events)}"
    missing = [e.operation for e in events if not e.steps]
    assert not missing, f"events missing steps: {missing}"
    non_auto = [e.operation for e in events if not e.automatable]
    assert not non_auto, f"expected all events automatable for suite readiness: {non_auto}"


def test_desktop_ops_cover_core_preferences_and_help() -> None:
    ops = {e.operation for e in load_desktop_events()}
    required = {
        "appSettingsAutoPerformanceEnabled",
        "appSettingsAutoPerformanceDisabled",
        "appSettingsPerformanceModeChanged",
        "appLanguageChanged",
        "appSettingsPluginInstallAllEnabled",
        "appSettingsPluginInstallAllDisabled",
        "appSettingsPluginAppEnabled",
        "appSettingsActivationModeChanged",
        "appFeedbackSubmitted",
        "appLogsExported",
        "appNetworkRefreshed",
        "appCacheCleared",
        "fontTempActivated",
        "fontActivationTypeSwitched",
        "fontLocalfontActivated",
        "fontLocalfontDeactivated",
        "fontSyncSuccess",
        "userSwitchWorkspaceApp",
        "userLogoutApp",
        "userLoginFailureApp",
        "userLoginInitiatedApp",
        "identityLinked",
    }
    missing = required - ops
    assert not missing, f"catalog missing operations: {missing}"
