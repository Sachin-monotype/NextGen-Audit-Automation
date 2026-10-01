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

    from .connection import url_parameters

    params = url_parameters(rmq.url)
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


def delete_queue_from_broker(
    rmq_url: str,
    queue_name: str,
    *,
    if_unused: bool = False,
    if_empty: bool = False,
) -> dict[str, Any]:
    """Delete a queue from the RabbitMQ broker."""
    from typing import Any
    from .connection import url_parameters

    if not rmq_url:
        return {"ok": False, "error": "RabbitMQ URL not configured."}

    params = url_parameters(rmq_url)
    params.heartbeat = 60
    connection = None
    try:
        connection = pika.BlockingConnection(params)
        ch = connection.channel()
        result = ch.queue_delete(queue=queue_name, if_unused=if_unused, if_empty=if_empty)
        count = int(getattr(result.method, "message_count", 0))
        log.info("Deleted queue `%s` (%d messages discarded)", queue_name, count)
        ch.close()
        return {"ok": True, "queue": queue_name, "message_count": count}
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "NOT_FOUND" in msg or "404" in msg:
            return {"ok": False, "error": f"Queue '{queue_name}' not found on broker."}
        return {"ok": False, "error": msg}
    finally:
        try:
            if connection and connection.is_open:
                connection.close()
        except Exception:
            pass


def declare_and_bind_queue(
    rmq_url: str,
    queue_name: str,
    collection: str = "enriched",
    *,
    durable: bool = True,
) -> dict[str, Any]:
    """Declare a queue on the broker and bind to the appropriate event exchange."""
    from typing import Any
    from .connection import url_parameters

    if not rmq_url:
        return {"ok": False, "error": "RabbitMQ URL not configured."}

    params = url_parameters(rmq_url)
    params.heartbeat = 60
    connection = None
    try:
        connection = pika.BlockingConnection(params)
        ch = connection.channel()
        try:
            ch.queue_declare(queue=queue_name, durable=durable)
        except Exception:
            try:
                ch = connection.channel()
                ch.queue_declare(queue=queue_name, passive=True)
            except Exception as e:
                return {"ok": False, "error": f"Failed to declare queue '{queue_name}': {e}"}

        exchange = ""
        if collection == "raw":
            exchange = "mt.platform.raw_events"
        elif collection == "enriched":
            exchange = "mt.platform.events"

        if exchange:
            try:
                ch.queue_bind(queue=queue_name, exchange=exchange, routing_key="#")
            except Exception as exc:
                log.warning("Could not bind queue `%s` to exchange `%s`: %s", queue_name, exchange, exc)

        ch.close()
        return {"ok": True, "queue": queue_name, "collection": collection, "exchange": exchange}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    finally:
        try:
            if connection and connection.is_open:
                connection.close()
        except Exception:
            pass


