"""Single-queue RabbitMQ → Mongo consumer with batching, retry and reconnect.

One consumer runs in its own thread with its own BlockingConnection consuming ONE
queue into ONE Mongo collection. Batching and acks happen on the connection thread
(required by pika): we drain the ``channel.consume`` generator, accumulate a batch,
and flush + ack (multiple) when the batch fills or the queue goes idle.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field

import pika

from .config import IngestionConfig, QueueBinding
from .repository import MongoWriter

log = logging.getLogger(__name__)


@dataclass
class ConsumerStats:
    name: str
    queue: str
    collection: str
    connected: bool = False
    consumed: int = 0
    inserted: int = 0
    invalid: int = 0
    failed_flushes: int = 0
    last_insert_at: float | None = None
    last_error: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "name": self.name,
                "queue": self.queue,
                "collection": self.collection,
                "connected": self.connected,
                "consumed": self.consumed,
                "inserted": self.inserted,
                "invalid": self.invalid,
                "failed_flushes": self.failed_flushes,
                "last_insert_at": self.last_insert_at,
                "last_error": self.last_error,
            }


class QueueConsumer:
    def __init__(
        self,
        binding: QueueBinding,
        config: IngestionConfig,
        writer: MongoWriter,
    ) -> None:
        self._binding = binding
        self._config = config
        self._writer = writer
        self._stop = threading.Event()
        self.stats = ConsumerStats(
            name=binding.name, queue=binding.queue, collection=binding.collection
        )

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        params = pika.URLParameters(self._config.rabbitmq_url)
        params.heartbeat = 600
        params.blocked_connection_timeout = 300

        while not self._stop.is_set():
            connection = None
            try:
                connection = pika.BlockingConnection(params)
                channel = connection.channel()
                channel.basic_qos(prefetch_count=self._config.prefetch)
                with self.stats._lock:
                    self.stats.connected = True
                    self.stats.last_error = ""
                log.info(
                    "Ingestion consumer started: %s (queue=%s → %s)",
                    self._binding.name,
                    self._binding.queue,
                    self._binding.collection,
                )
                self._consume(channel)
            except Exception as exc:  # noqa: BLE001 — reconnect on any broker/mongo error
                msg = str(exc).strip() or exc.__class__.__name__
                low = (msg + " " + exc.__class__.__name__).lower()
                if "timed out" in low or "timeout" in low or "connection" in low or "amqpconnection" in low:
                    msg = "Cannot reach RabbitMQ — connection timed out (are you on the corporate VPN?)"
                with self.stats._lock:
                    self.stats.connected = False
                    self.stats.last_error = msg
                log.warning("Ingestion consumer %s error: %s", self._binding.name, msg)
            finally:
                with self.stats._lock:
                    self.stats.connected = False
                try:
                    if connection and connection.is_open:
                        connection.close()
                except Exception:
                    pass

            if self._stop.is_set():
                break
            time.sleep(self._config.reconnect_delay_sec)

    def _consume(self, channel) -> None:
        batch: list[dict] = []
        last_tag: int | None = None
        last_flush = time.monotonic()
        flush_interval = self._config.flush_interval_sec

        for method, _properties, body in channel.consume(
            self._binding.queue, inactivity_timeout=1.0, auto_ack=False
        ):
            if self._stop.is_set():
                break

            if method is not None:
                with self.stats._lock:
                    self.stats.consumed += 1
                try:
                    document = json.loads(body.decode("utf-8"))
                    if isinstance(document, dict):
                        batch.append(document)
                    else:
                        with self.stats._lock:
                            self.stats.invalid += 1
                except (ValueError, UnicodeDecodeError):
                    with self.stats._lock:
                        self.stats.invalid += 1
                last_tag = method.delivery_tag

            now = time.monotonic()
            full = len(batch) >= self._config.prefetch
            idle_due = (now - last_flush) >= flush_interval and last_tag is not None
            if (full or idle_due) and last_tag is not None:
                if self._flush(channel, batch, last_tag):
                    batch = []
                    last_tag = None
                last_flush = now

        # Drain remaining on stop.
        if batch and last_tag is not None:
            self._flush(channel, batch, last_tag)
        try:
            channel.cancel()
        except Exception:
            pass

    def _flush(self, channel, batch: list[dict], last_tag: int) -> bool:
        """Insert batch (with retry) then ack up to last_tag. Returns True on success."""
        if not batch:
            # Nothing valid parsed but messages were delivered — ack them.
            self._ack(channel, last_tag)
            return True

        attempt = 0
        while attempt < self._config.max_insert_retries and not self._stop.is_set():
            attempt += 1
            try:
                inserted = self._writer.insert_many(self._binding.collection, batch)
                with self.stats._lock:
                    self.stats.inserted += inserted
                    self.stats.last_insert_at = time.time()
                self._ack(channel, last_tag)
                return True
            except Exception as exc:  # noqa: BLE001
                with self.stats._lock:
                    self.stats.last_error = str(exc)
                log.warning(
                    "Ingestion insert retry %s/%s (%s): %s",
                    attempt,
                    self._config.max_insert_retries,
                    self._binding.name,
                    exc,
                )
                time.sleep(self._config.insert_retry_delay_sec * attempt)

        with self.stats._lock:
            self.stats.failed_flushes += 1
        # Nack so the broker redelivers; keeps data safe.
        try:
            channel.basic_nack(delivery_tag=last_tag, multiple=True, requeue=True)
        except Exception:
            pass
        return False

    @staticmethod
    def _ack(channel, last_tag: int) -> None:
        try:
            channel.basic_ack(delivery_tag=last_tag, multiple=True)
        except Exception:
            pass
