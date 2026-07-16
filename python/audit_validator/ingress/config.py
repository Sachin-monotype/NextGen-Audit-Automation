"""Ingress queue / RabbitMQ settings — all names come from environment."""

from __future__ import annotations

import os


class IngressConfigError(ValueError):
    pass


def ingress_queue_names() -> tuple[str, str]:
    raw = (os.getenv("INGRESS_RAW_QUEUE") or "").strip()
    enriched = (os.getenv("INGRESS_ENRICHED_QUEUE") or "").strip()
    missing = []
    if not raw:
        missing.append("INGRESS_RAW_QUEUE")
    if not enriched:
        missing.append("INGRESS_ENRICHED_QUEUE")
    if missing:
        raise IngressConfigError(
            "Set " + " and ".join(missing) + " in .env "
            "(PP desktop/plugin test queues on vhost mt-connect-preprod). "
            "See .env.example."
        )
    return raw, enriched


def ingress_rabbitmq_url(base_url: str | None = None) -> str:
    explicit = (os.getenv("INGRESS_RABBITMQ_URL") or "").strip()
    if explicit:
        return explicit
    url = (base_url or os.getenv("RABBITMQ_URL") or "").strip()
    if not url:
        return url
    if url.rstrip("/").endswith("/mt-connect-preprod"):
        return url
    if url.rstrip("/").endswith("/mt-connect"):
        return url.rsplit("/", 1)[0] + "/mt-connect-preprod"
    return url
