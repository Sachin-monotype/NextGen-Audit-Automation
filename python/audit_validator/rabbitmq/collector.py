"""RabbitMQ consumer — captures raw and enriched envelopes by operation and routing key."""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable

import pika
from pika import BasicProperties
from pika.adapters.blocking_connection import BlockingChannel

from ..config import AppConfig, RabbitMQConfig
from ..models import JsonDict
from .enriched_routing_keys import ENRICHED_ROUTING_KEYS_LIST

log = logging.getLogger(__name__)


def _correlation_id(payload: JsonDict, properties: BasicProperties | None = None) -> str | None:
    cid = payload.get("xCorrelationId")
    if isinstance(cid, str) and cid:
        return cid
    headers = getattr(properties, "headers", None) if properties else None
    if isinstance(headers, dict):
        for key in ("x-correlation-id", "xCorrelationId", "x_correlation_id"):
            val = headers.get(key)
            if isinstance(val, str) and val:
                return val
            if isinstance(val, bytes):
                return val.decode("utf-8", errors="replace")
    return None


def _operation_from_payload(payload: JsonDict) -> tuple[str, str]:
    source = payload.get("source") or {}
    operation = source.get("operation") or "unknown"
    service = source.get("service") or "unknown"
    return str(operation), str(service)


def _operation_key(payload: JsonDict) -> str:
    operation, service = _operation_from_payload(payload)
    return f"{operation}-{service}"


def _parse_key(key: str) -> tuple[str, str]:
    if key.endswith("-mtconnect-api"):
        return key[: -len("-mtconnect-api")], "mtconnect-api"
    operation, service = key.rsplit("-", 1)
    return operation, service


def _payload_routing_key(payload: JsonDict, amqp_routing_key: str) -> str:
    """Prefer AMQP routing key; fall back to payload fields if present."""
    if amqp_routing_key:
        return amqp_routing_key
    for field in ("routingKey", "type", "eventType", "notificationType"):
        val = payload.get(field)
        if isinstance(val, str) and val:
            return val
    source = payload.get("source")
    if isinstance(source, dict):
        types = source.get("type")
        if isinstance(types, list) and types:
            return str(types[0])
    return "unknown"


