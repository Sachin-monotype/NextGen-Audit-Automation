"""Publish raw audit envelopes to the platform raw-events exchange."""

from __future__ import annotations

import json
import logging

import pika
from pika import BasicProperties

from ..config import RabbitMQConfig
from ..models import JsonDict

log = logging.getLogger(__name__)


def publish_raw_event(
    rmq: RabbitMQConfig,
    payload: JsonDict,
    *,
    routing_key: str | None = None,
    amqp_routing_key: str | None = None,
) -> str:
    """
    Publish a cron/scheduler raw envelope to mt.platform.raw_events (or configured exchange).

    ``amqp_routing_key`` is the RabbitMQ binding key (often ``raw.events`` for LMS/login).
    ``routing_key`` / payload.routingKey is the notification routing key inside the JSON body.

    Returns the AMQP routing key used.
    """
    amqp_rk = (amqp_routing_key or routing_key or "").strip()
    if not amqp_rk:
        raise ValueError("amqp_routing_key required")

    cid = str(payload.get("xCorrelationId") or "")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    props = BasicProperties(
        content_type="application/json",
        delivery_mode=2,
        headers={"x-correlation-id": cid} if cid else None,
    )

    conn = pika.BlockingConnection(pika.URLParameters(rmq.url))
    try:
        ch = conn.channel()
        try:
            ch.exchange_declare(exchange=rmq.raw_exchange, passive=True)
        except Exception:
            ch.exchange_declare(exchange=rmq.raw_exchange, exchange_type="topic", durable=True)
        ch.basic_publish(
            exchange=rmq.raw_exchange,
            routing_key=amqp_rk,
            body=body,
            properties=props,
        )
        payload_rk = str(payload.get("routingKey") or "")
        log.info(
            "Published raw cron event amqp_rk=%s payload_rk=%s correlation=%s",
            amqp_rk,
            payload_rk or "(none)",
            cid[:8],
        )
    finally:
        conn.close()
    return amqp_rk
