"""Monotype Connect desktop OAuth (Sign in → Auth0 browser → mtfngpp deeplink).

Ported from MTConnectAutomation web-audit ``lib/app-login.js``.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import subprocess
import time
from typing import Any

log = logging.getLogger(__name__)

_AUTH_URL_RE = re.compile(r'"AuthorizationUrl"\s*:\s*"([^"]+)"')
_MTFNGPP_RE = re.compile(r"mtfngpp:/?/[^\"'<>\s]+", re.I)

# Shared across auth ops in one suite (initiated → identityLinked).
_LAST_AUTHORIZATION_URL: str | None = None

_CAPTURE_INJECT_JS = """
(function() {
  if (typeof window === 'undefined') return;
  window.__capturedAuthUrls = window.__capturedAuthUrls || [];
  window.__capturedConnectEvents = window.__capturedConnectEvents || [];
  if (window.__consoleCaptureInstalled) return;
  window.__consoleCaptureInstalled = true;
  var markers = ['AUTH_STATE_CHANGED', 'COMPLETE_LOGIN_RESPONSE', 'AuthorizationUrl'];
  ['log','info','debug','warn','error'].forEach(function(method) {
    var orig = console[method];
    console[method] = function() {
      var args = Array.prototype.slice.call(arguments);
      try {
        var str = args.map(function(a) {
          if (a && typeof a === 'object') {
            try { return JSON.stringify(a); } catch(e) { return String(a); }
          }
          return String(a);
        }).join(' ');
        for (var i = 0; i < markers.length; i++) {
          if (str.indexOf(markers[i]) !== -1) {
            window.__capturedConnectEvents.push({ts: Date.now(), text: str});
            if (str.indexOf('AuthorizationUrl') !== -1) {
              window.__capturedAuthUrls.push(str);
            }
            break;
          }
        }
      } catch(e) {}
      orig.apply(console, args);
    };
  });
})();
"""

_CALLBACK_EXTRACT_JS = """
() => {
  const urls = [];
  const seen = new Set();
  const add = (u) => {
    if (!u) return;
    const n = String(u).replace(/\\\\/g, '/').trim();
    if (n.indexOf('mtfngpp') === -1) return;
    if (!seen.has(n)) { seen.add(n); urls.push(n); }
  };
  const html = document.documentElement.innerHTML;
  const re = /mtfngpp:\\/?\\/[^"'<>\\s]+/gi;
  let m;
  while ((m = re.exec(html)) !== null) add(m[0]);
  document.querySelectorAll('a[href]').forEach((a) => {
    add((a.getAttribute('href') || a.href || '').trim());
  });
  document.querySelectorAll('[href*="mtfngpp"], [data-href*="mtfngpp"]').forEach((el) => {
    add((el.getAttribute('href') || el.getAttribute('data-href') || '').trim());
  });
  return urls.length ? urls[urls.length - 1] : null;
}
"""


def oauth_credentials() -> dict[str, str]:
    """Resolve desktop OAuth creds (AUDIT_* preferred, then OAUTH_*)."""
    username = (
        os.getenv("AUDIT_USERNAME", "").strip()
        or os.getenv("OAUTH_USERNAME", "").strip()
        or os.getenv("CASEPILOT_UI_USERNAME", "").strip()
    )
    password = (
        os.getenv("AUDIT_PASSWORD", "").strip()
        or os.getenv("OAUTH_PASSWORD", "").strip()
        or os.getenv("CASEPILOT_UI_PASSWORD", "").strip()
    )
    company = (
        os.getenv("AUDIT_COMPANY", "").strip()
        or os.getenv("OAUTH_COMPANY", "").strip()
        or "Monotype system admin"
    )
    return {"username": username, "password": password, "company": company}


def is_app_logged_in(page: Any) -> bool:
    url = (page.url or "").lower()
    if url.startswith("file:"):
        return False
    sign_in = page.locator("[data-qa-id='sign-in-button']").first
    try:
        if sign_in.is_visible(timeout=1_500):
            return False
    except Exception:  # noqa: BLE001
        pass
    for sel in (
        "[data-qa-id*='profile-avatar']",
        "[data-testid*='profile-avatar']",
        "[data-qa-id='discover-menu']",
        "[data-testid='discover-menu']",
        "[data-qa-id='sidebar']",
        "[data-testid='sidebar']",
    ):
        try:
            if page.locator(sel).first.is_visible(timeout=1_500):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _install_auth_capture(page: Any) -> None:
    try:
        cdp = page.context.new_cdp_session(page)
        cdp.send("Runtime.evaluate", {"expression": _CAPTURE_INJECT_JS})
    except Exception:  # noqa: BLE001
        try:
            page.evaluate(_CAPTURE_INJECT_JS)
        except Exception:  # noqa: BLE001
            pass


def _read_captured_auth_urls(page: Any) -> list[str]:
    try:
        cdp = page.context.new_cdp_session(page)
        result = cdp.send(
            "Runtime.evaluate",
            {
                "expression": "JSON.stringify(window.__capturedAuthUrls || [])",
                "returnByValue": True,
            },
        )
        raw = (result.get("result") or {}).get("value") or "[]"
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001
        try:
            data = page.evaluate("() => window.__capturedAuthUrls || []")
            return data if isinstance(data, list) else []
        except Exception:  # noqa: BLE001
            return []


def _get_authorization_url(page: Any, *, timeout_ms: int = 90_000) -> str | None:
    """Return the latest desktop AuthorizationUrl (externaldesktoplogin / Auth0)."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        found: list[str] = []
        for entry in _read_captured_auth_urls(page):
            match = _AUTH_URL_RE.search(str(entry))
            if not match:
                continue
            url = match.group(1).replace("\\/", "/")
            if "externaldesktoplogin" in url or "/authorize" in url or "AuthorizationUrl" in str(entry):
                found.append(url)
        if found:
            return found[-1]
        time.sleep(0.5)
    return None


def click_app_sign_in(page: Any, *, timeout_ms: int = 90_000) -> None:
    host = page.locator("[data-qa-id='sign-in-button']").first
    host.wait_for(state="visible", timeout=timeout_ms)
    page.evaluate(
        """() => {
      const el = document.querySelector("[data-qa-id='sign-in-button']");
      const btn =
        el?.shadowRoot?.querySelector('button') || el?.querySelector('button') || el;
      btn?.dispatchEvent(
        new MouseEvent('click', { bubbles: true, cancelable: true, view: window })
      );
    }"""
    )


def start_login_initiated(page: Any) -> None:
    """Click Sign in so ``userLoginInitiatedApp`` fires (does not complete OAuth)."""
    if is_app_logged_in(page):
        raise RuntimeError("App is already logged in — logout before login-initiated")
    auth_url = _begin_sign_in_capture(page)
    log.info("Login initiated; AuthorizationUrl cached for identityLinked (%s…)", auth_url[:48])
    # Give CurlDebug a moment to flush the initiated event.
    page.wait_for_timeout(2_000)


def _wait_for_browser_signin_modal(page: Any, *, timeout_ms: int = 60_000) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        try:
            if page.get_by_text(re.compile(r"signing you in", re.I)).first.is_visible(timeout=400):
                return True
        except Exception:  # noqa: BLE001
            pass
        try:
            desc = page.locator("md-typography[data-qa-id='overlay-modal-description']").first
            if desc.is_visible(timeout=400):
                text = (desc.inner_text() or "")
                if re.search(r"browser", text, re.I):
                    return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.4)
    return False