class QueueEventCollector:
    """
    Consumes raw, enriched, and dead-letter queues.

    Primary pairing key: `xCorrelationId` (raw ↔ enriched).
    Secondary indexes: operation key and AMQP routing key.
    """

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._rmq = config.rabbitmq
        self._lock = threading.Lock()
        self._raw: dict[str, JsonDict] = {}
        self._raw_by_correlation: dict[str, JsonDict] = {}
        self._enriched: dict[str, JsonDict] = {}
        self._enriched_by_correlation: dict[str, JsonDict] = {}
        self._enriched_by_routing_key: dict[str, JsonDict] = {}
        self._enriched_routing_key_for_op: dict[str, str] = {}
        self._enriched_routing_key_for_correlation: dict[str, str] = {}
        self._dead_letter: dict[str, JsonDict] = {}
        self._dead_letter_by_correlation: dict[str, JsonDict] = {}
        self._connection: pika.BlockingConnection | None = None
        self._channel: BlockingChannel | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._write_files = True

    def missing_enriched_correlation_ids(self) -> set[str]:
        with self._lock:
            return {
                cid
                for cid in self._raw_by_correlation
                if cid not in self._enriched_by_correlation
            }

    @property
    def rabbitmq_config(self) -> RabbitMQConfig:
        return self._rmq

    def inject_enriched(
        self,
        payload: JsonDict,
        *,
        routing_key: str | None = None,
    ) -> bool:
        """Add an enriched payload discovered after live consume (e.g. queue peek)."""
        cid = _correlation_id(payload, None)
        if not cid:
            return False
        amqp_rk = routing_key or ""
        rk = _payload_routing_key(payload, amqp_rk)
        op_key = _operation_key(payload)
        with self._lock:
            if cid in self._enriched_by_correlation:
                return False
            self._enriched_by_correlation[cid] = payload
            self._enriched_by_routing_key[rk] = payload
            self._enriched_routing_key_for_correlation[cid] = rk
            if op_key != "unknown-unknown":
                self._enriched[op_key] = payload
                self._enriched_routing_key_for_op[op_key] = rk
        return True

    def clear_capture(self) -> None:
        """Drop in-memory captures; consumer thread keeps running."""
        with self._lock:
            self._raw.clear()
            self._raw_by_correlation.clear()
            self._enriched.clear()
            self._enriched_by_correlation.clear()
            self._enriched_by_routing_key.clear()
            self._enriched_routing_key_for_op.clear()
            self._enriched_routing_key_for_correlation.clear()
            self._dead_letter.clear()
            self._dead_letter_by_correlation.clear()
        log.info("Cleared in-memory queue captures (fresh-run window for validation)")

    @property
    def raw_correlation_count(self) -> int:
        with self._lock:
            return len(self._raw_by_correlation)

    @property
    def enriched_correlation_count(self) -> int:
        with self._lock:
            return len(self._enriched_by_correlation)

    @property
    def raw_count(self) -> int:
        with self._lock:
            return len(self._raw)

    @property
    def enriched_count(self) -> int:
        with self._lock:
            return len(self._enriched_by_routing_key)

    @property
    def enriched_op_count(self) -> int:
        with self._lock:
            return len(self._enriched)

    @property
    def dead_letter_count(self) -> int:
        with self._lock:
            return len(self._dead_letter)

    def received_routing_keys(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._enriched_by_routing_key.keys())

    def missing_routing_keys(self) -> list[str]:
        if self._rmq.wildcard_bind_mode:
            return []
        expected = self._rmq.enriched_routing_keys
        received = self.received_routing_keys()
        return sorted(expected - received)

    def start(self, *, write_files: bool = True) -> None:
        if self._running:
            return
        self._write_files = write_files
        self._running = True
        self._thread = threading.Thread(target=self._consume_loop, daemon=True)
        self._thread.start()
        for _ in range(50):
            if self._channel is not None:
                log.info("Queue consumer connected and listening")
                return
            time.sleep(0.1)
        raise RuntimeError("Failed to start RabbitMQ consumer within 5s")

    def stop(self) -> None:
        self._running = False
        if self._connection and self._connection.is_open:
            try:
                self._connection.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=5)
        self._connection = None
        self._channel = None

    def wait_for_missing_enriched(self, timeout_sec: float, *, poll_sec: float = 0.5) -> int:
        """Keep consuming until every captured raw correlation has enriched, or timeout."""
        deadline = time.monotonic() + timeout_sec
        last_missing: int | None = None
        while time.monotonic() < deadline:
            with self._lock:
                missing = sum(
                    1
                    for cid in self._raw_by_correlation
                    if cid not in self._enriched_by_correlation
                )
            if missing == 0:
                return 0
            if missing != last_missing:
                last_missing = missing
                log.info("Enrichment catch-up: %d raw correlation(s) still without enriched", missing)
            time.sleep(poll_sec)
        return last_missing or 0

    def wait_for_operations(
        self,
        operations: frozenset[str] | set[str],
        timeout_sec: float,
        *,
        poll_sec: float = 1.0,
    ) -> set[str]:
        """Return once every selected operation has a raw+enriched correlation pair.

        Used for targeted generate+validate so we stop as soon as the *selected*
        operations settle, instead of waiting for the whole (shared) queue to go
        idle. Returns the set of operations still missing an enriched pair.
        """
        wanted = {op for op in operations if op}
        if not wanted:
            return set()
        deadline = time.monotonic() + timeout_sec
        missing = set(wanted)
        while time.monotonic() < deadline:
            with self._lock:
                paired_ops: set[str] = set()
                for cid, raw in self._raw_by_correlation.items():
                    if cid in self._enriched_by_correlation:
                        op = str((raw.get("source") or {}).get("operation") or "")
                        if op:
                            paired_ops.add(op)
            missing = wanted - paired_ops
            if not missing:
                log.info("All %d selected operation(s) have raw+enriched pairs", len(wanted))
                return set()
            time.sleep(poll_sec)
        return missing

    def wait_until_settled(self, timeout_sec: float, *, min_elapsed_sec: float = 0) -> None:
        idle_needed = 2.0
        deadline = time.monotonic() + timeout_sec
        started = time.monotonic()
        last_counts = (-1, -1)
        idle_since: float | None = None

        while time.monotonic() < deadline:
            with self._lock:
                counts = (
                    len(self._raw_by_correlation),
                    len(self._enriched_by_correlation),
                )
            elapsed = time.monotonic() - started
            if counts != last_counts:
                last_counts = counts
                idle_since = None
            elif elapsed >= min_elapsed_sec:
                if idle_since is None:
                    idle_since = time.monotonic()
                idle_needed = 5.0 if counts[0] > counts[1] else 2.0
                if time.monotonic() - idle_since >= idle_needed:
                    log.info(
                        "Queues settled (raw_correlations=%d enriched_correlations=%d)",
                        counts[0],
                        counts[1],
                    )
                    return
            time.sleep(0.5)

        log.warning("Settle timeout reached (raw=%d enriched=%d)", *last_counts)

    def wait_until_queues_empty(
        self,
        timeout_sec: float,
        *,
        poll_interval: float = 2.0,
    ) -> dict[str, int]:
        """Drain until raw + enriched queue depths hit 0 or timeout."""
        from .queue_stats import get_queue_depths

        deadline = time.monotonic() + timeout_sec
        last_depths: dict[str, int] = {}
        idle_since: float | None = None
        idle_needed = 3.0

        while time.monotonic() < deadline:
            try:
                depths = get_queue_depths(self._rmq, extra_queues=())
            except Exception as exc:
                log.warning("Queue depth poll failed: %s", exc)
                time.sleep(poll_interval)
                continue

            raw_depth = depths.get(self._rmq.raw_queue, -1)
            enr_depth = depths.get(self._rmq.enriched_queue, -1)
            last_depths = {
                self._rmq.raw_queue: raw_depth,
                self._rmq.enriched_queue: enr_depth,
            }

            if raw_depth == 0 and enr_depth == 0:
                if idle_since is None:
                    idle_since = time.monotonic()
                elif time.monotonic() - idle_since >= idle_needed:
                    log.info(
                        "Queues drained (raw_correlations=%d enriched_correlations=%d)",
                        self.raw_correlation_count,
                        self.enriched_correlation_count,
                    )
                    return last_depths
            else:
                idle_since = None

            time.sleep(poll_interval)

        log.warning(
            "Queue drain timeout — raw_depth=%s enriched_depth=%s correlations raw=%d enriched=%d",
            last_depths.get(self._rmq.raw_queue),
            last_depths.get(self._rmq.enriched_queue),
            self.raw_correlation_count,
            self.enriched_correlation_count,
        )
        return last_depths

    def get_pair(self, key: str) -> tuple[JsonDict | None, JsonDict | None, JsonDict | None]:
        with self._lock:
            return (
                self._raw.get(key),
                self._enriched.get(key),
                self._dead_letter.get(key),
            )

    def snapshot_by_correlation(self) -> dict[str, tuple[JsonDict | None, JsonDict | None]]:
        """Return raw/enriched pairs keyed by xCorrelationId."""
        with self._lock:
            keys = set(self._raw_by_correlation) | set(self._enriched_by_correlation)
            return {
                cid: (
                    self._raw_by_correlation.get(cid),
                    self._enriched_by_correlation.get(cid),
                )
                for cid in keys
            }

    def get_by_correlation(
        self, correlation_id: str
    ) -> tuple[JsonDict | None, JsonDict | None, JsonDict | None]:
        with self._lock:
            return (
                self._raw_by_correlation.get(correlation_id),
                self._enriched_by_correlation.get(correlation_id),
                self._dead_letter_by_correlation.get(correlation_id),
            )

    def routing_key_for_correlation(self, correlation_id: str) -> str | None:
        with self._lock:
            return self._enriched_routing_key_for_correlation.get(correlation_id)

    def snapshot(self) -> dict[str, tuple[JsonDict | None, JsonDict | None]]:
        with self._lock:
            keys = set(self._raw) | set(self._enriched)
            return {k: (self._raw.get(k), self._enriched.get(k)) for k in keys}

    def snapshot_by_routing_key(self) -> dict[str, JsonDict]:
        with self._lock:
            return dict(self._enriched_by_routing_key)

    def _consume_loop(self) -> None:
        params = pika.URLParameters(self._rmq.url)
        params.heartbeat = 600
        params.blocked_connection_timeout = 300

        while self._running:
            try:
                self._connection = pika.BlockingConnection(params)
                self._channel = self._connection.channel()
                self._channel.basic_qos(prefetch_count=50)
                self._assert_topology(self._channel, self._rmq)

                self._channel.basic_consume(
                    queue=self._rmq.raw_queue,
                    on_message_callback=self._make_handler(self._raw, "raw"),
                    auto_ack=False,
                )
                self._channel.basic_consume(
                    queue=self._rmq.enriched_queue,
                    on_message_callback=self._make_enriched_handler(),
                    auto_ack=False,
                )
                if self._rmq.consume_dead_letter_queue:
                    self._channel.basic_consume(
                        queue=self._rmq.dead_letter_queue,
                        on_message_callback=self._make_handler(self._dead_letter, "dl"),
                        auto_ack=False,
                    )

                if self._rmq.wildcard_bind_mode:
                    log.info(
                        "Consuming raw=%s enriched=%s (wildcard # bind — correlation validation)%s",
                        self._rmq.raw_queue,
                        self._rmq.enriched_queue,
                        f" dl={self._rmq.dead_letter_queue}"
                        if self._rmq.consume_dead_letter_queue
                        else "",
                    )
                else:
                    log.info(
                        "Consuming raw=%s enriched=%s (%d expected routing keys)%s",
                        self._rmq.raw_queue,
                        self._rmq.enriched_queue,
                        len(self._rmq.enriched_routing_keys),
                        f" dl={self._rmq.dead_letter_queue}"
                        if self._rmq.consume_dead_letter_queue
                        else "",
                    )

                while self._running and self._channel.is_open:
                    self._connection.process_data_events(time_limit=1)

            except Exception as exc:
                if self._running:
                    log.error("Consumer error, retrying in 2s: %s", exc)
                    time.sleep(2)
            finally:
                if self._connection and self._connection.is_open:
                    try:
                        self._connection.close()
                    except Exception:
                        pass

    def _make_handler(
        self,
        store: dict[str, JsonDict],
        label: str,
    ) -> Callable:
        def handler(ch: BlockingChannel, method, properties, body: bytes) -> None:
            try:
                payload = json.loads(body.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("Payload is not a JSON object")
                key = _operation_key(payload)
                operation, service = _parse_key(key)
                cid = _correlation_id(payload, properties)

                with self._lock:
                    store[key] = payload
                    if cid:
                        store_by_cid = (
                            self._raw_by_correlation
                            if label == "raw"
                            else self._dead_letter_by_correlation
                        )
                        store_by_cid[cid] = payload

                if self._write_files:
                    self._write_payload(label, operation, service, payload, cid)

                log.debug("[%s] %s", label, key)
            except Exception as exc:
                log.warning("[%s] Failed to process message: %s", label, exc)
            finally:
                ch.basic_ack(method.delivery_tag)

        return handler

    def _make_enriched_handler(self) -> Callable:
        def handler(ch: BlockingChannel, method, properties, body: bytes) -> None:
            try:
                payload = json.loads(body.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("Payload is not a JSON object")

                amqp_rk = method.routing_key or ""
                routing_key = _payload_routing_key(payload, amqp_rk)
                op_key = _operation_key(payload)
                cid = _correlation_id(payload, properties)

                with self._lock:
                    self._enriched_by_routing_key[routing_key] = payload
                    if op_key != "unknown-unknown":
                        self._enriched[op_key] = payload
                        self._enriched_routing_key_for_op[op_key] = routing_key
                    if cid:
                        self._enriched_by_correlation[cid] = payload
                        self._enriched_routing_key_for_correlation[cid] = routing_key

                if self._write_files:
                    self._write_enriched_payload(routing_key, op_key, payload)

                if routing_key not in self._rmq.enriched_routing_keys:
                    log.debug(
                        "[enriched] Routing key `%s` outside resolver registry",
                        routing_key,
                    )
                else:
                    log.debug("[enriched] routing_key=%s op_key=%s", routing_key, op_key)
            except Exception as exc:
                log.warning("[enriched] Failed to process message: %s", exc)
            finally:
                ch.basic_ack(method.delivery_tag)

        return handler

    def _write_payload(
        self,
        label: str,
        operation: str,
        service: str,
        payload: JsonDict,
        correlation_id: str | None = None,
    ) -> None:
        if label == "raw":
            out_dir = self._config.raw_events_dir
        elif label == "dl":
            out_dir = self._config.dead_letter_events_dir
        else:
            return

        out_dir.mkdir(parents=True, exist_ok=True)
        if operation and operation != "unknown":
            path = out_dir / f"{operation}.json"
        else:
            cid = (correlation_id or "event")[:8]
            path = out_dir / f"unknown-{cid}.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _write_enriched_payload(
        self,
        routing_key: str,
        op_key: str,
        payload: JsonDict,
    ) -> None:
        out_dir = self._config.enriched_events_dir
        out_dir.mkdir(parents=True, exist_ok=True)

        if op_key != "unknown-unknown":
            operation, _service = _parse_key(op_key)
            if operation and operation != "unknown":
                op_path = out_dir / f"{operation}.json"
                op_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @staticmethod
    def _assert_topology(channel: BlockingChannel, rmq: RabbitMQConfig) -> None:
        if rmq.raw_queue_passive and rmq.enriched_queue_passive:
            channel.queue_declare(queue=rmq.raw_queue, passive=True)
            channel.queue_declare(queue=rmq.enriched_queue, passive=True)
            log.info(
                "Passive consume on `%s` + `%s` — platform bindings unchanged",
                rmq.raw_queue,
                rmq.enriched_queue,
            )
            return

        channel.exchange_declare(exchange=rmq.raw_exchange, exchange_type="topic", durable=True)
        channel.exchange_declare(
            exchange=rmq.enriched_exchange, exchange_type="topic", durable=True
        )
        channel.exchange_declare(
            exchange=rmq.dead_letter_exchange, exchange_type="topic", durable=True
        )

        if rmq.raw_queue_passive:
            try:
                channel.queue_declare(queue=rmq.raw_queue, passive=True)
                log.info("Raw queue `%s` (passive consume)", rmq.raw_queue)
            except Exception as exc:
                log.warning(
                    "Passive declare failed for raw `%s`: %s — creating queue",
                    rmq.raw_queue,
                    exc,
                )
                channel.queue_declare(queue=rmq.raw_queue, durable=True)
                channel.queue_bind(
                    queue=rmq.raw_queue, exchange=rmq.raw_exchange, routing_key="#"
                )
        else:
            channel.queue_declare(queue=rmq.raw_queue, durable=True)
            channel.queue_bind(queue=rmq.raw_queue, exchange=rmq.raw_exchange, routing_key="#")

        if rmq.consume_dead_letter_queue:
            channel.queue_declare(queue=rmq.dead_letter_queue, durable=True)
            channel.queue_bind(
                queue=rmq.dead_letter_queue,
                exchange=rmq.dead_letter_exchange,
                routing_key="dl",
            )

        QueueEventCollector._setup_enriched_queue(channel, rmq)

    @staticmethod
    def _setup_enriched_queue(channel: BlockingChannel, rmq: RabbitMQConfig) -> None:
        if rmq.enriched_queue_passive:
            try:
                channel.queue_declare(queue=rmq.enriched_queue, passive=True)
                bind_note = (
                    "wildcard # binding"
                    if rmq.wildcard_bind_mode
                    else "platform bindings"
                )
                log.info(
                    "Enriched queue `%s` exists (passive) — using %s",
                    rmq.enriched_queue,
                    bind_note,
                )
                return
            except Exception as exc:
                log.warning(
                    "Passive declare failed for `%s`: %s — creating queue locally",
                    rmq.enriched_queue,
                    exc,
                )

        channel.queue_declare(queue=rmq.enriched_queue, durable=True)

        if rmq.enriched_use_wildcard_bind:
            channel.queue_bind(
                queue=rmq.enriched_queue,
                exchange=rmq.enriched_exchange,
                routing_key="#",
            )
            log.info("Bound enriched queue `%s` with wildcard #", rmq.enriched_queue)
            return

        bound = 0
        for routing_key in ENRICHED_ROUTING_KEYS_LIST:
            channel.queue_bind(
                queue=rmq.enriched_queue,
                exchange=rmq.enriched_exchange,
                routing_key=routing_key,
            )
            bound += 1
        log.info(
            "Bound enriched queue `%s` with %d routing keys on `%s`",
            rmq.enriched_queue,
            bound,
            rmq.enriched_exchange,
        )
