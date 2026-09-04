"""Configuration for the RabbitMQ → MongoDB ingestion service.

Ported from the `audit-sense` Node service. This drains the platform's
*subscription* queues (catch-all routing) into MongoDB so the audit UI always has
fresh, complete raw + enriched pairs. It is intentionally separate from the
validator's per-run resolver tap (``RAW_EVENTS_QUEUE`` / ``ENRICHED_EVENTS_QUEUE``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace

from audit_validator.env_profiles import (
    get_audit_profile,
    mongo_db_for_profile,
    mongo_url_for_profile,
    rabbitmq_url_for_profile,
)

from .targets import ingest_mongo_databases, ingest_target_names


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


def max_docs_for_mongo_db(db_name: str) -> int:
    """Per-environment cap on docs kept per ``source.operation``."""
    name = (db_name or "").strip()
    low = name.lower()
    default = _env_int("MONGO_RETENTION_MAX_DOCS_PER_OPERATION", 20)
    if "preprod" in low or low.endswith("pp") or name == "AuditLogsPreprod":
        return _env_int("MONGO_RETENTION_MAX_DOCS_PER_OPERATION_PP", 0)
    if "qa" in low or name == "AuditLogsQA":
        return _env_int(
            "MONGO_RETENTION_MAX_DOCS_PER_OPERATION_QA",
            _env_int("INGEST_CLEANUP_MAX_DOCS_PER_OPERATION", default),
        )
    if "uat" in low or name == "AuditLogsUAT":
        return _env_int("MONGO_RETENTION_MAX_DOCS_PER_OPERATION_UAT", 50)
    return default


def keep_hours_for_mongo_db(db_name: str) -> float:
    """Same per-environment window as backend Mongo retention."""
    name = (db_name or "").strip()
    low = name.lower()
    default = float(_env("MONGO_RETENTION_KEEP_HOURS", "3") or "3")
    if "preprod" in low or low.endswith("pp") or name == "AuditLogsPreprod":
        return float(_env("MONGO_RETENTION_KEEP_HOURS_PP", "0.5") or "0.5")
    if "qa" in low or name == "AuditLogsQA":
        return float(_env("MONGO_RETENTION_KEEP_HOURS_QA", str(default)) or default)
    if "uat" in low or name == "AuditLogsUAT":
        return float(_env("MONGO_RETENTION_KEEP_HOURS_UAT", str(default)) or default)
    return default


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
    mongo_databases: tuple[str, ...]
    prefetch: int
    reconnect_delay_sec: float
    flush_interval_sec: float
    max_insert_retries: int
    insert_retry_delay_sec: float
    cleanup_interval_sec: float
    max_docs_per_operation: int
    purge_on_start: bool = False
    auto_purge_enabled: bool = False
    auto_purge_interval_sec: int = 3600
    auto_purge_min_ready: int = 500
    bindings: list[QueueBinding] = field(default_factory=list)


@dataclass(frozen=True)
class IngestLaneConfig:
    """One audit target: dedicated vhost URL + Mongo DB + queue bindings."""

    target: str
    vhost: str
    rabbitmq_url: str
    mongo_url: str
    mongo_db: str
    config: IngestionConfig


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
        _env("RABBITMQ_RAW_QUEUE", "mtraw-automation(DO NOT DELETE)"),
    )
    enriched_queue = _env(
        "INGEST_ENRICHED_QUEUE",
        _env(
            "RABBITMQ_ENRICHED_QUEUE",
            "mtenrich-automation(DO NOT DELETE)",
        ),
    )
    # Prefer active profile DLQ (UAT → mt.raw_dlq[Do not Delete)); never fall back
    # to the platform resolver.dlq when a profile queue is configured.
    profile_dlq = ""
    try:
        from audit_validator.env_profiles import get_audit_profile

        profile_dlq = (get_audit_profile().dead_letter_queue or "").strip()
    except Exception:
        profile_dlq = ""
    dlq_queue = _env(
        "INGEST_DLQ_QUEUE",
        _env(
            "RABBITMQ_DLQ_QUEUE",
            _env("DEAD_LETTER_QUEUE", profile_dlq or "mt.platform.raw_events.resolver.dlq"),
        ),
    )

    raw_col = mongo_raw or _env("MONGO_COLLECTION_RAW", "raw")
    enriched_col = mongo_enriched or _env("MONGO_COLLECTION_ENRICHED", "enriched")
    dlq_col = mongo_dlq or _env("MONGO_COLLECTION_DLQ", "dlq")

    if mongo_db:
        databases = (mongo_db,)
    else:
        resolved = ingest_mongo_databases()
        databases = tuple(resolved) if resolved else (_env("MONGO_DB_NAME", "AuditLogsPreprod"),)

    bindings = [
        QueueBinding("raw", raw_queue, raw_col),
        QueueBinding("enriched", enriched_queue, enriched_col),
        QueueBinding("dlq", dlq_queue, dlq_col),
    ]
    if raw_queue != "mt_test_raw" and _env_bool("INGEST_ADD_TEST_QUEUES", False):
        bindings.append(QueueBinding("raw_test", "mt_test_raw", raw_col))
    if enriched_queue != "mt_test enrich" and _env_bool("INGEST_ADD_TEST_QUEUES", False):
        bindings.append(QueueBinding("enriched_test", "mt_test enrich", enriched_col))

    return IngestionConfig(
        rabbitmq_url=rabbitmq_url or _env("INGEST_RABBITMQ_URL", _env("RABBITMQ_URL", "amqp://localhost:5672/%2F")),
        mongo_url=mongo_url or _env("MONGO_DB_URL", "mongodb://localhost:27017"),
        mongo_db=databases[0],
        mongo_databases=databases,
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
        auto_purge_enabled=_env_bool("INGEST_AUTO_PURGE", False),
        auto_purge_interval_sec=_env_int("INGEST_AUTO_PURGE_INTERVAL_SEC", 3600),
        auto_purge_min_ready=_env_int("INGEST_AUTO_PURGE_MIN_READY", 500),
        bindings=bindings,
    )


def load_ingest_lanes(
    base: IngestionConfig | None = None,
    *,
    rabbitmq_url: str | None = None,
) -> list[IngestLaneConfig]:
    """Build one ingestion lane per ``INGEST_TARGETS`` entry (separate vhost + Mongo DB)."""
    root = base or load_ingestion_config(rabbitmq_url=rabbitmq_url)
    base_rmq = rabbitmq_url or root.rabbitmq_url
    lanes: list[IngestLaneConfig] = []
    for target in ingest_target_names():
        profile = get_audit_profile(target)
        lane_rmq = rabbitmq_url_for_profile(profile) or root.rabbitmq_url
        lane_mongo = mongo_url_for_profile(profile) or root.mongo_url
        mongo_db = mongo_db_for_profile(profile)
        raw_q = root.bindings[0].queue if (root.bindings and root.bindings[0].queue) else profile.ingress_raw_queue
        enriched_q = root.bindings[1].queue if (len(root.bindings) > 1 and root.bindings[1].queue) else profile.ingress_enriched_queue
        dlq_q = (
            (profile.dead_letter_queue or "").strip()
            or (root.bindings[2].queue if len(root.bindings) > 2 else "")
            or "mt.platform.raw_events.resolver.dlq"
        )
        lane_bindings = [
            QueueBinding("raw", raw_q, "raw"),
            QueueBinding("enriched", enriched_q, "enriched"),
            QueueBinding("dlq", dlq_q, "dlq"),
        ]
        lane_config = replace(
            root,
            rabbitmq_url=lane_rmq,
            mongo_url=lane_mongo,
            mongo_db=mongo_db,
            mongo_databases=(mongo_db,),
            bindings=lane_bindings,
        )
        lanes.append(
            IngestLaneConfig(
                target=target,
                vhost=profile.rabbitmq_vhost,
                rabbitmq_url=lane_rmq,
                mongo_url=lane_mongo,
                mongo_db=mongo_db,
                config=lane_config,
            )
        )
    return lanes
