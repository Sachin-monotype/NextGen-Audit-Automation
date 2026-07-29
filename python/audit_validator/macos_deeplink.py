"""macOS ``mtfngpp://`` handler helpers for Monotype Connect / NextGen."""

from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

MONOTYPE_NEXTGEN = Path("/Applications/Monotype NextGen/Monotype NextGen.app")
MONOTYPE_BUNDLE_ID = "com.monotype.unified"
CASEPILOT_ELECTRON = (
    Path.home() / ".casepilot" / "electron-cdp" / "electron" / "Electron.app"
)
LSREGISTER = (
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
    "LaunchServices.framework/Support/lsregister"
)
LS_PREFS = (
    Path.home()
    / "Library/Preferences/com.apple.LaunchServices/com.apple.launchservices.secure.plist"
)
SCHEME = "mtfngpp"
BAD_HANDLERS = {"com.github.electron", "com.github.Electron", "electron"}


def _read_handlers() -> list[dict]:
    if not LS_PREFS.is_file():
        return []
    with LS_PREFS.open("rb") as fh:
        data = plistlib.load(fh)
    handlers = data.get("LSHandlers")
    return list(handlers) if isinstance(handlers, list) else []


def _write_handlers(handlers: list[dict]) -> None:
    LS_PREFS.parent.mkdir(parents=True, exist_ok=True)
    if LS_PREFS.is_file():
        with LS_PREFS.open("rb") as fh:
            data = plistlib.load(fh)
    else:
        data = {}
    data["LSHandlers"] = handlers
    with LS_PREFS.open("wb") as fh:
        plistlib.dump(data, fh)


def current_mtfngpp_handler() -> str | None:
  handlers = _read_handlers()
  for item in handlers:
    if item.get("LSHandlerURLScheme") == SCHEME:
      return str(item.get("LSHandlerRoleAll") or "").strip() or None
  return None


def restore_mtfngpp_handler(*, quiet: bool = False) -> bool:
    """Point ``mtfngpp://`` at Monotype NextGen instead of CasePilot stock Electron."""
    if not MONOTYPE_NEXTGEN.is_dir():
        if not quiet:
            print("restore_mtfngpp_handler: Monotype NextGen.app not installed")
        return False

    current = (current_mtfngpp_handler() or "").lower()
    if current == MONOTYPE_BUNDLE_ID.lower():
        return True

    if CASEPILOT_ELECTRON.is_dir():
        subprocess.run(
            [LSREGISTER, "-u", str(CASEPILOT_ELECTRON)],
            check=False,
            capture_output=True,
            text=True,
        )

    subprocess.run(
        [LSREGISTER, "-f", str(MONOTYPE_NEXTGEN)],
        check=False,
        capture_output=True,
        text=True,
    )

    handlers = _read_handlers()
    kept = [h for h in handlers if h.get("LSHandlerURLScheme") != SCHEME]
    kept.append(
        {
            "LSHandlerURLScheme": SCHEME,
            "LSHandlerRoleAll": MONOTYPE_BUNDLE_ID,
        }
    )
    _write_handlers(kept)

    subprocess.run(
        [LSREGISTER, "-kill", "-r", "-domain", "local", "-domain", "system", "-domain", "user"],
        check=False,
        capture_output=True,
        text=True,
    )
    return True
