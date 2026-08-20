"""Notification Center & Email end-to-end verification framework."""

from .email_client import GmailInboxReader, EmailMessage
from .web_ui_client import NextGenNotificationUIClient, WebNotificationItem
from .matcher import assert_notification_text, MatchResult
from .e2e_runner import E2ENotificationRunner, NotificationTestCase, E2ENotificationResult

__all__ = [
    "GmailInboxReader",
    "EmailMessage",
    "NextGenNotificationUIClient",
    "WebNotificationItem",
    "assert_notification_text",
    "MatchResult",
    "E2ENotificationRunner",
    "NotificationTestCase",
    "E2ENotificationResult",
]
