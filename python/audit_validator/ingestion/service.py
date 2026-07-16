"""Ingestion service — runs all queue consumers + a cleanup scheduler.

Equivalent of audit-sense's bootstrap: connect Mongo, ensure indexes, start one
consumer per queue in its own thread, and run a periodic cleanup that keeps only the
latest N documents per operation.
"""

from __future__ import annotations

import logging
import threading
import time

import pika

from .config import IngestionConfig, load_ingestion_config
from .consumer import QueueConsumer
from .repository import MongoWriter

log = logging.getLogger(__name__)


class IngestionService:
    def __init__(self, config: IngestionConfig | None = None) -> None:
        self._config = config or load_ingestion_config()
        self._writer = MongoWriter(self._config.mongo_url, self._config.mongo_db)
        self._consumers: list[QueueConsumer] = [
            QueueConsumer(binding, self._config, self._writer)
            for binding in self._config.bindings
        ]
        self._threads: list[threading.Thread] = []
        self._cleanup_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._started_at: float | None = None
        self._cleanup_deleted = 0
        self._last_cleanup_at: float | None = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return bool(self._threads) and any(t.is_alive() for t in self._threads)

    def purge(self) -> dict:
        """Purge each subscription queue's backlog. Consumed+acked messages are already
        removed from the queue; this drops anything still queued so only fresh events
        get ingested. Returns per-queue purged message counts."""
        purged: dict[str, int] = {}
        params = pika.URLParameters(self._config.rabbitmq_url)
        connection = None
        try:
            connection = pika.BlockingConnection(params)
            channel = connection.channel()
            for binding in self._config.bindings:
                try:
                    result = channel.queue_purge(binding.queue)
                    count = getattr(getattr(result, "method", None), "message_count", 0) or 0
                    purged[binding.name] = int(count)
                    log.info("Purged %s message(s) from %s", count, binding.queue)
                except Exception as exc:  # noqa: BLE001
                    log.warning("Purge %s failed: %s", binding.queue, exc)
                    purged[binding.name] = 0
        finally:
            try:
                if connection and connection.is_open:
                    connection.close()
            except Exception:
                pass
        return purged

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        collections = [b.collection for b in self._config.bindings]
        try:
            self._writer.ensure_indexes(collections)
        except Exception as exc:  # noqa: BLE001
            log.warning("ensure_indexes failed (continuing): %s", exc)

        if self._config.purge_on_start:
            try:
                self.purge()
            except Exception as exc:  # noqa: BLE001
                log.warning("purge_on_start failed (continuing): %s", exc)

        self._threads = []
        for consumer in self._consumers:
            consumer._stop.clear()
            thread = threading.Thread(
                target=consumer.run, name=f"ingest-{consumer.stats.name}", daemon=True
            )
            thread.start()
            self._threads.append(thread)

        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop, name="ingest-cleanup", daemon=True
        )
        self._cleanup_thread.start()
        self._started_at = time.time()
        log.info("Ingestion service started (%d consumers)", len(self._consumers))

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        for consumer in self._consumers:
            consumer.stop()
        deadline = time.monotonic() + timeout
        for thread in self._threads:
            remaining = max(0.1, deadline - time.monotonic())
            thread.join(timeout=remaining)
        self._threads = []
        log.info("Ingestion service stopped")

    def _cleanup_loop(self) -> None:
        # First cleanup after one interval so freshly-inserted data settles.
        while not self._stop.wait(self._config.cleanup_interval_sec):
            self._run_cleanup_once()

    def _run_cleanup_once(self) -> None:
        total = 0
        for binding in self._config.bindings:
            try:
                total += self._writer.cleanup_collection(
                    binding.collection, self._config.max_docs_per_operation
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("cleanup %s failed: %s", binding.collection, exc)
        with self._lock:
            self._cleanup_deleted += total
            self._last_cleanup_at = time.time()
        if total:
            log.info("Ingestion cleanup removed %d stale document(s)", total)

    def status(self) -> dict:
        with self._lock:
            cleanup_deleted = self._cleanup_deleted
            last_cleanup_at = self._last_cleanup_at
            started_at = self._started_at
        consumers = [c.stats.snapshot() for c in self._consumers]
        return {
            "running": self.running,
            "started_at": started_at,
            "mongo_connected": self._writer.ping(),
            "rabbitmq_connected": any(c["connected"] for c in consumers),
            "max_docs_per_operation": self._config.max_docs_per_operation,
            "cleanup_interval_sec": self._config.cleanup_interval_sec,
            "cleanup_deleted": cleanup_deleted,
            "last_cleanup_at": last_cleanup_at,
            "totals": {
                "consumed": sum(c["consumed"] for c in consumers),
                "inserted": sum(c["inserted"] for c in consumers),
                "invalid": sum(c["invalid"] for c in consumers),
            },
            "consumers": consumers,
        }
