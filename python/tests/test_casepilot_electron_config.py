"""Tests for CasePilot Electron ui_config."""

from __future__ import annotations

from audit_validator.casepilot_mcp import (
    CasePilotConfig,
    build_ui_config,
    selection_uses_electron_app,
)


def _cfg(**kwargs) -> CasePilotConfig:
    base = CasePilotConfig(
        api_key="cp_test",
        ui_username="user@example.com",
        ui_password="secret",
        ui_base_url="https://nextgen.example.com",
        electron_app_path="/Applications/Monotype Connect.app",
    )
    for k, v in kwargs.items():
        setattr(base, k, v)
    return base


def test_selection_uses_electron_app() -> None:
    assert selection_uses_electron_app(
        [{"id": "ingress:app_settings_auto_performance_enabled", "touchpoint": "Desktop App"}]
    )
    assert not selection_uses_electron_app(
        [{"id": "ingress:plugin_panel_opened", "touchpoint": "Plugin"}]
    )
    assert not selection_uses_electron_app(
        [{"id": "activateFamily", "touchpoint": "global"}]
    )


def test_build_ui_config_electron_launch() -> None:
    cfg = _cfg()
    selection = [{"id": "ingress:app_cache_cleared", "touchpoint": "Desktop App"}]
    out = build_ui_config(cfg, selection=selection, extra={"headless": False})
    assert out["app_type"] == "electron"
    assert out["electron_attach_mode"] == "launch"
    assert out["electron_app_path"] == "/Applications/Monotype Connect.app"
    assert out["electron_auto_login"] is True
    assert out["electron_quit_existing_before_launch"] is True
    assert out["electron_use_open_on_macos"] is True
    assert out["electron_debug_port"] == 9222
    assert out["username"] == "user@example.com"
    assert out["password"] == "secret"
    assert out["headless"] is False
    assert "base_url" not in out


def test_build_ui_config_electron_attach() -> None:
    cfg = _cfg(electron_attach_mode="attach")
    out = build_ui_config(
        cfg,
        selection=[{"id": "ingress:app_logs_exported", "touchpoint": "Desktop App"}],
    )
    assert out["electron_attach_mode"] == "attach"
    assert out["electron_debug_port"] == 9222
    assert "electron_app_path" not in out


def test_build_ui_config_web() -> None:
    cfg = _cfg()
    out = build_ui_config(
        cfg,
        selection=[{"id": "activateFamily", "operation": "activateFamily", "touchpoint": "global"}],
        base_url="https://nextgen.monotype-pp.com",
    )
    assert out["app_type"] == "web"
    assert out["base_url"] == "https://nextgen.monotype-pp.com"
    assert out["browser"] == "chrome"


def test_ui_config_ready_electron(monkeypatch) -> None:
    cfg = _cfg()
    sel = [{"id": "ingress:app_language_changed", "touchpoint": "Desktop App"}]
    assert cfg.ui_config_ready(selection=sel)

    monkeypatch.setattr(
        "audit_validator.casepilot_mcp.discover_electron_app_path",
        lambda: "",
    )
    cfg_bad = _cfg(electron_app_path="")
    assert not cfg_bad.ui_config_ready(selection=sel)
