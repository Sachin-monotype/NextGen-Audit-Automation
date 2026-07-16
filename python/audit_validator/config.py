"""Load configuration from project .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .env_profiles import apply_audit_profile
from .project_root import find_project_root
from .rabbitmq.enriched_routing_keys import ENRICHED_ROUTING_KEYS


def _int_env(key: str, default: int) -> int:
    val = os.getenv(key)
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _bool_env(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RabbitMQConfig:
    url: str
    raw_queue: str
    enriched_queue: str
    dead_letter_queue: str
    raw_exchange: str
    enriched_exchange: str
    dead_letter_exchange: str
    raw_queue_passive: bool
    enriched_queue_passive: bool
    enriched_use_wildcard_bind: bool
    consume_dead_letter_queue: bool
    enriched_routing_keys: frozenset[str]
    platform_notification_queue: str

    @property
    def wildcard_bind_mode(self) -> bool:
        """Enriched queue bound with `#` — skip per-key coverage, validate by correlation only."""
        return self.enriched_use_wildcard_bind


@dataclass(frozen=True)
class AppConfig:
    project_root: Path
    rabbitmq: RabbitMQConfig
    event_wait_timeout_ms: int
    settle_after_flows_sec: float
    enriched_catchup_sec: float
    purge_queues_on_e2e: bool
    purge_enriched_queue: bool
    purge_test_queues_on_e2e: bool
    enriched_backlog_drain_sec: float
    backlog_drain_sec: float
    validate_captured_only: bool
    raw_events_dir: Path
    enriched_events_dir: Path
    dead_letter_events_dir: Path


def load_config(project_root: Path | None = None) -> AppConfig:
    root = project_root or find_project_root()
    apply_audit_profile(project_root=root)

    timeout_ms = _int_env("EVENT_WAIT_TIMEOUT_MS", 30_000)
    settle_sec = float(os.getenv("PYTHON_SETTLE_SEC", "120"))
    enriched_catchup_sec = float(os.getenv("ENRICHED_CATCHUP_SEC", "90"))

    enriched_queue = os.getenv(
        "ENRICHED_EVENTS_QUEUE",
        "mt.platform,resolver.enriched_events_test_queue",
    )
    raw_queue = os.getenv(
        "RAW_EVENTS_QUEUE",
        "mt.platform,resolver.raw_events_test_queue",
    )

    # PP platform queues are pre-provisioned — consume passively (do not rebind).
    default_platform_passive = raw_queue.startswith("mt.platform")
    raw_passive = _bool_env("RAW_QUEUE_PASSIVE", default_platform_passive)

    default_enriched_passive = (
        enriched_queue.endswith("notification_automation.queue")
        or enriched_queue.endswith("reporing.testing")
        or enriched_queue.endswith("enrichpayload")
        or enriched_queue.endswith("notification.queue")
        or "enriched_events_test_queue" in enriched_queue
    )
    enriched_passive = _bool_env("ENRICHED_QUEUE_PASSIVE", default_enriched_passive)

    # Platform tap queues (resolver.*payload, reporing.testing) use platform bindings.
    enriched_wildcard = _bool_env("ENRICHED_QUEUE_WILDCARD_BIND", False)

    default_no_dl_consume = (
        raw_queue.endswith("rawpayload")
        or "raw_events_test_queue" in raw_queue
        or enriched_queue.endswith(("reporing.testing", "enrichpayload"))
        or "enriched_events_test_queue" in enriched_queue
    )
    consume_dl = _bool_env("CONSUME_DEAD_LETTER_QUEUE", not default_no_dl_consume)

    platform_notification_queue = os.getenv(
        "PLATFORM_NOTIFICATION_QUEUE",
        "mt.platform.events.notification.queue",
    )

    purge_test_queues = _bool_env("PURGE_TEST_QUEUES_ON_E2E", True)
    validate_captured = _bool_env("VALIDATE_CAPTURED_ONLY", enriched_wildcard)

    default_backlog_drain = 0.0 if purge_test_queues else 60.0
    backlog_drain = float(os.getenv("ENRICHED_BACKLOG_DRAIN_SEC", str(default_backlog_drain)))
    backlog_only_drain = float(os.getenv("BACKLOG_DRAIN_SEC", "600"))

    url = os.getenv("RABBITMQ_URL", "amqp://localhost:5672/%2F")

    return AppConfig(
        project_root=root,
        rabbitmq=RabbitMQConfig(
            url=url,
            raw_queue=raw_queue,
            enriched_queue=enriched_queue,
            dead_letter_queue=os.getenv(
                "DEAD_LETTER_QUEUE", "mt.platform.raw_events.resolver.dlq"
            ),
            raw_exchange=os.getenv("RAW_EVENTS_EXCHANGE", "mt.platform.raw_events"),
            enriched_exchange=os.getenv("ENRICHED_EVENTS_EXCHANGE", "mt.platform.events"),
            dead_letter_exchange=os.getenv(
                "DEAD_LETTER_EXCHANGE", "mt.platform.raw_events.resolver.dlx"
            ),
            raw_queue_passive=raw_passive,
            enriched_queue_passive=enriched_passive,
            enriched_use_wildcard_bind=enriched_wildcard,
            consume_dead_letter_queue=consume_dl,
            enriched_routing_keys=ENRICHED_ROUTING_KEYS,
            platform_notification_queue=platform_notification_queue,
        ),
        event_wait_timeout_ms=timeout_ms,
        settle_after_flows_sec=settle_sec,
        enriched_catchup_sec=enriched_catchup_sec,
        purge_queues_on_e2e=_bool_env("PURGE_QUEUES_ON_E2E", False),
        purge_enriched_queue=_bool_env("PURGE_ENRICHED_QUEUE", False),
        purge_test_queues_on_e2e=purge_test_queues,
        enriched_backlog_drain_sec=backlog_drain,
        backlog_drain_sec=backlog_only_drain,
        validate_captured_only=validate_captured,
        raw_events_dir=root / "payload" / "raw",
        enriched_events_dir=root / "payload" / "enrich",
        dead_letter_events_dir=root / "temp" / "dlq",
    )
