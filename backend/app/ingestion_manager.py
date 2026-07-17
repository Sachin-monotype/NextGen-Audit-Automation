"""Backend-side lifecycle manager for the RabbitMQ → Mongo ingestion service.

Wraps ``audit_validator.ingestion.IngestionService`` with the backend's Mongo
settings and a process-wide singleton so the UI can start/stop/inspect it.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from .config import Settings

log = logging.getLogger(__name__)


class IngestionManager:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._service: Any | None = None
        self._lock = threading.Lock()

    def _build_service(self) -> Any:
        from audit_validator.ingestion import IngestionService, load_ingestion_config

        config = load_ingestion_config(
            rabbitmq_url=os.getenv("INGEST_RABBITMQ_URL") or os.getenv("RABBITMQ_URL"),
            mongo_url=self._settings.mongo_url,
            mongo_db=self._settings.mongo_db,
            mongo_raw=self._settings.mongo_raw,
            mongo_enriched=self._settings.mongo_enriched,
            mongo_dlq=self._settings.mongo_dlq,
        )
        return IngestionService(config)

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._service is None:
                self._service = self._build_service()
            if not self._service.running:
                self._service.start()
        return self.status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if self._service is not None and self._service.running:
                self._service.stop()
        return self.status()

    def reconfigure(self) -> dict[str, Any]:
        """Rebuild consumers from the current environment profile."""
        with self._lock:
            was_running = bool(self._service is not None and self._service.running)
            if was_running:
                self._service.stop()
            self._service = None
        if was_running:
            return self.start()
        return self.status()

    def purge(self) -> dict[str, Any]:
        with self._lock:
            if self._service is None:
                self._service = self._build_service()
            service = self._service
        try:
            purged = service.purge()
            return {"ok": True, "purged": purged, "total_purged": sum(purged.values())}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def status(self) -> dict[str, Any]:
        with self._lock:
            service = self._service
        if service is None:
            return {
                "running": False,
                "started_at": None,
                "mongo_connected": None,
                "rabbitmq_connected": False,
                "totals": {"consumed": 0, "inserted": 0, "invalid": 0},
                "consumers": [],
            }
        try:
            return service.status()
        except Exception as exc:  # noqa: BLE001
            return {"running": service.running, "error": str(exc)}
