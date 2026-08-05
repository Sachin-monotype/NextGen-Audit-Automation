#!/usr/bin/env python3
"""Dump Monotype Connect desktop app HTML and suggested XPath selectors via CDP.

Connect to a running desktop app started with --remote-debugging-port (default 9222),
then write page HTML and a selector inventory for desktop_navigation.json maintenance.

Usage:
  python scripts/inspect_desktop_html.py
  python scripts/inspect_desktop_html.py --port 9222 --out reports/desktop-html
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

from audit_validator.desktop.config import cdp_port  # noqa: E402


def _suggest_xpaths(page) -> list[dict]:
    """Collect interactive elements with data-testid, role, and XPath."""
    script = """
    () => {
      const out = [];
      const nodes = document.querySelectorAll(
        'button, a, input, select, textarea, [role="button"], [role="tab"], [data-testid]'
      );
      nodes.forEach((el, i) => {
        if (i > 500) return;
        const testId = el.getAttribute('data-testid') || '';
        const role = el.getAttribute('role') || el.tagName.toLowerCase();
        const label = (el.getAttribute('aria-label') || el.innerText || '').trim().slice(0, 80);
        let xpath = '';
        if (testId) xpath = `//*[@data-testid='${testId}']`;
        else if (label) xpath = `//${el.tagName.toLowerCase()}[contains(normalize-space(.),'${label.slice(0, 30).replace(/'/g, "")}')]`;
        out.push({ tag: el.tagName.toLowerCase(), testId, role, label, xpath });
      });
      return out;
    }
    """
    return page.evaluate(script)


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect desktop app HTML / XPath via CDP")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--out", type=Path, default=ROOT / "reports" / "desktop-html")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Install playwright: pip install playwright && playwright install chromium", file=sys.stderr)
        return 1

    port = args.port or cdp_port()
    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        if not browser.contexts:
            print(f"No browser contexts on port {port}", file=sys.stderr)
            return 1
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()
        html = page.content()
        (out_dir / "page.html").write_text(html, encoding="utf-8")
        selectors = _suggest_xpaths(page)
        (out_dir / "selectors.json").write_text(
            json.dumps(selectors, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        browser.close()

    print(f"Wrote {out_dir / 'page.html'} ({len(html)} bytes)")
    print(f"Wrote {out_dir / 'selectors.json'} ({len(selectors)} elements)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
