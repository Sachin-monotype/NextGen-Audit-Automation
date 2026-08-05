"""Desktop automation paths and defaults."""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

TARGET_URL = (
    os.getenv("INGRESS_API_URL") or "https://mt-audit-log-resolver-service-preprod.monotype-pp.com/v1/audit-events"
).strip()


def ingress_target_url() -> str:
    """Resolver URL for the active AUDIT_TARGET (re-reads env after profile apply)."""
    return (
        os.getenv("INGRESS_API_URL") or TARGET_URL
    ).strip()


def is_audit_ingress_curl(line_or_curl: str) -> bool:
    """True when CurlDebug targets any audit-events resolver (QA / PP / UAT)."""
    text = line_or_curl or ""
    if "audit-events" in text:
        return True
    url = ingress_target_url()
    return bool(url) and url in text

_DEFAULT_LOG_DIRS: tuple[Path, ...] = (
    Path(
        r"C:\Users\Dell\AppData\Local\Monotype\Monotype Connect\Logs\ConnectService\service"
    ),
    Path.home()
    / "Library"
    / "Logs"
    / "Monotype"
    / "Monotype Connect"
    / "ConnectService"
    / "service",
)

_MACOS_APP_CANDIDATES: tuple[Path, ...] = (
    Path("/Applications/Monotype NextGen/Monotype NextGen.app"),
    Path("/Applications/Monotype Connect.app"),
    Path("/Applications/Monotype/Monotype Connect.app"),
)

_WINDOWS_APP_CANDIDATES: tuple[Path, ...] = (
    Path(r"C:\Program Files\Monotype\Monotype Connect\Monotype Connect.exe"),
    Path(r"C:\Program Files\Monotype NextGen\Monotype NextGen.exe"),
)


def _env_path(*keys: str) -> Path | None:
    for key in keys:
        raw = (os.getenv(key) or "").strip()
        if raw:
            return Path(raw).expanduser()
    return None


def _macos_executable_in_bundle(bundle: Path) -> Path | None:
    macos_dir = bundle / "Contents" / "MacOS"
    if not macos_dir.is_dir():
        return None
    exes = sorted(
        p for p in macos_dir.iterdir() if p.is_file() and os.access(p, os.X_OK)
    )
    if not exes:
        return None
    stem = bundle.stem.replace(" ", "")
    for exe in exes:
        if exe.stem.replace(" ", "") == stem.replace(" ", ""):
            return exe
    return exes[0]


def resolve_desktop_executable(raw: Path) -> Path | None:
    """Resolve a .app bundle, directory, or direct binary path to an executable."""
    if raw.is_file() and os.access(raw, os.X_OK):
        return raw
    if raw.suffix == ".app" or raw.name.endswith(".app"):
        return _macos_executable_in_bundle(raw)
    if sys.platform == "darwin":
        app = raw if raw.suffix == ".app" else raw.with_suffix(".app")
        if app.is_dir():
            return _macos_executable_in_bundle(app)
    return None


def desktop_app_path() -> Path | None:
    """Return the Monotype desktop executable, or None if not found."""
    candidates: list[Path] = []
    for path in (
        _env_path("DESKTOP_APP_PATH", "MONOTYPE_CONNECT_APP", "CASEPILOT_ELECTRON_APP_PATH"),
    ):
        if path is not None:
            candidates.append(path)

    if sys.platform == "darwin":
        candidates.extend(_MACOS_APP_CANDIDATES)
    elif sys.platform.startswith("win"):
        candidates.extend(_WINDOWS_APP_CANDIDATES)

    seen: set[Path] = set()
    for raw in candidates:
        key = raw.resolve() if raw.exists() else raw
        if key in seen:
            continue
        seen.add(key)
        resolved = resolve_desktop_executable(raw)
        if resolved is not None:
            return resolved
    return None


def default_log_dir() -> Path:
    env = (os.getenv("LOGS_PATH") or os.getenv("CONNECT_SERVICE_LOG_DIR") or "").strip()
    if env:
        return Path(env).expanduser()
    for candidate in _DEFAULT_LOG_DIRS:
        if candidate.is_dir():
            return candidate
    return _DEFAULT_LOG_DIRS[0 if sys.platform.startswith("win") else 1]


def cdp_port() -> int:
    raw = (
        os.getenv("DESKTOP_CDP_PORT")
        or os.getenv("CASEPILOT_ELECTRON_DEBUG_PORT")
        or "9222"
    )
    try:
        return int(raw)
    except ValueError:
        return 9222


def electron_attach_mode() -> str:
    """``launch`` (default) or ``attach`` — mirrors CasePilot electron settings."""
    return (os.getenv("DESKTOP_ATTACH_MODE") or os.getenv("CASEPILOT_ELECTRON_ATTACH_MODE") or "launch").strip().lower()


def use_open_on_macos() -> bool:
    raw = (os.getenv("DESKTOP_USE_OPEN_ON_MACOS") or os.getenv("CASEPILOT_ELECTRON_USE_OPEN_ON_MACOS") or "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def quit_existing_before_launch() -> bool:
    raw = (
        os.getenv("DESKTOP_QUIT_EXISTING")
        or os.getenv("CASEPILOT_ELECTRON_QUIT_EXISTING_BEFORE_LAUNCH")
        or "true"
    ).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def desktop_app_bundle() -> Path | None:
    """Return the .app bundle path when available."""
    for raw in (
        _env_path("DESKTOP_APP_PATH", "MONOTYPE_CONNECT_APP", "CASEPILOT_ELECTRON_APP_PATH"),
    ):
        if raw is None:
            continue
        if raw.suffix == ".app" or raw.name.endswith(".app"):
            return raw if raw.is_dir() else None
        if raw.is_file():
            parent = raw.parent
            while parent != parent.parent:
                if parent.suffix == ".app":
                    return parent
                parent = parent.parent
    exe = desktop_app_path()
    if exe:
        parent = exe.parent
        while parent != parent.parent:
            if parent.suffix == ".app":
                return parent
            parent = parent.parent
    if sys.platform == "darwin":
        for candidate in _MACOS_APP_CANDIDATES:
            if candidate.is_dir():
                return candidate
    return None


def today_local() -> date:
    return date.today()
