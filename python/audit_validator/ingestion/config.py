"""Configuration for the RabbitMQ → MongoDB ingestion service.

Ported from the `audit-sense` Node service. This drains the platform's
*subscription* queues (catch-all routing) into MongoDB so the audit UI always has
fresh, complete raw + enriched pairs. It is intentionally separate from the
validator's per-run resolver tap (``RAW_EVENTS_QUEUE`` / ``ENRICHED_EVENTS_QUEUE``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    val = os.getenv(name)
    return val if val not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class QueueBinding:
    """One queue → Mongo collection mapping."""

    name: str          # a friendly name for logs (raw / enriched / dlq)
    queue: str         # RabbitMQ queue to consume
    collection: str    # Mongo collection to write into


@dataclass(frozen=True)
class IngestionConfig:
    rabbitmq_url: str
    mongo_url: str
    mongo_db: str
    prefetch: int
    reconnect_delay_sec: float
    flush_interval_sec: float
    max_insert_retries: int
    insert_retry_delay_sec: float
    cleanup_interval_sec: float
    max_docs_per_operation: int
    purge_on_start: bool = False
    bindings: list[QueueBinding] = field(default_factory=list)


def load_ingestion_config(
    *,
    rabbitmq_url: str | None = None,
    mongo_url: str | None = None,
    mongo_db: str | None = None,
    mongo_raw: str | None = None,
    mongo_enriched: str | None = None,
    mongo_dlq: str | None = None,
) -> IngestionConfig:
    """Resolve ingestion config from explicit args first, then env.

    Defaults to the preprod automation test taps (same as ``RAW_EVENTS_QUEUE`` /
    ``ENRICHED_EVENTS_QUEUE``) so Mongo fills from queues that exist and hold backlog.
    Platform mains are ``mt.platform.raw_events.resolver.queue`` and
    ``mt.platform.events.notification.queue`` — leave those to the resolver.
    """
    raw_queue = _env(
        "INGEST_RAW_QUEUE",
        _env("RABBITMQ_RAW_QUEUE", "mt.platform,resolver.raw_events_test_queue"),
    )
    enriched_queue = _env(
        "INGEST_ENRICHED_QUEUE",
        _env(
            "RABBITMQ_ENRICHED_QUEUE",
            "mt.platform,resolver.enriched_events_test_queue",
        ),
    )
    dlq_queue = _env(
        "INGEST_DLQ_QUEUE",
        _env("RABBITMQ_DLQ_QUEUE", "mt.platform.raw_events.resolver.dlq"),
    )

    raw_col = mongo_raw or _env("MONGO_COLLECTION_RAW", "raw")
    enriched_col = mongo_enriched or _env("MONGO_COLLECTION_ENRICHED", "enriched")
    dlq_col = mongo_dlq or _env("MONGO_COLLECTION_DLQ", "dlq")

    return IngestionConfig(
        rabbitmq_url=rabbitmq_url or _env("INGEST_RABBITMQ_URL", _env("RABBITMQ_URL", "amqp://localhost:5672/%2F")),
        mongo_url=mongo_url or _env("MONGO_DB_URL", "mongodb://localhost:27017"),
        mongo_db=mongo_db or _env("MONGO_DB_NAME", "AuditLogsPreprod"),
        prefetch=_env_int("INGEST_PREFETCH", 100),
        reconnect_delay_sec=_env_int("INGEST_RECONNECT_DELAY_MS", 5000) / 1000.0,
        flush_interval_sec=_env_int("INGEST_BATCH_FLUSH_INTERVAL_MS", 5000) / 1000.0,
        max_insert_retries=_env_int("INGEST_BATCH_MAX_INSERT_RETRIES", 10),
        insert_retry_delay_sec=_env_int("INGEST_BATCH_INSERT_RETRY_DELAY_MS", 2000) / 1000.0,
        cleanup_interval_sec=_env_int("INGEST_CLEANUP_INTERVAL_MS", 30000) / 1000.0,
        max_docs_per_operation=_env_int(
            "INGEST_CLEANUP_MAX_DOCS_PER_OPERATION",
            _env_int("CLEANUP_MAX_DOCS_PER_OPERATION", 20),
        ),
        purge_on_start=_env_bool("INGEST_PURGE_ON_START", False),
        bindings=[
            QueueBinding("raw", raw_queue, raw_col),
            QueueBinding("enriched", enriched_queue, enriched_col),
            QueueBinding("dlq", dlq_queue, dlq_col),
        ],
    )
