#!/usr/bin/env python3
"""Reset macOS ``mtfngpp://`` handler from CasePilot stock Electron to Monotype NextGen.

Run after CasePilot UI runs or whenever the browser shows "Open Electron?":

    python3 scripts/fix_mtfngpp_handler.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from audit_validator.macos_deeplink import (  # noqa: E402
    BAD_HANDLERS,
    MONOTYPE_BUNDLE_ID,
    SCHEME,
    current_mtfngpp_handler,
    restore_mtfngpp_handler,
)


def main() -> int:
    before = current_mtfngpp_handler()
    print(f"Current {SCHEME}:// handler: {before or 'unset'}")

    if not restore_mtfngpp_handler():
        print("ERROR: Monotype NextGen.app is not installed in /Applications.", file=sys.stderr)
        return 1

    after = current_mtfngpp_handler()
    print(f"Updated {SCHEME}:// handler: {after}")

    if str(after).lower() in BAD_HANDLERS:
        print(
            "WARNING: handler still points at Electron. Quit Chrome completely and retry, "
            "or log out/in once.",
            file=sys.stderr,
        )
        return 1

    print(f"Done — {SCHEME}:// should open {MONOTYPE_BUNDLE_ID} (Monotype Connect / NextGen).")
    print("Re-open your browser tab and click 'Open the desktop app' again.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
