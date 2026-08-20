"""End-to-End Notification Test Runner (Trigger Cron -> Verify Web UI -> Verify Email)."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .email_client import GmailInboxReader, EmailMessage
from .web_ui_client import NextGenNotificationUIClient, WebNotificationItem
from .matcher import assert_notification_text, MatchResult

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "python"))

from audit_validator.config import load_config
from audit_validator.env_profiles import apply_audit_profile
from audit_validator.cron.payloads import normalize_cron_payload, amqp_routing_key_for_payload
from audit_validator.rabbitmq.publisher import publish_raw_event
from audit_validator.case_keys import cron_case_key
from audit_validator.generation_tracker import record_generation

log = logging.getLogger(__name__)


@dataclass
class NotificationTestCase:
    id: str
    routingKey: str
    operation: str
    trigger: str
    service: str
    recipient: str
    base_file: str
    channels: list[str]  # ["web", "email"]
    expected_web_string: str = ""
    expected_web_regex: str = ""
    expected_email_subject: str = ""
    expected_email_body_regex: str = ""
    target_email: str = ""
    invite_email: str = ""


@dataclass
class ChannelResult:
    channel: str  # "web" | "email"
    status: str   # "PASS" | "FAIL" | "SKIPPED"
    expected: str
    actual: str
    diff: str = ""
    latency_seconds: float = 0.0


@dataclass
class E2ENotificationResult:
    case_id: str
    routingKey: str
    operation: str
    cid: str
    eid: str
    web_result: ChannelResult | None = None
    email_result: ChannelResult | None = None
    overall_status: str = "PASS"  # "PASS" | "FAIL" | "PARTIAL"


class E2ENotificationRunner:
    """Orchestrate end-to-end notification verification across Web and Email channels."""

    def __init__(
        self,
        project_root: Path = PROJECT_ROOT,
        target_gcid: str = "93bbce28-5143-497c-a959-1f9eada55230",
        target_user_id: str = "1a140831-8b1c-404c-ae68-5d3fb2284f78",
        target_profile_id: str = "5e0ff5be-97a1-11f1-ac0d-0e0a04e472ab",
        admin_email: str = "agentqatest@gmail.com",
        headless_browser: bool = True,
    ) -> None:
        self.project_root = project_root
        self.target_gcid = target_gcid
        self.target_user_id = target_user_id
        self.target_profile_id = target_profile_id
        self.admin_email = admin_email
        self.headless_browser = headless_browser

        apply_audit_profile(project_root=self.project_root)
        self.cfg = load_config(self.project_root)

        self.web_client = NextGenNotificationUIClient(
            email=self.admin_email,
            headless=self.headless_browser,
        )
        self.email_reader = GmailInboxReader(email_address=self.admin_email)

    def run_case(self, test_case: NotificationTestCase, pre_clear_web: bool = True) -> E2ENotificationResult:
        """Run single E2E notification test case."""
        log.info("==================================================")
        log.info("Running E2E Notification Test: %s (%s)", test_case.id, test_case.routingKey)
        log.info("Channels to verify: %s", test_case.channels)
        log.info("==================================================")

        start_time = datetime.now(timezone.utc)
        t0 = time.time()

        # Step 1: Pre-clear Web Notifications if requested
        if "web" in test_case.channels and pre_clear_web:
            try:
                log.info("Pre-test cleanup: Marking all notifications as read on Web UI...")
                self.web_client.mark_all_as_read()
            except Exception as e:
                log.warning("Web pre-cleanup warning: %s", e)

        # Step 2: Build & Publish Cron Payload
        base_dir = self.project_root / "python" / "audit_validator" / "data" / "cron_payloads"
        base_path = base_dir / test_case.base_file
        raw = json.loads(base_path.read_text(encoding="utf-8"))

        raw["routingKey"] = test_case.routingKey
        src = raw.setdefault("source", {})
        src["service"] = test_case.service
        src["operation"] = test_case.operation
        src["trigger"] = test_case.trigger

        target_em = test_case.target_email or self.admin_email
        if test_case.invite_email:
            subj = raw.setdefault("subject", {})
            if "email" in subj:
                subj["email"] = test_case.invite_email
            if "expiredInvitations" in subj and isinstance(subj["expiredInvitations"], list):
                for inv in subj["expiredInvitations"]:
                    inv["email"] = test_case.invite_email
            if "expiringUsers" in subj and isinstance(subj["expiringUsers"], list):
                for u in subj["expiringUsers"]:
                    u["email"] = test_case.invite_email
            if "deactivatedUsers" in subj and isinstance(subj["deactivatedUsers"], list):
                for u in subj["deactivatedUsers"]:
                    u["email"] = test_case.invite_email

        payload = normalize_cron_payload(
            raw,
            case_id=test_case.id,
            gcid=self.target_gcid,
            user_id=self.target_user_id,
            profile_id=self.target_profile_id,
        )
        payload["routingKey"] = test_case.routingKey

        cid = payload["xCorrelationId"]
        eid = payload["eventId"]
        amqp_rk = amqp_routing_key_for_payload(payload)

        log.info("Publishing cron event amqp_rk=%s payload_rk=%s cid=%s", amqp_rk, test_case.routingKey, cid[:8])
        publish_raw_event(self.cfg.rabbitmq, payload, amqp_routing_key=amqp_rk)

        try:
            record_generation(
                test_case.operation,
                cid,
                kind="cron",
                project_root=self.project_root,
                case_key=cron_case_key(test_case.id),
                meta={
                    "case_id": test_case.id,
                    "eventId": eid,
                    "profile_id": self.target_profile_id,
                    "customer_id": self.target_gcid,
                    "email": target_em,
                },
            )
        except Exception:
            pass

        # Step 3: Verify Channels
        web_res: ChannelResult | None = None
        email_res: ChannelResult | None = None

        # Verify Web
        if "web" in test_case.channels:
            log.info("Verifying Web Notification Center channel...")
            web_t0 = time.time()
            web_item = self.web_client.wait_for_notification(
                expected_substring=test_case.expected_web_string,
                expected_regex=test_case.expected_web_regex,
                timeout_seconds=30,
            )
            web_lat = time.time() - web_t0

            if web_item:
                match = assert_notification_text(
                    actual_text=web_item.text,
                    expected_text_or_regex=test_case.expected_web_regex or test_case.expected_web_string,
                    is_regex=bool(test_case.expected_web_regex),
                )
                web_res = ChannelResult(
                    channel="web",
                    status="PASS" if match.is_match else "FAIL",
                    expected=test_case.expected_web_string or test_case.expected_web_regex,
                    actual=web_item.text,
                    diff=match.diff,
                    latency_seconds=web_lat,
                )
            else:
                web_res = ChannelResult(
                    channel="web",
                    status="FAIL",
                    expected=test_case.expected_web_string or test_case.expected_web_regex,
                    actual="(No notification received on UI within timeout)",
                    latency_seconds=web_lat,
                )

        # Verify Email
        if "email" in test_case.channels:
            log.info("Verifying Email inbox channel...")
            email_t0 = time.time()
            email_msg = self.email_reader.wait_for_email(
                subject_regex=test_case.expected_email_subject,
                body_regex=test_case.expected_email_body_regex,
                recipient=target_em,
                since_time=start_time,
                timeout_seconds=35,
            )
            email_lat = time.time() - email_t0

            if email_msg:
                email_res = ChannelResult(
                    channel="email",
                    status="PASS",
                    expected=test_case.expected_email_subject,
                    actual=email_msg.subject,
                    latency_seconds=email_lat,
                )
            else:
                email_res = ChannelResult(
                    channel="email",
                    status="FAIL",
                    expected=test_case.expected_email_subject,
                    actual="(No email received in inbox within timeout)",
                    latency_seconds=email_lat,
                )

        # Determine overall status
        statuses = [r.status for r in [web_res, email_res] if r is not None]
        if all(s == "PASS" for s in statuses):
            overall = "PASS"
        elif any(s == "PASS" for s in statuses):
            overall = "PARTIAL"
        else:
            overall = "FAIL"

        return E2ENotificationResult(
            case_id=test_case.id,
            routingKey=test_case.routingKey,
            operation=test_case.operation,
            cid=cid,
            eid=eid,
            web_result=web_res,
            email_result=email_res,
            overall_status=overall,
        )

    def close(self) -> None:
        """Clean up clients."""
        self.web_client.close()
        self.email_reader.close()
