"""RabbitMQ queue depth inspection (passive declare)."""

from __future__ import annotations

import logging

import pika

from ..config import RabbitMQConfig

log = logging.getLogger(__name__)

# Preprod resolver + notification pipeline queues (monitoring only).
RESOLVER_DLQ = "mt.platform.raw_events.resolver.dlq"
RESOLVER_QUEUE = "mt.platform.raw_events.resolver.queue"
PLATFORM_RAW_PAYLOAD_QUEUE = "mt.platform.resolver.rawpayload"
PLATFORM_ENRICHED_TEST_QUEUE = "mt.platform.events.reporing.testing"
PLATFORM_NOTIFICATION_QUEUE = "mt.platform.events.notification.queue"


def get_queue_depths(
    rmq: RabbitMQConfig,
    *,
    extra_queues: tuple[str, ...] | None = None,
) -> dict[str, int]:
    """Return queue name → ready message count (-1 if queue missing)."""
    if extra_queues is None:
        extra_queues = (
            RESOLVER_DLQ,
            RESOLVER_QUEUE,
            rmq.platform_notification_queue,
        )
    names = [rmq.raw_queue, rmq.enriched_queue, rmq.dead_letter_queue, *extra_queues]
    params = pika.URLParameters(rmq.url)
    params.heartbeat = 60
    connection = pika.BlockingConnection(params)
    channel = connection.channel()
    depths: dict[str, int] = {}
    try:
        for name in names:
            try:
                method = channel.queue_declare(queue=name, passive=True)
                depths[name] = int(method.method.message_count)
            except Exception as exc:
                log.debug("Queue `%s` passive declare failed: %s", name, exc)
                depths[name] = -1
    finally:
        connection.close()
    return depths
