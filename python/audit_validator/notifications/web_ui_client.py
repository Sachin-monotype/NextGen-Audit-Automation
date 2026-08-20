"""Playwright UI client for NextGen QA Notification Center."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page

log = logging.getLogger(__name__)


@dataclass
class WebNotificationItem:
    text: str
    time_text: str = ""
    is_unread: bool = False
    details_link: str = ""
    expanded_details: list[str] = field(default_factory=list)


class NextGenNotificationUIClient:
    """Automate login and Notification Center interactions on NextGen QA portal."""

    def __init__(
        self,
        base_url: str | None = None,
        email: str | None = None,
        password: str | None = None,
        headless: bool = True,
        user_data_dir: str | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("NEXTGEN_QA_URL") or "https://connect-preprod.monotype-pp.com").rstrip("/")
        self.email = email or os.getenv("QA_LOGIN_EMAIL") or os.getenv("GMAIL_USER") or "agentqatest@gmail.com"
        self.password = password or os.getenv("QA_LOGIN_PASSWORD") or ""
        self.headless = headless
        self.user_data_dir = user_data_dir

        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    def start(self) -> Page:
        """Start Playwright browser and open page."""
        log.info("Launching browser (headless=%s) for %s", self.headless, self.base_url)
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=self.headless,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        self._context = self._browser.new_context(
            viewport={"width": 1440, "height": 900},
            ignore_https_errors=True,
        )
        self._page = self._context.new_page()
        return self._page

    def close(self) -> None:
        """Close browser resources."""
        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None

    def ensure_logged_in(self) -> None:
        """Navigate to portal and log in if prompted."""
        if not self._page:
            self.start()

        page = self._page
        target_url = f"{self.base_url}/notifications"
        log.info("Navigating to %s", target_url)
        page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3000)

        # Check if redirected to Auth0 or Monotype login
        if "auth0" in page.url.lower() or "login" in page.url.lower() or page.locator("input[type='email']").count() > 0:
            log.info("Login form detected at %s. Attempting login for %s...", page.url, self.email)
            if not self.password:
                log.warning("No password provided in QA_LOGIN_PASSWORD. Waiting for manual/SSO login...")
            else:
                try:
                    # Fill username / email
                    email_input = page.locator("input[type='email'], input[name='username'], input[name='email']").first
                    if email_input.is_visible(timeout=5000):
                        email_input.fill(self.email)

                    # Click Continue / Submit if multi-step login
                    continue_btn = page.locator("button:has-text('Continue'), button[type='submit']").first
                    if continue_btn.is_visible(timeout=2000):
                        continue_btn.click()
                        page.wait_for_timeout(2000)

                    # Fill password
                    pwd_input = page.locator("input[type='password'], input[name='password']").first
                    if pwd_input.is_visible(timeout=5000):
                        pwd_input.fill(self.password)
                        submit_btn = page.locator("button[type='submit'], button:has-text('Log In'), button:has-text('Sign In')").first
                        submit_btn.click()
                        page.wait_for_timeout(5000)
                except Exception as e:
                    log.warning("Automated login encounter: %s", e)

        # Ensure we are on /notifications
        if "/notifications" not in page.url:
            log.info("Navigating to notifications page: %s", target_url)
            page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

    def mark_all_as_read(self) -> bool:
        """Click 'Mark all as read' button on Notifications page."""
        self.ensure_logged_in()
        page = self._page
        log.info("Looking for 'Mark all as read' button...")
        try:
            btn = page.locator("button:has-text('Mark all as read'), [data-testid='mark-all-read']").first
            if btn.is_visible(timeout=5000) and btn.is_enabled():
                btn.click()
                log.info("Clicked 'Mark all as read'")
                page.wait_for_timeout(2000)
                return True
            else:
                log.info("'Mark all as read' button not enabled or already read.")
                return False
        except Exception as e:
            log.warning("Could not click 'Mark all as read': %s", e)
            return False

    def fetch_notifications(self) -> list[WebNotificationItem]:
        """Scrape all visible notification items from the Notifications UI."""
        self.ensure_logged_in()
        page = self._page
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        items: list[WebNotificationItem] = []
        # Find notification row containers
        # NextGen UI typically uses list items or card rows with typography
        rows = page.locator("div[class*='notification'], li[class*='notification'], tr[class*='notification']").all()
        
        # Fallback to inspecting text blocks under section groups (Today, Yesterday, etc.)
        if not rows:
            rows = page.locator("section div, [role='listitem'], div[class*='item']").all()

        log.info("Scraping notifications from UI (found %d candidate rows)...", len(rows))
        for r in rows:
            try:
                txt = r.inner_text().strip()
                if not txt or len(txt) < 5 or "Mark all as read" in txt or "Filter by" in txt:
                    continue
                # Split lines
                lines = [l.strip() for l in txt.split("\n") if l.strip()]
                main_text = lines[0] if lines else txt
                time_text = lines[1] if len(lines) > 1 else ""
                
                unread_dot = r.locator("span[class*='dot'], div[class*='unread'], [class*='blue']").count() > 0
                items.append(
                    WebNotificationItem(
                        text=main_text,
                        time_text=time_text,
                        is_unread=unread_dot,
                    )
                )
            except Exception:
                continue

        return items

    def wait_for_notification(
        self,
        expected_substring: str | None = None,
        expected_regex: str | None = None,
        timeout_seconds: int = 35,
        poll_interval: float = 4.0,
    ) -> WebNotificationItem | None:
        """Poll the Notifications UI until an expected notification appears."""
        import re
        start_time = time.time()
        pat = re.compile(expected_regex, re.IGNORECASE) if expected_regex else None
        sub = expected_substring.lower() if expected_substring else None

        log.info("Polling Web UI for notification (sub=%r, regex=%r)...", expected_substring, expected_regex)
        while time.time() - start_time < timeout_seconds:
            notifications = self.fetch_notifications()
            for item in notifications:
                if sub and sub in item.text.lower():
                    log.info("Matched notification on UI by substring: %r", item.text)
                    return item
                if pat and pat.search(item.text):
                    log.info("Matched notification on UI by regex: %r", item.text)
                    return item

            time.sleep(poll_interval)

        log.warning("Timed out waiting for notification on UI after %ds", timeout_seconds)
        return None
