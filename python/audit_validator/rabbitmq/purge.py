"""Purge automation RabbitMQ queues before/after E2E runs."""

from __future__ import annotations

import logging

import pika

from ..config import RabbitMQConfig

log = logging.getLogger(__name__)


def purge_test_queues(rmq: RabbitMQConfig) -> dict[str, int]:
    """Purge automation tap queues only (raw + enriched). Does not touch DLQ."""
    return purge_queues(
        rmq,
        include_enriched=True,
        include_dead_letter=False,
        queues=[rmq.raw_queue, rmq.enriched_queue],
    )


def purge_queues(
    rmq: RabbitMQConfig,
    *,
    include_enriched: bool = True,
    include_dead_letter: bool = True,
    queues: list[str] | None = None,
) -> dict[str, int]:
    """
    Remove all messages from raw, enriched, and (optionally) dead-letter queues.

    Returns a mapping of queue name → number of messages purged.
    """
    if queues is not None:
        queue_names = list(queues)
    else:
        queue_names = [rmq.raw_queue]
        if include_enriched:
            queue_names.append(rmq.enriched_queue)
        if include_dead_letter:
            queue_names.append(rmq.dead_letter_queue)

    params = pika.URLParameters(rmq.url)
    params.heartbeat = 60
    connection = pika.BlockingConnection(params)
    from urllib.parse import urlparse

    vhost = urlparse(rmq.url).path or "/"
    if vhost in {"/%2F", "%2F"}:
        vhost = "/"
    log.info("RabbitMQ vhost: %s", vhost)

    purged: dict[str, int] = {}
    try:
        for queue_name in queue_names:
            try:
                ch = connection.channel()
                ch.queue_declare(queue=queue_name, passive=True)
            except Exception as exc:
                log.warning("Queue `%s` not found — skipping purge: %s", queue_name, exc)
                purged[queue_name] = 0
                continue

            result = ch.queue_purge(queue=queue_name)
            count = int(getattr(result.method, "message_count", 0))
            purged[queue_name] = count
            log.info("Purged %d message(s) from `%s`", count, queue_name)
            ch.close()
    finally:
        connection.close()

    return purged
