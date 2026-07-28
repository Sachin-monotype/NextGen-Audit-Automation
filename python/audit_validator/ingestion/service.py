"""Ingestion service — runs all queue consumers + a cleanup scheduler.

Equivalent of audit-sense's bootstrap: connect Mongo, ensure indexes, start one
consumer per queue in its own thread, and run a periodic cleanup that keeps only the
latest N documents per operation.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import pika

from .config import IngestLaneConfig, IngestionConfig, load_ingest_lanes, load_ingestion_config
from .consumer import QueueConsumer
from .repository import MongoWriter
from .targets import ingest_target_names

log = logging.getLogger(__name__)


@dataclass
class _IngestLane:
    lane: IngestLaneConfig
    writer: MongoWriter
    consumers: list[QueueConsumer]


class IngestionService:
    def __init__(self, config: IngestionConfig | None = None) -> None:
        self._base = config or load_ingestion_config()
        self._lanes = self._build_lanes(self._base)
        self._consumers = [consumer for lane in self._lanes for consumer in lane.consumers]
        self._threads: list[threading.Thread] = []
        self._cleanup_thread: threading.Thread | None = None
        self._auto_purge_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._started_at: float | None = None
        self._cleanup_deleted = 0
        self._last_cleanup_at: float | None = None
        self._auto_purge_total = 0
        self._last_auto_purge_at: float | None = None
        self._lock = threading.Lock()

    @staticmethod
    def _build_lanes(base: IngestionConfig) -> list[_IngestLane]:
        lanes: list[_IngestLane] = []
        for lane_cfg in load_ingest_lanes(base):
            writer = MongoWriter(base.mongo_url, lane_cfg.mongo_db)
            consumers = [
                QueueConsumer(
                    binding,
                    lane_cfg.config,
                    writer,
                    target=lane_cfg.target,
                    vhost=lane_cfg.vhost,
                )
                for binding in lane_cfg.config.bindings
            ]
            lanes.append(_IngestLane(lane=lane_cfg, writer=writer, consumers=consumers))
        return lanes

    @property
    def running(self) -> bool:
        return bool(self._threads) and any(t.is_alive() for t in self._threads)

    def _purge_lane(self, lane: _IngestLane, *, include_dlq: bool = False, min_ready: int = 0) -> dict[str, int]:
        purged: dict[str, int] = {}
        params = pika.URLParameters(lane.lane.rabbitmq_url)
        connection = None
        try:
            connection = pika.BlockingConnection(params)
            channel = connection.channel()
            for binding in lane.lane.config.bindings:
                if binding.name == "dlq" and not include_dlq:
                    continue
                key = f"{lane.lane.target}:{binding.name}"
                try:
                    declare = channel.queue_declare(queue=binding.queue, passive=True)
                    ready = int(getattr(declare.method, "message_count", 0) or 0)
                    if min_ready and ready < min_ready:
                        purged[key] = 0
                        continue
                    result = channel.queue_purge(binding.queue)
                    count = getattr(getattr(result, "method", None), "message_count", 0) or 0
                    purged[key] = int(count)
                    log.info(
                        "Purged %s message(s) from %s (%s / %s)",
                        count,
                        binding.queue,
                        lane.lane.target,
                        lane.lane.vhost,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "Purge %s failed (%s / %s): %s",
                        binding.queue,
                        lane.lane.target,
                        lane.lane.vhost,
                        exc,
                    )
                    purged[key] = 0
        finally:
            try:
                if connection and connection.is_open:
                    connection.close()
            except Exception:
                pass
        return purged

    def purge(self, *, include_dlq: bool = False) -> dict:
        """Purge ready backlog on each lane's tap queues (per vhost)."""
        merged: dict[str, int] = {}
        for lane in self._lanes:
            merged.update(self._purge_lane(lane, include_dlq=include_dlq))
        return merged

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        for lane in self._lanes:
            collections = [b.collection for b in lane.lane.config.bindings]
            try:
                lane.writer.ensure_indexes(collections)
            except Exception as exc:  # noqa: BLE001
                log.warning("ensure_indexes failed for %s (continuing): %s", lane.lane.target, exc)

        if self._base.purge_on_start:
            try:
                self.purge()
            except Exception as exc:  # noqa: BLE001
                log.warning("purge_on_start failed (continuing): %s", exc)

        self._threads = []
        for consumer in self._consumers:
            consumer._stop.clear()
            thread = threading.Thread(
                target=consumer.run,
                name=f"ingest-{consumer.stats.name}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop, name="ingest-cleanup", daemon=True
        )
        self._cleanup_thread.start()

        if self._base.auto_purge_enabled and self._base.auto_purge_interval_sec > 0:
            self._auto_purge_thread = threading.Thread(
                target=self._auto_purge_loop, name="ingest-auto-purge", daemon=True
            )
            self._auto_purge_thread.start()

        self._started_at = time.time()
        lane_summary = ", ".join(
            f"{lane.lane.target}@{lane.lane.vhost}→{lane.lane.mongo_db}" for lane in self._lanes
        )
        log.info(
            "Ingestion service started (%d consumers; %s)",
            len(self._consumers),
            lane_summary,
        )

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
        while not self._stop.wait(self._base.cleanup_interval_sec):
            self._run_cleanup_once()

    def _auto_purge_loop(self) -> None:
        interval = self._base.auto_purge_interval_sec
        while not self._stop.wait(interval):
            self._run_auto_purge_once()

    def _run_auto_purge_once(self) -> None:
        try:
            purged: dict[str, int] = {}
            min_ready = self._base.auto_purge_min_ready
            for lane in self._lanes:
                purged.update(
                    self._purge_lane(lane, include_dlq=False, min_ready=min_ready)
                )
            total = sum(purged.values())
            with self._lock:
                self._auto_purge_total += total
                self._last_auto_purge_at = time.time()
            if total:
                log.info(
                    "Auto-purge removed %s ready message(s) from tap queues (min_ready=%s)",
                    total,
                    min_ready,
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("Auto-purge failed: %s", exc)

    def _run_cleanup_once(self) -> None:
        total = 0
        for lane in self._lanes:
            for binding in lane.lane.config.bindings:
                try:
                    total += lane.writer.cleanup_collection(
                        binding.collection, self._base.max_docs_per_operation
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "cleanup %s/%s failed: %s",
                        lane.lane.target,
                        binding.collection,
                        exc,
                    )
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
            auto_purge_total = self._auto_purge_total
            last_auto_purge_at = self._last_auto_purge_at
        consumers = [c.stats.snapshot() for c in self._consumers]
        mongo_dbs = [lane.lane.mongo_db for lane in self._lanes]
        return {
            "running": self.running,
            "started_at": started_at,
            "mongo_connected": all(lane.writer.ping() for lane in self._lanes),
            "mongo_databases": mongo_dbs,
            "ingest_targets": ingest_target_names(),
            "ingest_lanes": [
                {
                    "target": lane.lane.target,
                    "vhost": lane.lane.vhost,
                    "mongo_db": lane.lane.mongo_db,
                }
                for lane in self._lanes
            ],
            "multi_target": len(self._lanes) > 1,
            "rabbitmq_connected": any(c["connected"] for c in consumers),
            "max_docs_per_operation": self._base.max_docs_per_operation,
            "cleanup_interval_sec": self._base.cleanup_interval_sec,
            "cleanup_deleted": cleanup_deleted,
            "last_cleanup_at": last_cleanup_at,
            "auto_purge_enabled": self._base.auto_purge_enabled,
            "auto_purge_interval_sec": self._base.auto_purge_interval_sec,
            "auto_purge_min_ready": self._base.auto_purge_min_ready,
            "auto_purge_total": auto_purge_total,
            "last_auto_purge_at": last_auto_purge_at,
            "totals": {
                "consumed": sum(c["consumed"] for c in consumers),
                "inserted": sum(c["inserted"] for c in consumers),
                "invalid": sum(c["invalid"] for c in consumers),
            },
            "consumers": consumers,
        }