def _wait_for_app_shell(page: Any, *, timeout_ms: int = 120_000) -> None:
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        if is_app_logged_in(page):
            return
        time.sleep(1.0)
    raise RuntimeError("App shell not ready after OAuth deeplink")


def _resolve_oauth_callback(oauth_page: Any) -> str | None:
    callback = oauth_page.url or ""
    if "mtfngpp" in callback.lower():
        return _normalize_deeplink(callback)

    # Prefer the explicit Open desktop app control when present.
    for sel in (
        'a[href*="mtfngpp"]',
        'button[href*="mtfngpp"]',
        "[data-qa-id*='open-desktop']",
    ):
        try:
            loc = oauth_page.locator(sel).first
            if loc.count() > 0 and loc.is_visible(timeout=1_500):
                href = loc.get_attribute("href") or ""
                if "mtfngpp" in href.lower():
                    return _normalize_deeplink(href)
        except Exception:  # noqa: BLE001
            continue

    open_btn = oauth_page.get_by_role("button", name=re.compile(r"open.*desktop", re.I)).first
    try:
        if open_btn.is_visible(timeout=5_000):
            href = open_btn.get_attribute("href")
            if href and re.search(r"mtfngpp", href, re.I):
                return _normalize_deeplink(href)
            # Clicking may navigate to mtfngpp://
            open_btn.click(no_wait_after=True)
            time.sleep(2.0)
            callback = oauth_page.url or ""
            if "mtfngpp" in callback.lower():
                return _normalize_deeplink(callback)
    except Exception:  # noqa: BLE001
        pass

    link = oauth_page.locator('a[href*="mtfngpp"]').first
    try:
        if link.is_visible(timeout=3_000):
            href = link.get_attribute("href")
            if href:
                return _normalize_deeplink(href)
    except Exception:  # noqa: BLE001
        pass

    try:
        extracted = oauth_page.evaluate(_CALLBACK_EXTRACT_JS)
        if extracted:
            return _normalize_deeplink(str(extracted))
    except Exception:  # noqa: BLE001
        pass
    return None


