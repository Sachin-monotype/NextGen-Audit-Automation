"""Playwright-based Monotype Connect desktop UI runner (Electron)."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import (
    cdp_port,
    desktop_app_bundle,
    desktop_app_path,
    electron_attach_mode,
    quit_existing_before_launch,
    use_open_on_macos,
)
from .navigation import DesktopEvent, UiStep

log = logging.getLogger(__name__)


@dataclass
class StepResult:
    event_operation: str
    step_description: str
    status: str
    error: str = ""


@dataclass
class UiRunResult:
    triggered_operations: list[str] = field(default_factory=list)
    step_results: list[StepResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _playwright_available() -> bool:
    try:
        import playwright  # noqa: F401

        return True
    except ImportError:
        return False


def _cdp_ready(port: int, *, timeout_sec: float = 1.0) -> bool:
    url = f"http://127.0.0.1:{port}/json/version"
    try:
        with urllib.request.urlopen(url, timeout=timeout_sec) as resp:  # noqa: S310
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _wait_for_cdp(port: int, *, timeout_sec: float = 45.0) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if _cdp_ready(port):
            return True
        time.sleep(0.5)
    return False


def _quit_existing_app(exe: Path | None) -> None:
    if not quit_existing_before_launch():
        return
    bundle = desktop_app_bundle()
    name = (bundle.stem if bundle else exe.stem if exe else "Monotype NextGen").strip()
    if sys.platform == "darwin":
        subprocess.run(["pkill", "-x", name], check=False)  # noqa: S603
        time.sleep(1.5)
    elif sys.platform.startswith("win") and exe:
        subprocess.run(  # noqa: S603
            ["taskkill", "/IM", exe.name, "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1.5)


def _launch_desktop_app(exe: Path, port: int) -> subprocess.Popen[Any] | None:
    bundle = desktop_app_bundle()
    if sys.platform == "darwin" and use_open_on_macos() and bundle:
        subprocess.run(  # noqa: S603
            ["open", "-n", "-a", str(bundle), "--args", f"--remote-debugging-port={port}"],
            check=True,
        )
        return None
    return subprocess.Popen(  # noqa: S603
        [str(exe), f"--remote-debugging-port={port}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _all_pages(browser: Any) -> list[Any]:
    pages: list[Any] = []
    for context in browser.contexts:
        pages.extend(context.pages)
    return pages


def _pick_main_page(browser: Any) -> Any | None:
    pages = _all_pages(browser)
    for page in pages:
        url = page.url or ""
        if "nextgen.monotype-pp.com" in url and url.startswith("https"):
            return page
    for page in pages:
        if (page.url or "").startswith("https"):
            return page
    return pages[0] if pages else None


def _pick_preferences_page(browser: Any) -> Any | None:
    pages = _all_pages(browser)
    for page in pages:
        url = (page.url or "").lower()
        title = ""
        try:
            title = (page.title() or "").lower()
        except Exception:  # noqa: BLE001
            pass
        if any(k in url for k in ("preference", "settings", "help", "support")):
            return page
        if any(k in title for k in ("preference", "settings", "help")):
            return page
    for page in pages:
        if (page.url or "").startswith("file://") and "electron-panel" not in (page.url or ""):
            return page
    return _pick_main_page(browser)


def _wait_for_main_ui(page: Any, *, timeout_ms: int = 60_000) -> None:
    page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    page.locator("[data-testid='main-layout'], [data-testid='sidebar']").first.wait_for(
        state="visible",
        timeout=timeout_ms,
    )


def _open_native_preferences(main_page: Any, browser: Any) -> Any:
    """Open the desktop Preferences window (macOS: ⌘,). Returns the active page."""
    main_page.bring_to_front()
    if sys.platform == "darwin":
        main_page.keyboard.press("Meta+Comma")
    else:
        main_page.keyboard.press("Control+Comma")
    time.sleep(2.0)
    pref = _pick_preferences_page(browser)
    if pref:
        pref.bring_to_front()
        try:
            pref.wait_for_load_state("domcontentloaded", timeout=15_000)
        except Exception:  # noqa: BLE001
            pass
        return pref
    return main_page


def _ensure_preferences_section(
    page: Any,
    browser: Any,
    main_page: Any,
    section: str,
    *,
    timeout_ms: int = 15_000,
) -> Any:
    """Land on Preferences > general|plugins|about|updates."""
    section = (section or "general").strip().lower()
    active = page
    if "help-support" in (active.url or "") or "help" in (active.url or "").lower():
        try:
            active.locator("[data-qa-id='mac-header']").get_by_text("Preferences", exact=True).first.click(
                timeout=timeout_ms, force=True
            )
            active.wait_for_timeout(1000)
        except Exception:  # noqa: BLE001
            pass
    if "preferences" not in (active.url or ""):
        active = _open_native_preferences(main_page or page, browser)
    # Prefer sidebar menu item
    menu = active.locator(f"[data-testid='menu-item-{section}']").first
    try:
        menu.wait_for(state="visible", timeout=5000)
        menu.click(timeout=timeout_ms, force=True)
    except Exception:  # noqa: BLE001
        active.get_by_text(section.capitalize(), exact=False).first.click(timeout=timeout_ms, force=True)
    active.wait_for_timeout(1000)
    return active


def _qa_base_url() -> str:
    return (
        os.getenv("DESKTOP_APP_BASE_URL")
        or os.getenv("NEXTGEN_QA_URL")
        or "https://nextgen-qa.monotype-pp.com"
    ).rstrip("/")


def _md_toggle_checked(loc: Any) -> bool | None:
    """Best-effort checked state for md-toggle / role=switch."""
    try:
        checked_attr = loc.get_attribute("checked")
        if checked_attr is not None:
            return True
        aria = loc.get_attribute("aria-checked")
        if aria is not None:
            return aria.lower() == "true"
        # shadow / inner switch
        inner = loc.locator("[role='switch']").first
        if inner.count() > 0:
            aria2 = inner.get_attribute("aria-checked")
            if aria2 is not None:
                return aria2.lower() == "true"
    except Exception:  # noqa: BLE001
        return None
    return False


def _force_click(loc: Any, *, timeout_ms: int) -> None:
    loc.wait_for(state="attached", timeout=timeout_ms)
    try:
        loc.click(timeout=timeout_ms, force=True)
    except Exception:
        # label overlay on md-toggle
        loc.locator("label, [role='switch'], .md-toggle__switch").first.click(
            timeout=timeout_ms, force=True
        )


def _open_profile_menu(page: Any, *, timeout_ms: int = 10_000) -> None:
    page.bring_to_front()
    for sel in (
        "[data-qa-id='profile-avatar-trigger']",
        "[data-testid='profile-avatar-trigger']",
        "[data-qa-id='profile-avatar-wrapper']",
    ):
        loc = page.locator(sel).first
        try:
            if loc.count() > 0:
                _force_click(loc, timeout_ms=timeout_ms)
                page.wait_for_timeout(800)
                return
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError("Could not open profile avatar menu")


def _open_help_support(page: Any, browser: Any, *, timeout_ms: int = 15_000) -> Any:
    """Profile menu → Help & support. Returns the help page."""
    _open_profile_menu(page, timeout_ms=timeout_ms)
    page.get_by_text("Help & support", exact=False).first.click(timeout=timeout_ms, force=True)
    page.wait_for_timeout(1500)
    for p in _all_pages(browser):
        if "help-support" in (p.url or "") or "help" in (p.url or "").lower():
            p.bring_to_front()
            try:
                p.wait_for_load_state("domcontentloaded", timeout=10_000)
            except Exception:  # noqa: BLE001
                pass
            return p
    return page


def _select_dropdown_option(page: Any, step: UiStep, *, timeout_ms: int) -> None:
    loc = _locator_for(page, step)
    _force_click(loc, timeout_ms=timeout_ms)
    page.wait_for_timeout(700)
    options = [o.strip() for o in (step.value or "").split("|") if o.strip()]
    if not options:
        options = [step.description] if step.description else []
    for label in options:
        candidate = page.get_by_text(label, exact=False).first
        try:
            if candidate.is_visible(timeout=800):
                candidate.click(timeout=timeout_ms, force=True)
                page.wait_for_timeout(400)
                return
        except Exception:  # noqa: BLE001
            continue
    page.keyboard.press("Escape")
    raise RuntimeError(f"No dropdown option matched for {step.description!r}: {options}")


def _click_text_options(page: Any, step: UiStep, *, timeout_ms: int) -> None:
    labels = [o.strip() for o in (step.value or step.description or "").split("|") if o.strip()]
    errors: list[str] = []
    for label in labels:
        for factory in (
            lambda lab=label: page.get_by_role("button", name=lab, exact=False).first,
            lambda lab=label: page.get_by_role("menuitem", name=lab, exact=False).first,
            lambda lab=label: page.get_by_role("link", name=lab, exact=False).first,
            lambda lab=label: page.get_by_text(lab, exact=False).first,
        ):
            try:
                loc = factory()
                if loc.is_visible(timeout=900):
                    loc.click(timeout=timeout_ms, force=True)
                    return
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))
                continue
    raise RuntimeError(errors[-1] if errors else f"Could not click text options: {labels}")


def _locator_for(page: Any, step: UiStep) -> Any:
    if step.selector:
        return page.locator(step.selector).first
    if step.xpath:
        return page.locator(f"xpath={step.xpath}").first
    raise ValueError(f"No selector/xpath for step: {step.description}")


def _execute_step(
    page: Any,
    step: UiStep,
    *,
    browser: Any | None = None,
    main_page: Any | None = None,
    timeout_ms: int = 15_000,
) -> Any:
    """Execute one UI step. Returns the active page (may change for prefs/help/goto)."""
    action = step.action
    active = page

    if action == "open_preferences":
        base = main_page or page
        return _open_native_preferences(base, browser) if browser else page

    if action == "open_preferences_section":
        if browser is None:
            raise RuntimeError("open_preferences_section requires browser handle")
        return _ensure_preferences_section(
            page, browser, main_page or page, step.value or "general", timeout_ms=timeout_ms
        )

    if action == "open_profile_menu":
        _open_profile_menu(page, timeout_ms=timeout_ms)
        return page

    if action == "open_help_support":
        if browser is None:
            raise RuntimeError("open_help_support requires browser handle")
        return _open_help_support(page, browser, timeout_ms=timeout_ms)

    if action == "goto":
        path = (step.value or "").strip()
        if not path:
            raise ValueError("goto step requires value URL/path")
        url = path if path.startswith("http") else f"{_qa_base_url()}{path if path.startswith('/') else '/' + path}"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=min(timeout_ms, 20_000))
        except Exception:
            # Electron SPA sometimes stalls on goto — fall through; caller may still be on a usable page.
            pass
        page.wait_for_timeout(1500)
        return page

    if action == "click_text":
        _click_text_options(page, step, timeout_ms=timeout_ms)
        return page

    if action == "select_option":
        _select_dropdown_option(page, step, timeout_ms=timeout_ms)
        return page

    if action in {"toggle_on", "toggle_off", "toggle"}:
        loc = _locator_for(page, step)
        loc.wait_for(state="attached", timeout=timeout_ms)
        state = _md_toggle_checked(loc)
        want_on = action == "toggle_on" or (action == "toggle" and state is False)
        want_off = action == "toggle_off" or (action == "toggle" and state is True)
        if action == "toggle":
            _force_click(loc, timeout_ms=timeout_ms)
            return page
        if want_on and state is True:
            return page
        if want_off and state is False:
            return page
        _force_click(loc, timeout_ms=timeout_ms)
        return page

    if action == "sleep":
        page.wait_for_timeout(int(float(step.value or "1") * 1000))
        return page

    errors: list[str] = []
    for factory in (
        lambda: _locator_for(page, step) if step.selector or step.xpath else None,
        lambda: page.get_by_role("button", name=step.description, exact=False).first
        if step.description
        else None,
        lambda: page.get_by_text(step.description, exact=False).first if step.description else None,
    ):
        try:
            loc = factory()
            if loc is None:
                continue
            if action in {"click", "toggle"}:
                _force_click(loc, timeout_ms=timeout_ms)
            elif action == "check":
                loc.check(timeout=timeout_ms, force=True)
            elif action == "uncheck":
                loc.uncheck(timeout=timeout_ms, force=True)
            elif action == "fill":
                loc.wait_for(state="visible", timeout=timeout_ms)
                loc.fill(step.value or "", timeout=timeout_ms)
            elif action == "select":
                loc.select_option(step.value or "", timeout=timeout_ms)
            elif action == "press":
                page.keyboard.press(step.value or "Enter")
            elif action == "hover":
                loc.hover(timeout=timeout_ms)
            else:
                raise ValueError(f"Unknown UI action: {action}")
            return active
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
    raise RuntimeError(errors[-1] if errors else f"Could not execute step: {step.description}")


def _navigate_breadcrumb(page: Any, segments: list[str], *, timeout_ms: int = 15_000) -> None:
    """Click sidebar / menu items by data-testid or visible text."""
    for seg in segments:
        text = seg.strip()
        if not text or text.lower() in {"desktop app", "n/a", "na"}:
            continue
        # Strip action suffixes like "Enable toggle"
        label = text.split(">")[-1].strip() if ">" in text else text
        for action_word in (" Enable toggle", " Disable toggle", " Select mode", " Confirm"):
            if label.endswith(action_word):
                label = label[: -len(action_word)].strip()

        candidates: list[Any] = []
        if label:
            safe = label.replace("'", "\\'")
            candidates.extend(
                [
                    page.locator(f"[data-testid='menu-item-{label}']").first,
                    page.locator(f"[data-testid='{label.lower().replace(' ', '-')}']").first,
                    page.get_by_role("button", name=label, exact=False),
                    page.get_by_role("link", name=label, exact=False),
                    page.get_by_role("tab", name=label, exact=False),
                    page.get_by_text(label, exact=False),
                    page.locator(f"xpath=//*[contains(normalize-space(.), '{safe}')]").first,
                ]
            )
        clicked = False
        for loc in candidates:
            try:
                if loc.is_visible(timeout=2_000):
                    loc.click(timeout=timeout_ms)
                    clicked = True
                    page.wait_for_timeout(500)
                    break
            except Exception:  # noqa: BLE001
                continue
        if not clicked:
            raise RuntimeError(f"Could not find UI control for navigation step: {label!r}")


def _connect_browser(pw: Any, port: int) -> Any:
    from playwright.sync_api import Error as PlaywrightError

    try:
        return pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    except PlaywrightError as exc:
        raise RuntimeError(
            f"Could not connect to desktop app on CDP port {port}. "
            f"Quit any running Monotype NextGen instance and retry, or launch manually with "
            f"--remote-debugging-port={port}. Original error: {exc}"
        ) from exc


def _ensure_app_cdp(
    exe: Path | None,
    port: int,
    *,
    connect_only: bool,
    progress: Callable[[str], None] | None,
) -> subprocess.Popen[Any] | None:
    if _cdp_ready(port, timeout_sec=1.0):
        if progress:
            progress(f"CDP already listening on localhost:{port}")
        return None

    if connect_only or electron_attach_mode() == "attach":
        raise RuntimeError(
            f"No desktop app listening on CDP port {port}. "
            f"Quit Monotype NextGen, then rerun (auto-launch), or start manually:\n"
            f"  open -n -a 'Monotype NextGen' --args --remote-debugging-port={port}"
        )

    if not exe:
        raise RuntimeError(
            "Desktop app executable not found. Set DESKTOP_APP_PATH in .env — "
            "e.g. /Applications/Monotype NextGen/Monotype NextGen.app"
        )

    if progress:
        progress(f"Quitting any existing Monotype NextGen instance…")
    _quit_existing_app(exe)

    if progress:
        progress(f"Launching desktop app (CDP port {port})…")
    proc = _launch_desktop_app(exe, port)
    if not _wait_for_cdp(port):
        if proc:
            proc.terminate()
        raise RuntimeError(
            f"Desktop app did not expose CDP on port {port} within 45s. "
            "On macOS use DESKTOP_USE_OPEN_ON_MACOS=true (default) and ensure the app is fully quit first."
        )
    if progress:
        progress(f"CDP ready on localhost:{port}")
    return proc


def _needs_preferences_window(event: DesktopEvent) -> bool:
    if any(s.action == "open_preferences" for s in event.steps):
        return True
    nav = " ".join(event.navigation).lower()
    return "preferences" in nav and "help" not in nav.split("preferences")[0]


def _needs_help_support(event: DesktopEvent) -> bool:
    if any(s.action == "open_help_support" for s in event.steps):
        return True
    nav = " ".join(event.navigation).lower()
    return "help" in nav and "support" in nav


def _page_for_event(browser: Any, main_page: Any, event: DesktopEvent, *, prefs_open: bool) -> Any:
    if _needs_help_support(event):
        return main_page
    if _needs_preferences_window(event):
        if not prefs_open:
            return _open_native_preferences(main_page, browser)
        pref = _pick_preferences_page(browser)
        return pref or main_page
    return main_page


def run_desktop_ui_steps(
    events: list[DesktopEvent],
    *,
    app_path: Path | None = None,
    connect_only: bool = False,
    settle_sec: float = 2.0,
    on_event_triggered: Callable[[DesktopEvent], None] | None = None,
    progress: Callable[[str], None] | None = None,
) -> UiRunResult:
    """Launch or attach to the desktop app and execute navigation steps."""
    result = UiRunResult()
    runnable = [e for e in events if e.automatable]
    if not runnable:
        result.errors.append("No automatable desktop events in selection.")
        return result

    if not _playwright_available():
        result.errors.append(
            "playwright is not installed — run: pip install playwright && playwright install chromium. "
            "Use --validate-only to parse today's logs without UI automation."
        )
        return result

    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    def _log(msg: str) -> None:
        if progress:
            progress(msg)
        else:
            log.info(msg)

    exe = app_path or desktop_app_path()
    port = cdp_port()

    try:
        _ensure_app_cdp(exe, port, connect_only=connect_only, progress=progress)
    except RuntimeError as exc:
        result.errors.append(str(exc))
        return result

    with sync_playwright() as pw:
        browser = None
        try:
            _log(f"Connecting via CDP localhost:{port}")
            browser = _connect_browser(pw, port)
            main_page = _pick_main_page(browser)
            if main_page is None:
                raise RuntimeError("No browser pages found after CDP connect.")
            _log(f"Using main page: {main_page.url[:100]}")
            main_page.bring_to_front()
            _wait_for_main_ui(main_page)
            prefs_open = False

            for event in runnable:
                _log(f"Trigger {event.event_name} ({event.operation})")
                try:
                    page = _page_for_event(browser, main_page, event, prefs_open=prefs_open)
                    if _needs_preferences_window(event):
                        prefs_open = True
                    page.bring_to_front()

                    # Prefer explicit steps. Freeform navigation strings are human hints only.
                    if event.steps:
                        for step in event.steps:
                            try:
                                page = _execute_step(
                                    page,
                                    step,
                                    browser=browser,
                                    main_page=main_page,
                                ) or page
                                if step.action == "open_preferences":
                                    prefs_open = True
                                result.step_results.append(
                                    StepResult(
                                        event_operation=event.operation,
                                        step_description=step.description or step.action,
                                        status="PASS",
                                    )
                                )
                            except (PlaywrightError, ValueError, RuntimeError) as exc:
                                result.step_results.append(
                                    StepResult(
                                        event_operation=event.operation,
                                        step_description=step.description or step.action,
                                        status="FAIL",
                                        error=str(exc),
                                    )
                                )
                                raise
                    else:
                        nav_segments = [
                            s.replace("Desktop app > ", "").replace("Desktop app>", "").strip()
                            for s in event.navigation
                        ]
                        for nav_line in nav_segments:
                            parts = [p.strip() for p in nav_line.split(">") if p.strip()]
                            if prefs_open and parts and parts[0].lower() == "preferences":
                                parts = parts[1:]
                            if parts:
                                _navigate_breadcrumb(page, parts)

                    time.sleep(settle_sec)
                    result.triggered_operations.append(event.operation)
                    if on_event_triggered:
                        on_event_triggered(event)
                except Exception as exc:  # noqa: BLE001
                    err = f"{event.operation}: {exc}"
                    result.errors.append(err)
                    _log(f"  ✖ {err}")

        finally:
            if browser:
                browser.close()

    return result


def launch_app_with_debug_port(app_path: Path | None = None, *, port: int | None = None) -> subprocess.Popen[Any] | None:
    exe = app_path or desktop_app_path()
    if not exe:
        return None
    port = port or cdp_port()
    _quit_existing_app(exe)
    return _launch_desktop_app(exe, port)
