"""Resolve which audit environments live ingestion drains."""

from __future__ import annotations

import os

from audit_validator.env_profiles import audit_target_name, get_audit_profile, mongo_db_for_profile


def ingest_target_names() -> list[str]:
    """Audit targets to drain in parallel (each uses its own RabbitMQ vhost + Mongo DB).

    ``INGEST_TARGETS=pp,qa`` runs consumers on ``mt-connect-preprod`` →
    ``AuditLogsPreprod`` and ``mt-connect-qa`` → ``AuditLogsQA`` at the same time.
    When unset, only the active ``AUDIT_TARGET`` lane is used.
    """
    raw = os.getenv("INGEST_TARGETS", "").strip()
    if not raw:
        return [audit_target_name()]
    seen: set[str] = set()
    names: list[str] = []
    for part in raw.split(","):
        key = part.strip().lower()
        if not key:
            continue
        profile = get_audit_profile(key)
        if profile.name not in seen:
            seen.add(profile.name)
            names.append(profile.name)
    return names or [audit_target_name()]


def ingest_mongo_databases() -> list[str]:
    return [mongo_db_for_profile(get_audit_profile(name)) for name in ingest_target_names()]


def multi_target_ingestion_enabled() -> bool:
    return len(ingest_target_names()) > 1