def _normalize_deeplink(url: str) -> str:
    """Unescape HTML entities and keep ``mtfngpp:///auth?...`` form (three slashes)."""
    normalized = html.unescape((url or "").strip())
    # Force mtfngpp:///path so query params (code/state) stay on the path, not host.
    if normalized.lower().startswith("mtfngpp:"):
        rest = re.sub(r"^mtfngpp:\/*", "", normalized, flags=re.I)
        normalized = f"mtfngpp:///{rest.lstrip('/')}"
    return normalized


def _trigger_deeplink(url: str) -> None:
    normalized = _normalize_deeplink(url)
    log.info("App OAuth: open deeplink %s…", normalized[:80])
    subprocess.run(["open", normalized], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # noqa: S603


def _fill_auth0_username(oauth_page: Any, username: str) -> None:
    user_field = oauth_page.locator("#username, input[name='username']").first
    user_field.wait_for(state="visible", timeout=90_000)
    user_field.fill(username)
    oauth_page.locator('button[data-action-button-primary="true"]').first.click()


def _fill_auth0_password(oauth_page: Any, password: str) -> None:
    pass_field = oauth_page.locator("#password, input[name='password']").first
    pass_field.wait_for(state="visible", timeout=60_000)
    pass_field.fill(password)
    oauth_page.locator('button[data-action-button-primary="true"]').last.click()


def _wait_for_oauth_success_page(oauth_page: Any, *, timeout_ms: int = 90_000) -> None:
    """Wait until Auth0/desktop callback shows the success / Open desktop app UI."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        try:
            body = (oauth_page.inner_text("body") or "").lower()
        except Exception:  # noqa: BLE001
            body = ""
        if "sign in successful" in body or "open the desktop app" in body:
            return
        if "mtfngpp" in (oauth_page.url or "").lower():
            return
        try:
            if oauth_page.locator('a[href*="mtfngpp"]').first.is_visible(timeout=400):
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    log.warning("OAuth success page not detected before timeout; continuing")


def _pick_company(oauth_page: Any, company: str) -> None:
    company_btn = oauth_page.locator(f'button[aria-label="{company}"]').first
    try:
        if company_btn.is_visible(timeout=15_000):
            company_btn.click()
            return
    except Exception:  # noqa: BLE001
        pass
    try:
        fallback = (
            oauth_page.locator("button[aria-label]")
            .filter(has=oauth_page.locator("md-avatar"))
            .first
        )
        if fallback.is_visible(timeout=5_000):
            fallback.click()
    except Exception:  # noqa: BLE001
        pass


def _launch_oauth_browser(playwright: Any):
    return playwright.chromium.launch(headless=False)


def _remember_authorization_url(url: str | None) -> None:
    global _LAST_AUTHORIZATION_URL
    if url:
        _LAST_AUTHORIZATION_URL = url


def _pop_authorization_url(page: Any | None = None) -> str | None:
    """Prefer in-memory URL from a prior Sign in; fall back to page capture."""
    global _LAST_AUTHORIZATION_URL
    if _LAST_AUTHORIZATION_URL:
        url = _LAST_AUTHORIZATION_URL
        _LAST_AUTHORIZATION_URL = None
        return url
    if page is None:
        return None
    for entry in reversed(_read_captured_auth_urls(page)):
        match = _AUTH_URL_RE.search(str(entry))
        if match:
            return match.group(1).replace("\\/", "/")
    return None


def _begin_sign_in_capture(page: Any) -> str:
    """Click Sign in and return AuthorizationUrl."""
    _install_auth_capture(page)
    try:
        page.evaluate("() => { window.__capturedAuthUrls = []; }")
    except Exception:  # noqa: BLE001
        pass
    click_app_sign_in(page)
    auth_url = _get_authorization_url(page)
    if not auth_url:
        raise RuntimeError("Could not capture AuthorizationUrl from app after Sign in")
    _remember_authorization_url(auth_url)
    _wait_for_browser_signin_modal(page, timeout_ms=45_000)
    return auth_url


def login_app_failure(
    page: Any,
    *,
    username: str | None = None,
    playwright: Any | None = None,
) -> None:
    """Sign in → Auth0 wrong password, then cancel in-app so failure is emitted.

    Historical CurlDebug shows ``userLoginFailureApp`` with error ``CancelledByUser``
    after an initiated login is abandoned. Wrong password alone does not notify the
    Electron service; cancelling the Signing-you-in modal does.
    """
    global _LAST_AUTHORIZATION_URL
    creds = oauth_credentials()
    user = (username or creds["username"] or "").strip()
    if not user:
        raise RuntimeError("Set AUDIT_USERNAME / OAUTH_USERNAME for login failure flow")
    if is_app_logged_in(page):
        raise RuntimeError("App is logged in — logout before login-failure")

    auth_url = _begin_sign_in_capture(page)
    log.info("App OAuth failure: authorization URL captured")

    from playwright.sync_api import sync_playwright

    def _run(pw: Any) -> None:
        browser = _launch_oauth_browser(pw)
        try:
            oauth_page = browser.new_page()
            oauth_page.goto(auth_url, wait_until="domcontentloaded", timeout=60_000)
            _fill_auth0_username(oauth_page, user)
            _fill_auth0_password(oauth_page, "DefinitelyWrongPassword!123")
            time.sleep(3.0)
            wrong = oauth_page.locator(
                "#error-element-password, [data-error-code='wrong-email-credentials']"
            )
            try:
                wrong.first.wait_for(state="visible", timeout=15_000)
            except Exception:  # noqa: BLE001
                log.warning("Auth0 wrong-credential banner not visible; continuing")
        finally:
            browser.close()

    if playwright is not None:
        _run(playwright)
    else:
        with sync_playwright() as pw:
            _run(pw)

    # Cancel the in-app login session via modal close (CancelledByUser → userLoginFailureApp).
    dismiss_signin_modal(page)
    # Do not reuse this AuthorizationUrl for identityLinked.
    _LAST_AUTHORIZATION_URL = None
    try:
        page.evaluate("() => { window.__capturedAuthUrls = []; }")
    except Exception:  # noqa: BLE001
        pass
    page.wait_for_timeout(3_000)


def complete_oauth_from_captured_url(
    page: Any,
    *,
    username: str | None = None,
    password: str | None = None,
    company: str | None = None,
    playwright: Any | None = None,
) -> bool:
    """If AuthorizationUrl was already captured (after initiated), finish success OAuth.

    Returns True when a captured URL was used; False if caller should start fresh.
    """
    auth_url = _pop_authorization_url(page)
    if not auth_url:
        return False

    creds = oauth_credentials()
    user = (username or creds["username"] or "").strip()
    pwd = (password or creds["password"] or "").strip()
    company_name = (company or creds["company"] or "").strip()
    if not user or not pwd:
        raise RuntimeError("Set AUDIT_USERNAME/AUDIT_PASSWORD (or OAUTH_*) for app OAuth login")

    from playwright.sync_api import sync_playwright

    def _run(pw: Any) -> None:
        browser = _launch_oauth_browser(pw)
        try:
            oauth_page = browser.new_page()
            oauth_page.goto(auth_url, wait_until="domcontentloaded", timeout=60_000)
            _fill_auth0_username(oauth_page, user)
            _fill_auth0_password(oauth_page, pwd)
            time.sleep(2.0)
            _pick_company(oauth_page, company_name)
            _wait_for_oauth_success_page(oauth_page)
            callback = _resolve_oauth_callback(oauth_page)
            if not callback:
                html = oauth_page.content()
                m = _MTFNGPP_RE.search(html or "")
                callback = m.group(0) if m else None
            if not callback:
                raise RuntimeError("OAuth completed but no mtfngpp deeplink was found")
            _trigger_deeplink(callback)
            time.sleep(8.0)
        finally:
            browser.close()

    if playwright is not None:
        _run(playwright)
    else:
        with sync_playwright() as pw:
            _run(pw)
    _wait_for_app_shell(page)
    return True


def dismiss_signin_modal(page: Any) -> None:
    """Dismiss 'Signing you in' overlay (Close modal → CancelledByUser failure)."""
    try:
        close = page.locator("md-modal").locator('[aria-label="Close modal"]').first
        if close.count() > 0:
            box = close.bounding_box()
            if box:
                page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                page.wait_for_timeout(1_500)
                return
            close.click(force=True, timeout=5_000)
            page.wait_for_timeout(1_500)
            return
    except Exception:  # noqa: BLE001
        pass
    for lab in ("Cancel", "Close", "Dismiss", "OK", "Got it"):
        try:
            loc = page.get_by_role("button", name=re.compile(lab, re.I)).first
            if loc.is_visible(timeout=800):
                loc.click(force=True)
                page.wait_for_timeout(800)
                return
        except Exception:  # noqa: BLE001
            continue
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
    except Exception:  # noqa: BLE001
        pass


def login_app(
    page: Any,
    *,
    username: str | None = None,
    password: str | None = None,
    company: str | None = None,
    playwright: Any | None = None,
) -> None:
    """Full OAuth success path (``identityLinked`` after deeplink)."""
    creds = oauth_credentials()
    user = (username or creds["username"] or "").strip()
    pwd = (password or creds["password"] or "").strip()
    company_name = (company or creds["company"] or "").strip()
    if not user or not pwd:
        raise RuntimeError("Set AUDIT_USERNAME/AUDIT_PASSWORD (or OAUTH_*) for app OAuth login")

    if is_app_logged_in(page):
        log.info("App OAuth: already logged in")
        return

    auth_url = _begin_sign_in_capture(page)
    log.info("App OAuth: authorization URL captured")

    from playwright.sync_api import sync_playwright

    def _run(pw: Any) -> None:
        browser = _launch_oauth_browser(pw)
        try:
            oauth_page = browser.new_page()
            oauth_page.goto(auth_url, wait_until="domcontentloaded", timeout=60_000)
            _fill_auth0_username(oauth_page, user)
            _fill_auth0_password(oauth_page, pwd)
            time.sleep(2.0)
            _pick_company(oauth_page, company_name)
            _wait_for_oauth_success_page(oauth_page)
            callback = _resolve_oauth_callback(oauth_page)
            if not callback:
                # Fallback: scan page HTML for mtfngpp
                html = oauth_page.content()
                m = _MTFNGPP_RE.search(html or "")
                callback = m.group(0) if m else None
            if not callback:
                raise RuntimeError("OAuth completed but no mtfngpp deeplink was found")
            log.info("App OAuth: triggering deeplink")
            _trigger_deeplink(callback)
            time.sleep(8.0)
        finally:
            browser.close()

    if playwright is not None:
        _run(playwright)
    else:
        with sync_playwright() as pw:
            _run(pw)

    _wait_for_app_shell(page)
    log.info("App OAuth: shell ready")


def _click_qa_host(page: Any, qa_id: str, *, timeout_ms: int = 15_000) -> bool:
    """Click an md-button host by data-qa-id (shadow-safe)."""
    sel = f"[data-qa-id='{qa_id}']"
    loc = page.locator(sel).first
    try:
        if loc.count() == 0:
            return False
        loc.wait_for(state="attached", timeout=timeout_ms)
        page.evaluate(
            """(qa) => {
              const el = document.querySelector(`[data-qa-id='${qa}']`);
              if (!el) return;
              const btn =
                el.shadowRoot?.querySelector('button') || el.querySelector('button') || el;
              btn.dispatchEvent(
                new MouseEvent('click', { bubbles: true, cancelable: true, view: window })
              );
            }""",
            qa_id,
        )
        return True
    except Exception:  # noqa: BLE001
        try:
            from .ui_runner import _force_click

            _force_click(loc, timeout_ms=timeout_ms)
            return True
        except Exception:  # noqa: BLE001
            return False


def ensure_main_shell(page: Any, browser: Any | None = None) -> Any:
    """Leave Preferences/Help and land on Discover so profile menu works."""
    from .ui_runner import _force_click, _goto_via_desktop_shell, _pick_main_page

    url = (page.url or "").lower()
    if "preferences" in url or "help-support" in url:
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
        except Exception:  # noqa: BLE001
            pass
        # Sidebar Discover is more reliable than Home when Preferences tab is focused.
        for sel in (
            "[data-qa-id='menu-item-Discover fonts']",
            "[data-testid='menu-item-Discover fonts']",
            "button[aria-label='Discover fonts']",
        ):
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    _force_click(loc, timeout_ms=10_000)
                    page.wait_for_timeout(1_200)
                    break
            except Exception:  # noqa: BLE001
                continue
        try:
            page = _goto_via_desktop_shell(
                page, "/discover-fonts/all", timeout_ms=20_000, browser=browser
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("ensure_main_shell navigate failed: %s", exc)
    if browser is not None:
        picked = _pick_main_page(browser)
        if picked is not None:
            page = picked
            try:
                page.bring_to_front()
            except Exception:  # noqa: BLE001
                pass
    return page


def _confirm_sign_out_modal(page: Any, *, timeout_ms: int = 10_000) -> bool:
    """Confirm the 'Are you sure you want to sign out?' overlay if present."""
    primary = page.locator("[data-qa-id='overlay-modal-primary-button']").first
    try:
        primary.wait_for(state="visible", timeout=timeout_ms)
    except Exception:  # noqa: BLE001
        return False
    try:
        box = primary.bounding_box()
        if box:
            page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        else:
            primary.click(timeout=timeout_ms, force=True)
    except Exception:  # noqa: BLE001
        if not _click_qa_host(page, "overlay-modal-primary-button", timeout_ms=timeout_ms):
            return False
    page.wait_for_timeout(2_500)
    return True


def _open_profile_dropdown(page: Any, *, timeout_ms: int = 15_000) -> None:
    """Open profile menu; do not toggle-close if already open."""
    if page.locator("[data-qa-id='profile-dropdown']").count() > 0:
        return
    from .ui_runner import _open_profile_menu

    _open_profile_menu(page, timeout_ms=timeout_ms)
    page.wait_for_timeout(600)
    if page.locator("[data-qa-id='profile-dropdown']").count() > 0:
        return
    # Avatar click may have closed an already-open menu — open once more.
    _open_profile_menu(page, timeout_ms=timeout_ms)
    page.wait_for_timeout(600)
    if page.locator("[data-qa-id='profile-dropdown']").count() == 0:
        raise RuntimeError("Profile dropdown did not open")


def logout_app(page: Any, *, timeout_ms: int = 15_000, browser: Any | None = None) -> None:
    """Profile menu → Sign out (+ confirm overlay)."""
    page = ensure_main_shell(page, browser)
    if not is_app_logged_in(page):
        log.info("App logout: already signed out")
        return

    _open_profile_dropdown(page, timeout_ms=timeout_ms)
    so = page.locator("[data-qa-id='profile-menu-item-sign-out']").first
    if so.count() == 0:
        raise RuntimeError("Could not find Sign out in profile menu")
    box = so.bounding_box()
    if box:
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    elif not _click_qa_host(page, "profile-menu-item-sign-out", timeout_ms=timeout_ms):
        raise RuntimeError("Could not click Sign out in profile menu")
    page.wait_for_timeout(800)
    _confirm_sign_out_modal(page, timeout_ms=timeout_ms)
    page.wait_for_timeout(2_000)
    # Wait until sign-in appears or auth state flips.
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if not is_app_logged_in(page):
            return
        page.wait_for_timeout(500)
    if is_app_logged_in(page):
        raise RuntimeError("Sign out clicked but app still appears logged in")


def switch_workspace_app(page: Any, *, timeout_ms: int = 15_000, browser: Any | None = None) -> None:
    """Profile → first non-selected / inactive workspace option."""
    page = ensure_main_shell(page, browser)
    if not is_app_logged_in(page):
        raise RuntimeError("Cannot switch workspace while logged out")
    _open_profile_dropdown(page, timeout_ms=timeout_ms)
    page.wait_for_timeout(400)

    # Some builds nest workspaces under "All Companies".
    for lab in ("All Companies", "All companies", "Workspaces", "Switch workspace"):
        try:
            loc = page.get_by_text(lab, exact=False).first
            if loc.is_visible(timeout=800):
                loc.click(timeout=timeout_ms, force=True)
                page.wait_for_timeout(1_000)
                break
        except Exception:  # noqa: BLE001
            continue

    # Prefer inactive company cards (aria-label + md-avatar), skipping the active one.
    clicked = page.evaluate(
        """() => {
          const root =
            document.querySelector("[data-qa-id='profile-dropdown']") || document.body;
          const buttons = Array.from(
            root.querySelectorAll("button[aria-label], md-button[aria-label]")
          );
          for (const b of buttons) {
            const selected = (b.getAttribute("aria-selected") || "").toLowerCase() === "true";
            const active = (b.getAttribute("aria-current") || "").toLowerCase() === "true";
            const hasAvatar = !!b.querySelector("md-avatar");
            if (!hasAvatar || selected || active) continue;
            const btn =
              b.shadowRoot?.querySelector("button") || b.querySelector("button") || b;
            btn.dispatchEvent(
              new MouseEvent("click", { bubbles: true, cancelable: true, view: window })
            );
            return b.getAttribute("aria-label") || "workspace";
          }
          return null;
        }"""
    )
    if clicked:
        log.info("Switched workspace via company card: %s", clicked)
        page.wait_for_timeout(2_500)
        return

    candidates = page.locator(
        "[data-testid='workspace-option']:not([aria-selected='true']), "
        "[role='option']:not([aria-selected='true']), "
        "button[aria-label]:has(md-avatar)"
    )
    count = candidates.count()
    for i in range(count):
        opt = candidates.nth(i)
        try:
            selected = (opt.get_attribute("aria-selected") or "").lower() == "true"
            if selected:
                continue
            if opt.is_visible(timeout=800):
                opt.click(timeout=timeout_ms, force=True)
                page.wait_for_timeout(2_500)
                return
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError(
        "No alternate workspace option found (need another company on this user)"
    )


def ensure_logged_out(page: Any, *, timeout_ms: int = 15_000, browser: Any | None = None) -> Any:
    page = ensure_main_shell(page, browser)
    if is_app_logged_in(page):
        logout_app(page, timeout_ms=timeout_ms, browser=browser)
        page = ensure_main_shell(page, browser)
        page.wait_for_timeout(2_000)
        if is_app_logged_in(page):
            raise RuntimeError("Logout did not reach sign-in screen")
    return page