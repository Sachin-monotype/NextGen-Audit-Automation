"""Peek messages on tap queues via the RabbitMQ management HTTP API (requeue, no loss)."""

from __future__ import annotations

import json
import logging
import urllib.parse
from typing import TYPE_CHECKING

try:
    import requests
except ImportError:
    requests = None  # type: ignore

from ..models import JsonDict

if TYPE_CHECKING:
    from .collector import QueueEventCollector

log = logging.getLogger(__name__)

BATCH_SIZE = 100


def _parse_amqp_url(url: str) -> tuple[str, str, str, str]:
    parsed = urllib.parse.urlparse(url)
    vhost = urllib.parse.unquote(parsed.path.lstrip("/") or "/")
    return parsed.hostname or "", vhost, parsed.username or "", parsed.password or ""


def _correlation_id(payload: JsonDict) -> str | None:
    cid = payload.get("xCorrelationId")
    return cid if isinstance(cid, str) and cid else None


def _operation(payload: JsonDict) -> str:
    source = payload.get("source") or {}
    return str(source.get("operation") or "unknown")


def queue_depth(url: str, queue: str) -> int | None:
    if requests is None:
        return None
    host, vhost, user, password = _parse_amqp_url(url)
    if not host or not user:
        return None
    vhost_enc = urllib.parse.quote(vhost, safe="")
    queue_enc = urllib.parse.quote(queue, safe="")
    api = f"https://{host}/api/queues/{vhost_enc}/{queue_enc}"
    try:
        resp = requests.get(api, auth=(user, password), timeout=30)
        resp.raise_for_status()
        return int(resp.json().get("messages", 0))
    except Exception as exc:
        log.debug("Management depth for `%s` failed: %s", queue, exc)
        return None


def _fetch_batch(url: str, queue: str, count: int) -> list[tuple[JsonDict, str | None]]:
    if requests is None or count <= 0:
        return []
    host, vhost, user, password = _parse_amqp_url(url)
    vhost_enc = urllib.parse.quote(vhost, safe="")
    queue_enc = urllib.parse.quote(queue, safe="")
    api = f"https://{host}/api/queues/{vhost_enc}/{queue_enc}/get"
    resp = requests.post(
        api,
        json={"count": count, "ackmode": "ack_requeue_true", "encoding": "auto"},
        auth=(user, password),
        timeout=120,
    )
    resp.raise_for_status()
    out: list[tuple[JsonDict, str | None]] = []
    for item in resp.json():
        payload = item.get("payload")
        if isinstance(payload, str):
            payload = json.loads(payload)
        if isinstance(payload, dict):
            out.append((payload, item.get("routing_key")))
    return out


def scan_enriched_for_correlations(
    url: str,
    queue: str,
    needed: set[str],
    *,
    limit: int | None = 500,
) -> dict[str, tuple[JsonDict, str | None]]:
    """Return correlation_id → (payload, routing_key) for messages still on the queue."""
    if not needed:
        return {}
    depth = queue_depth(url, queue)
    if depth is None or depth <= 0:
        return {}

    target = depth if limit is None else min(depth, limit)
    found: dict[str, tuple[JsonDict, str | None]] = {}
    remaining = set(needed)
    scanned = 0
    while scanned < target and remaining:
        batch = _fetch_batch(url, queue, min(BATCH_SIZE, target - scanned))
        if not batch:
            break
        scanned += len(batch)
        for payload, routing_key in batch:
            cid = _correlation_id(payload)
            if cid and cid in remaining:
                found[cid] = (payload, routing_key)
                remaining.discard(cid)
        if len(batch) < BATCH_SIZE:
            break
    return found


def scan_enriched_for_operations(
    url: str,
    queue: str,
    operations: set[str],
    *,
    limit: int | None = 500,
) -> dict[str, tuple[JsonDict, str | None]]:
    """Return operation → (payload, routing_key) for the newest peeked match per operation."""
    if not operations:
        return {}
    depth = queue_depth(url, queue)
    if depth is None or depth <= 0:
        return {}

    target = depth if limit is None else min(depth, limit)
    found: dict[str, tuple[JsonDict, str | None]] = {}
    remaining = set(operations)
    scanned = 0
    while scanned < target and remaining:
        batch = _fetch_batch(url, queue, min(BATCH_SIZE, target - scanned))
        if not batch:
            break
        scanned += len(batch)
        for payload, routing_key in batch:
            op = _operation(payload)
            if op in remaining and op not in found:
                cid = _correlation_id(payload)
                if cid:
                    found[op] = (payload, routing_key)
                    remaining.discard(op)
        if len(batch) < BATCH_SIZE:
            break
    return found


def supplement_collector_from_queue(
    collector: QueueEventCollector,
    *,
    missing_correlation_ids: set[str] | None = None,
    missing_operations: set[str] | None = None,
    scan_limit: int = 800,
    extra_queues: list[str] | None = None,
) -> int:
    """
    Inject enriched payloads from the tap queue that the live consumer may have missed.

    Uses management API peek (messages are requeued). Returns number of injections.
    Also scans ``extra_queues`` (e.g. platform notification queue) when the resolver
    tap missed messages that still landed on the notification fanout queue.
    """
    rmq = collector.rabbitmq_config
    needed_cids = set(missing_correlation_ids or ())
    needed_ops = set(missing_operations or ())
    if not needed_cids and not needed_ops:
        return 0

    queues_to_scan = [rmq.enriched_queue]
    for queue in extra_queues or ():
        if queue and queue not in queues_to_scan:
            queues_to_scan.append(queue)
    if (
        rmq.platform_notification_queue
        and rmq.platform_notification_queue not in queues_to_scan
    ):
        queues_to_scan.append(rmq.platform_notification_queue)

    by_cid: dict[str, tuple[JsonDict, str | None]] = {}
    by_op: dict[str, tuple[JsonDict, str | None]] = {}
    per_queue_limit = max(scan_limit // len(queues_to_scan), 100)
    for queue in queues_to_scan:
        for cid, hit in scan_enriched_for_correlations(
            rmq.url, queue, needed_cids - set(by_cid), limit=per_queue_limit
        ).items():
            by_cid.setdefault(cid, hit)
        for op, hit in scan_enriched_for_operations(
            rmq.url, queue, needed_ops - set(by_op), limit=per_queue_limit
        ).items():
            by_op.setdefault(op, hit)

    injected = 0
    for cid, (payload, routing_key) in by_cid.items():
        if collector.inject_enriched(payload, routing_key=routing_key):
            injected += 1
            log.info("Queue peek: injected enriched for correlation %s", cid[:8])

    for op, (payload, routing_key) in by_op.items():
        cid = _correlation_id(payload)
        if not cid:
            continue
        if collector.inject_enriched(payload, routing_key=routing_key):
            injected += 1
            log.info("Queue peek: injected enriched for operation %s (cid %s)", op, cid[:8])

    return injected
