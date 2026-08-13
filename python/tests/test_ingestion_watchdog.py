"""Ingestion watchdog resurrects dead consumer threads without double-spawning."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

from audit_validator.ingestion.config import IngestLaneConfig, IngestionConfig, QueueBinding
from audit_validator.ingestion.consumer import QueueConsumer
from audit_validator.ingestion.service import IngestionService, _IngestLane


def _fake_config() -> IngestionConfig:
    return IngestionConfig(
        rabbitmq_url="amqp://guest:guest@127.0.0.1:5672/%2F",
        mongo_url="mongodb://127.0.0.1:27017",
        mongo_db="AuditLogsQA",
        mongo_databases=("AuditLogsQA",),
        prefetch=10,
        reconnect_delay_sec=0.01,
        flush_interval_sec=1.0,
        max_insert_retries=1,
        insert_retry_delay_sec=0.01,
        cleanup_interval_sec=3600.0,
        max_docs_per_operation=10,
        bindings=[
            QueueBinding("raw", "q-raw", "raw"),
            QueueBinding("enriched", "q-enrich", "enriched"),
        ],
    )


def _svc_with_fake_consumers() -> IngestionService:
    cfg = _fake_config()
    writer = MagicMock()
    consumers = [
        QueueConsumer(b, cfg, writer, target="qa", vhost="mt-connect-qa")
        for b in cfg.bindings
    ]
    for consumer in consumers:
        def _run(self=consumer) -> None:
            while not self._stop.wait(0.05):
                pass

        consumer.run = _run  # type: ignore[method-assign]

    lane_cfg = IngestLaneConfig(
        target="qa",
        vhost="mt-connect-qa",
        rabbitmq_url=cfg.rabbitmq_url,
        mongo_db=cfg.mongo_db,
        config=cfg,
    )

    with patch.object(IngestionService, "_build_lanes", return_value=[]):
        svc = IngestionService(cfg)
    svc._lanes = [_IngestLane(lane=lane_cfg, writer=writer, consumers=consumers)]
    svc._consumers = consumers
    return svc


def test_restart_dead_enriched_keeps_raw():
    svc = _svc_with_fake_consumers()
    keep = threading.Event()

    def _hold() -> None:
        keep.wait(2.0)

    raw_alive = threading.Thread(target=_hold, daemon=True)
    raw_alive.start()
    dead = threading.Thread(target=lambda: None, daemon=True)
    dead.start()
    dead.join(timeout=1)

    svc._threads = [raw_alive, dead]
    assert svc.running is True
    assert svc.all_consumers_alive is False

    n = svc._restart_dead_consumers()
    assert n == 1
    assert svc._threads[0] is raw_alive
    assert svc._threads[1].is_alive()
    keep.set()
    svc._stop.set()
    for c in svc._consumers:
        c.stop()
