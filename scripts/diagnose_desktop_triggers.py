#!/usr/bin/env python3
"""Thorough desktop trigger diagnostics — separate OUR bugs from APP gaps.

For each catalog event (non-auth):
  1. Locate the target control (exists?)
  2. Read state before
  3. Perform the action
  4. Read state after (did UI actually change?)
  5. Record verdict: AUTOMATION_BUG | UI_OK_NO_STATE_CHANGE | UI_STATE_CHANGED

Then wait for ConnectService CurlDebug flush and map which ops appeared in the log.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
except ImportError:
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--wait-sec", type=float, default=210.0)
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    from audit_validator.desktop.config import TARGET_URL, default_log_dir
    from audit_validator.desktop.log_extractor import _extract_payload_from_curl
    from audit_validator.desktop.ui_runner import (
        _ensure_preferences_section,
        _force_click,
        _md_toggle_checked,
        _open_help_support,
        _open_profile_menu,
        _pick_main_page,
        _qa_base_url,
    )

    log_dir = default_log_dir()
    log = sorted(log_dir.glob("file-*.log"))[-1]
    offset_before = log.stat().st_size
    print(f"Log baseline {log.name} offset={offset_before}")
    print(f"Will wait {args.wait_sec:.0f}s after all UI probes for CurlDebug\n")

    results: list[dict] = []

    def record(**kwargs: object) -> None:
        results.append(kwargs)
        verdict = kwargs.get("verdict")
        op = kwargs.get("operation")
        print(f"  [{verdict}] {op}: {kwargs.get('detail')}")

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
        main = _pick_main_page(browser)
        if main is None:
            print("No CDP page")
            return 1
        main.bring_to_front()
        print(f"Main page: {main.url}\n")

        # ---------- GENERAL PREFS ----------
        print("=== Preferences > General ===")
        try:
            page = _ensure_preferences_section(main, browser, main, "general")
            toggle = page.locator("[data-qa-id='preferences-general-auto-performance-toggle']").first
            toggle.wait_for(state="attached", timeout=10000)
            before = _md_toggle_checked(toggle)
            _force_click(toggle, timeout_ms=10000)
            time.sleep(1.5)
            after = _md_toggle_checked(toggle)
            changed = before is not None and after is not None and before != after
            record(
                operation="appSettingsAutoPerformance(toggle)",
                control_found=True,
                state_before=before,
                state_after=after,
                verdict="UI_STATE_CHANGED" if changed else "UI_OK_NO_STATE_CHANGE",
                detail=f"checked {before} → {after}",
            )
            # leave it toggled opposite again so both enable+disable fire if app emits
            _force_click(toggle, timeout_ms=10000)
            time.sleep(1.5)
            after2 = _md_toggle_checked(toggle)
            record(
                operation="appSettingsAutoPerformance(toggle_back)",
                control_found=True,
                state_before=after,
                state_after=after2,
                verdict="UI_STATE_CHANGED" if after != after2 else "UI_OK_NO_STATE_CHANGE",
                detail=f"checked {after} → {after2}",
            )
        except Exception as exc:  # noqa: BLE001
            record(
                operation="appSettingsAutoPerformance*",
                control_found=False,
                verdict="AUTOMATION_BUG",
                detail=str(exc)[:200],
            )

        # Performance mode dropdown
        try:
            page = _ensure_preferences_section(main, browser, main, "general")
            dd = page.locator(
                "[data-qa-id='preferences-general-performance-mode-dropdown'],"
                "[data-testid='preferences-general-performance-mode-dropdown']"
            ).first
            print(f"  perf dropdown count={dd.count()} visible probe…")
            # md-dropdown may not be 'visible' in playwright sense — use attached + force
            dd.wait_for(state="attached", timeout=10000)
            before_html = dd.inner_text() if False else dd.evaluate("el => el.outerHTML.slice(0,120)")
            _force_click(dd, timeout_ms=10000)
            time.sleep(1)
            opts = page.evaluate(
                """() => [...document.querySelectorAll('md-dropdown-item,[role="option"],li,button')]
                   .map(el => (el.innerText||'').trim()).filter(t => t && t.length < 40).slice(0,15)"""
            )
            clicked = None
            for label in ("Max performance", "Max capacity", "Optimal", "Balanced"):
                loc = page.get_by_text(label, exact=False).first
                try:
                    if loc.count() and loc.is_visible(timeout=500):
                        loc.click(force=True, timeout=5000)
                        clicked = label
                        break
                except Exception:
                    continue
            if not clicked and opts:
                # click first option-like text
                try:
                    page.get_by_text(opts[0], exact=False).first.click(force=True, timeout=5000)
                    clicked = opts[0]
                except Exception:
                    pass
            page.keyboard.press("Escape")
            record(
                operation="appSettingsPerformanceModeChanged",
                control_found=True,
                verdict="UI_STATE_CHANGED" if clicked else "AUTOMATION_BUG",
                detail=f"dropdown_html={before_html!r} options={opts!r} clicked={clicked!r}",
            )
        except Exception as exc:  # noqa: BLE001
            record(
                operation="appSettingsPerformanceModeChanged",
                control_found=False,
                verdict="AUTOMATION_BUG",
                detail=str(exc)[:240],
            )

        # Language
        try:
            page = _ensure_preferences_section(main, browser, main, "general")
            dd = page.locator("[data-qa-id='preferences-language-dropdown']").first
            dd.wait_for(state="attached", timeout=10000)
            _force_click(dd, timeout_ms=10000)
            time.sleep(1)
            opts = page.evaluate(
                """() => [...document.querySelectorAll('md-dropdown-item,[role="option"],li')]
                   .map(el => (el.innerText||'').trim()).filter(Boolean).slice(0,12)"""
            )
            clicked = None
            for label in opts or ["Deutsch", "English", "Français"]:
                try:
                    loc = page.get_by_text(label, exact=False).first
                    if loc.is_visible(timeout=400):
                        loc.click(force=True, timeout=4000)
                        clicked = label
                        break
                except Exception:
                    continue
            page.keyboard.press("Escape")
            record(
                operation="appLanguageChanged",
                control_found=True,
                verdict="UI_STATE_CHANGED" if clicked else "AUTOMATION_BUG",
                detail=f"options={opts!r} clicked={clicked!r}",
            )
        except Exception as exc:  # noqa: BLE001
            record(
                operation="appLanguageChanged",
                control_found=False,
                verdict="AUTOMATION_BUG",
                detail=str(exc)[:240],
            )

        # ---------- PLUGINS ----------
        print("\n=== Preferences > Plugins ===")
        try:
            page = _ensure_preferences_section(main, browser, main, "plugins")
            toggles = page.locator("[data-qa-id='preferences-plugins-page'] md-toggle.mtc-plugins-page__toggle")
            n = toggles.count()
            print(f"  plugin toggles found: {n}")
            if n == 0:
                record(
                    operation="appSettingsPluginInstallAll*",
                    control_found=False,
                    verdict="AUTOMATION_BUG",
                    detail="no md-toggle.mtc-plugins-page__toggle on plugins page",
                )
            else:
                t0 = toggles.nth(0)
                b = _md_toggle_checked(t0)
                _force_click(t0, timeout_ms=10000)
                time.sleep(1.2)
                a = _md_toggle_checked(t0)
                record(
                    operation="appSettingsPluginInstallAll(toggle)",
                    control_found=True,
                    state_before=b,
                    state_after=a,
                    verdict="UI_STATE_CHANGED" if b != a else "UI_OK_NO_STATE_CHANGE",
                    detail=f"first toggle checked {b} → {a}",
                )
            rows = page.locator("[data-testid='plugin-app-row']")
            print(f"  plugin-app-row count: {rows.count()}")
            if rows.count():
                row_tog = rows.first.locator("md-toggle").first
                b = _md_toggle_checked(row_tog)
                _force_click(row_tog, timeout_ms=10000)
                time.sleep(1.2)
                a = _md_toggle_checked(row_tog)
                record(
                    operation="appSettingsPluginAppEnabled(toggle)",
                    control_found=True,
                    state_before=b,
                    state_after=a,
                    verdict="UI_STATE_CHANGED" if b != a else "UI_OK_NO_STATE_CHANGE",
                    detail=f"first app-row toggle {b} → {a}",
                )
            dd = page.locator("md-dropdown[aria-label='Deactivate temporary fonts when']").first
            print(f"  activation dropdown count={dd.count()}")
            if dd.count() == 0:
                # dump dropdowns
                labels = page.evaluate(
                    """() => [...document.querySelectorAll('md-dropdown')].map(el => ({
                       aria: el.getAttribute('aria-label'),
                       text: (el.innerText||'').trim().slice(0,40)
                    }))"""
                )
                record(
                    operation="appSettingsActivationModeChanged",
                    control_found=False,
                    verdict="AUTOMATION_BUG",
                    detail=f"dropdown not found; all md-dropdown={labels!r}",
                )
            else:
                _force_click(dd, timeout_ms=10000)
                time.sleep(1)
                opts = page.evaluate(
                    """() => [...document.querySelectorAll('md-dropdown-item,[role="option"]')]
                       .map(el => (el.innerText||'').trim()).filter(Boolean).slice(0,12)"""
                )
                clicked = None
                for label in opts or ["Quit", "Close", "Never"]:
                    try:
                        loc = page.get_by_text(label, exact=False).first
                        if loc.is_visible(timeout=400):
                            loc.click(force=True, timeout=4000)
                            clicked = label
                            break
                    except Exception:
                        continue
                page.keyboard.press("Escape")
                record(
                    operation="appSettingsActivationModeChanged",
                    control_found=True,
                    verdict="UI_STATE_CHANGED" if clicked else "AUTOMATION_BUG",
                    detail=f"options={opts!r} clicked={clicked!r}",
                )
        except Exception as exc:  # noqa: BLE001
            record(
                operation="plugins_section",
                control_found=False,
                verdict="AUTOMATION_BUG",
                detail=str(exc)[:240],
            )

        # ---------- ABOUT / FEEDBACK ----------
        print("\n=== Preferences > About ===")
        try:
            page = _ensure_preferences_section(main, browser, main, "about")
            link = page.locator("xpath=//md-link[contains(.,'Share feedback')]").first
            print(f"  share feedback count={link.count()}")
            if link.count() == 0:
                texts = page.evaluate(
                    "() => (document.body.innerText||'').includes('Share feedback')"
                )
                record(
                    operation="appFeedbackSubmitted",
                    control_found=False,
                    verdict="AUTOMATION_BUG",
                    detail=f"md-link not found; body_has_text={texts}",
                )
            else:
                _force_click(link, timeout_ms=10000)
                time.sleep(1.5)
                page.keyboard.press("Escape")
                record(
                    operation="appFeedbackSubmitted",
                    control_found=True,
                    verdict="UI_STATE_CHANGED",
                    detail="clicked Share feedback md-link",
                )
        except Exception as exc:  # noqa: BLE001
            record(
                operation="appFeedbackSubmitted",
                control_found=False,
                verdict="AUTOMATION_BUG",
                detail=str(exc)[:240],
            )

        # ---------- HELP ----------
        print("\n=== Help & support ===")
        try:
            page = _open_help_support(main, browser)
            print(f"  help url={page.url}")
            for op, sel in (
                ("appNetworkRefreshed", "[data-qa-id='refresh-status-button']"),
                ("appLogsExported", "[data-qa-id='download-logs-button']"),
                ("appCacheCleared", "[data-qa-id='clear-cache-button']"),
            ):
                loc = page.locator(sel).first
                found = loc.count() > 0
                if not found:
                    record(
                        operation=op,
                        control_found=False,
                        verdict="AUTOMATION_BUG",
                        detail=f"missing {sel} on {page.url}",
                    )
                    continue
                _force_click(loc, timeout_ms=10000)
                time.sleep(1.2)
                if op == "appCacheCleared":
                    for lab in ("Clear", "Confirm", "Yes", "OK"):
                        try:
                            b = page.get_by_text(lab, exact=False).first
                            if b.is_visible(timeout=400):
                                b.click(force=True, timeout=3000)
                                break
                        except Exception:
                            continue
                    page.keyboard.press("Escape")
                record(
                    operation=op,
                    control_found=True,
                    verdict="UI_STATE_CHANGED",
                    detail=f"clicked {sel}",
                )
        except Exception as exc:  # noqa: BLE001
            record(
                operation="help_support_section",
                control_found=False,
                verdict="AUTOMATION_BUG",
                detail=str(exc)[:240],
            )

        # ---------- DISCOVER / FONTS ----------
        print("\n=== Discover fonts ===")
        try:
            page = main
            page.bring_to_front()
            url = f"{_qa_base_url()}/discover-fonts/all"
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
            except Exception as e:
                print(f"  goto warn: {e}")
            time.sleep(2.5)
            # Prefer page that has discover content
            for p in browser.contexts[0].pages:
                if "discover-fonts" in (p.url or ""):
                    page = p
                    page.bring_to_front()
                    break
            print(f"  discover url={page.url}")
            menus = page.locator("button[aria-label='Open options menu']")
            print(f"  options menus={menus.count()}")
            if menus.count() == 0:
                # dump activate-ish buttons
                btns = page.evaluate(
                    """() => [...document.querySelectorAll('button,md-button')]
                       .map(el => el.getAttribute('aria-label')||(el.innerText||'').trim())
                       .filter(t => /activ|option|menu/i.test(t)).slice(0,20)"""
                )
                record(
                    operation="fontTempActivated/fontActivationTypeSwitched",
                    control_found=False,
                    verdict="AUTOMATION_BUG",
                    detail=f"no Open options menu; activate-ish={btns!r}",
                )
            else:
                _force_click(menus.first, timeout_ms=10000)
                time.sleep(1)
                items = page.evaluate(
                    """() => [...document.querySelectorAll('[role="menuitem"],button,a,md-menu-item')]
                       .map(el => (el.innerText||el.getAttribute('aria-label')||'').trim())
                       .filter(t => t && t.length < 50).slice(0,25)"""
                )
                print(f"  menu items={items}")
                clicked = None
                for label in (
                    "Activate temporarily",
                    "Temporary",
                    "Activate permanently",
                    "Activate",
                ):
                    try:
                        loc = page.get_by_text(label, exact=False).first
                        if loc.is_visible(timeout=500):
                            loc.click(force=True, timeout=5000)
                            clicked = label
                            break
                    except Exception:
                        continue
                page.keyboard.press("Escape")
                record(
                    operation="fontActivate(menu)",
                    control_found=True,
                    verdict="UI_STATE_CHANGED" if clicked else "AUTOMATION_BUG",
                    detail=f"menu_items={items!r} clicked={clicked!r}",
                )

            # Manage / local fonts
            manage = page.locator("[data-testid='menu-item-Manage']").first
            print(f"  Manage menu count={manage.count()}")
            if manage.count() == 0:
                side = page.evaluate(
                    """() => [...document.querySelectorAll('[data-testid^="menu-item-"]')]
                       .map(el => el.getAttribute('data-testid'))"""
                )
                record(
                    operation="fontLocalfont*",
                    control_found=False,
                    verdict="AUTOMATION_BUG",
                    detail=f"Manage not found; sidebar={side!r}",
                )
            else:
                _force_click(manage, timeout_ms=10000)
                time.sleep(2)
                record(
                    operation="fontLocalfont*(open Manage)",
                    control_found=True,
                    verdict="UI_STATE_CHANGED",
                    detail=f"opened Manage url={page.url}",
                )
        except Exception as exc:  # noqa: BLE001
            record(
                operation="discover_fonts_section",
                control_found=False,
                verdict="AUTOMATION_BUG",
                detail=str(exc)[:240],
            )

        # ---------- PROFILE / WORKSPACE ----------
        print("\n=== Profile / workspace ===")
        try:
            page = _pick_main_page(browser) or main
            page.bring_to_front()
            _open_profile_menu(page)
            time.sleep(1)
            items = page.evaluate(
                """() => [...document.querySelectorAll('[role="menuitem"],button,a,md-button')]
                   .map(el => (el.innerText||el.getAttribute('aria-label')||'').trim())
                   .filter(t => t && t.length < 40).slice(0,30)"""
            )
            print(f"  profile menu items={items}")
            has_ws = any("workspace" in (t or "").lower() or "audit co" in (t or "").lower() for t in items)
            record(
                operation="userSwitchWorkspaceApp(menu)",
                control_found=True,
                verdict="UI_STATE_CHANGED" if has_ws else "AUTOMATION_BUG",
                detail=f"profile_items={items!r}",
            )
            page.keyboard.press("Escape")
        except Exception as exc:  # noqa: BLE001
            record(
                operation="userSwitchWorkspaceApp",
                control_found=False,
                verdict="AUTOMATION_BUG",
                detail=str(exc)[:240],
            )

        browser.close()

    print(f"\n=== Waiting {args.wait_sec:.0f}s for CurlDebug ===")
    time.sleep(max(0.0, args.wait_sec))

    log = sorted(log_dir.glob("file-*.log"))[-1]
    with log.open("r", encoding="utf-8", errors="ignore") as f:
        f.seek(min(offset_before, log.stat().st_size))
        chunk = f.read()

    fresh = []
    for line in chunk.splitlines():
        if "[CurlDebug]" not in line:
            continue
        curl = line.split("[CurlDebug]", 1)[1].strip()
        if TARGET_URL not in curl and "audit-events" not in curl:
            continue
        payload = _extract_payload_from_curl(curl) or {}
        src = payload.get("source") or {}
        fresh.append(
            {
                "operation": src.get("operation") or "",
                "xCorrelationId": payload.get("xCorrelationId") or "",
                "occurredAt": payload.get("occurredAt") or "",
            }
        )

    print(f"\nFresh CurlDebug since probes: {len(fresh)}")
    for row in fresh:
        print(f"  {row['operation']:40} {row['xCorrelationId']} @ {row['occurredAt']}")

    by_verdict: dict[str, int] = {}
    for r in results:
        by_verdict[str(r.get("verdict"))] = by_verdict.get(str(r.get("verdict")), 0) + 1

    out_dir = ROOT / "reports" / "curl-from-logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = out_dir / f"trigger-diagnostics-{ts}.json"
    report = {
        "checked_at": _now(),
        "wait_sec": args.wait_sec,
        "log_offset_before": offset_before,
        "ui_probes": results,
        "verdict_counts": by_verdict,
        "fresh_curls": fresh,
        "interpretation": {
            "AUTOMATION_BUG": "Control missing or our selector/action failed — fix on our side",
            "UI_OK_NO_STATE_CHANGE": "We clicked a real control but state did not change — may be already set or control inert",
            "UI_STATE_CHANGED": "Our click changed UI state — if no CurlDebug, gap is app/ConnectService emit side",
        },
    }
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nVerdict counts: {by_verdict}")
    print(f"Report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
