"""Email reader and verification client for Gmail IMAP."""

from __future__ import annotations

import email
from email.header import decode_header
import imaplib
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class EmailMessage:
    msg_id: str
    subject: str
    from_addr: str
    to_addr: str
    date_str: str
    received_at: datetime | None = None
    plain_body: str = ""
    html_body: str = ""
    raw_body: str = ""


class GmailInboxReader:
    """Read and search emails from Gmail IMAP using an App Password."""

    def __init__(
        self,
        email_address: str | None = None,
        app_password: str | None = None,
        host: str = "imap.gmail.com",
        port: int = 993,
    ) -> None:
        self.email_address = email_address or os.getenv("GMAIL_USER") or os.getenv("QA_EMAIL_USER") or ""
        self.app_password = app_password or os.getenv("GMAIL_APP_PASSWORD") or os.getenv("QA_EMAIL_APP_PASSWORD") or ""
        self.host = host
        self.port = port
        self._client: imaplib.IMAP4_SSL | None = None

    def connect(self) -> imaplib.IMAP4_SSL:
        if not self.email_address or not self.app_password:
            raise ValueError(
                "Gmail credentials missing. Please set GMAIL_USER and GMAIL_APP_PASSWORD in .env or pass them directly."
            )
        log.info("Connecting to IMAP %s:%d for %s", self.host, self.port, self.email_address)
        client = imaplib.IMAP4_SSL(self.host, self.port)
        clean_pwd = self.app_password.replace(" ", "")
        client.login(self.email_address, clean_pwd)
        self._client = client
        return client

    def close(self) -> None:
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            try:
                self._client.logout()
            except Exception:
                pass
            self._client = None

    def _decode_header_str(self, header_val: str | None) -> str:
        if not header_val:
            return ""
        decoded_parts = decode_header(header_val)
        result = []
        for content, enc in decoded_parts:
            if isinstance(content, bytes):
                enc = enc or "utf-8"
                try:
                    result.append(content.decode(enc, errors="replace"))
                except Exception:
                    result.append(content.decode("utf-8", errors="replace"))
            else:
                result.append(str(content))
        return "".join(result)

    def fetch_recent_emails(
        self,
        folder: str = "INBOX",
        limit: int = 10,
        since_time: datetime | None = None,
    ) -> list[EmailMessage]:
        """Fetch recent emails from folder, optionally filtered by timestamp."""
        client = self.connect()
        try:
            status, _ = client.select(folder)
            if status != "OK":
                raise RuntimeError(f"Failed to select folder {folder}")

            search_query = "ALL"
            if since_time:
                since_date_str = since_time.strftime("%d-%b-%Y")
                search_query = f'(SINCE "{since_date_str}")'

            status, data = client.search(None, search_query)
            if status != "OK" or not data or not data[0]:
                log.info("No emails found with search query: %s", search_query)
                return []

            msg_ids = data[0].split()
            recent_ids = msg_ids[-limit:]
            recent_ids.reverse()

            messages: list[EmailMessage] = []
            for mid in recent_ids:
                res, msg_data = client.fetch(mid, "(RFC822)")
                if res != "OK" or not msg_data:
                    continue

                raw_email = None
                for part in msg_data:
                    if isinstance(part, tuple) and len(part) >= 2:
                        raw_email = part[1]
                        break

                if not raw_email:
                    continue

                msg = email.message_from_bytes(raw_email)
                subject = self._decode_header_str(msg.get("Subject"))
                from_addr = self._decode_header_str(msg.get("From"))
                to_addr = self._decode_header_str(msg.get("To"))
                date_str = self._decode_header_str(msg.get("Date"))

                plain_body = ""
                html_body = ""

                if msg.is_multipart():
                    for part in msg.walk():
                        ctype = part.get_content_type()
                        cdispo = str(part.get("Content-Disposition"))
                        if "attachment" in cdispo:
                            continue
                        payload = part.get_payload(decode=True)
                        if payload:
                            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                            if ctype == "text/plain":
                                plain_body += text + "\n"
                            elif ctype == "text/html":
                                html_body += text + "\n"
                else:
                    payload = msg.get_payload(decode=True)
                    if payload:
                        ctype = msg.get_content_type()
                        text = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
                        if ctype == "text/html":
                            html_body = text
                        else:
                            plain_body = text

                email_obj = EmailMessage(
                    msg_id=mid.decode("ascii", errors="ignore"),
                    subject=subject,
                    from_addr=from_addr,
                    to_addr=to_addr,
                    date_str=date_str,
                    plain_body=plain_body.strip(),
                    html_body=html_body.strip(),
                    raw_body=(plain_body or html_body).strip(),
                )
                messages.append(email_obj)

            return messages
        finally:
            self.close()

    def wait_for_email(
        self,
        subject_regex: str | None = None,
        body_regex: str | None = None,
        recipient: str | None = None,
        since_time: datetime | None = None,
        timeout_seconds: int = 30,
        poll_interval: float = 3.0,
    ) -> EmailMessage | None:
        """Poll the inbox until an email matching the criteria arrives or timeout."""
        start_time = time.time()
        log.info(
            "Waiting up to %ds for email matching subject_regex=%r body_regex=%r...",
            timeout_seconds,
            subject_regex,
            body_regex,
        )

        subj_pat = re.compile(subject_regex, re.IGNORECASE) if subject_regex else None
        body_pat = re.compile(body_regex, re.IGNORECASE) if body_regex else None

        while time.time() - start_time < timeout_seconds:
            try:
                emails = self.fetch_recent_emails(limit=15, since_time=since_time)
                for em in emails:
                    if recipient and recipient.lower() not in em.to_addr.lower():
                        continue
                    if subj_pat and not subj_pat.search(em.subject):
                        continue
                    if body_pat and not body_pat.search(em.raw_body):
                        continue

                    log.info("Matching email found! Subject: %r (From: %s)", em.subject, em.from_addr)
                    return em
            except Exception as e:
                log.warning("Polling error reading IMAP: %s", e)

            time.sleep(poll_interval)

        log.warning("Timed out waiting for matching email after %ds", timeout_seconds)
        return None
