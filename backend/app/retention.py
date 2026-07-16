"""Background Mongo retention sweep.

Keeps each collection (raw / enriched / dlq) trimmed to the latest N docs per
operation. Runs once at startup and then on a fixed interval, independent of the
ingestion service — so a long-running local server never lets Mongo grow
unbounded even when live ingestion is stopped.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .db import AuditDatabase

log = logging.getLogger(__name__)


class RetentionScheduler:
    def __init__(self, db: "AuditDatabase", max_docs: int, interval_sec: int) -> None:
        self._db = db
        self._max_docs = max(1, int(max_docs))
        self._interval = max(60, int(interval_sec))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_removed: dict[str, int] = {}
        self.last_run: float | None = None

    def _sweep(self) -> None:
        import time

        try:
            removed = self._db.prune_all(self._max_docs)
            self.last_removed = removed
            self.last_run = time.time()
            total = sum(removed.values())
            if total:
                log.info(
                    "Mongo retention sweep removed %s docs (keep latest %s/op): %s",
                    total,
                    self._max_docs,
                    removed,
                )
        except Exception as exc:  # noqa: BLE001 — sweep must never crash the server
            log.warning("Mongo retention sweep failed: %s", exc)

    def _loop(self) -> None:
        # Immediate sweep on startup to trim any existing bloat, then periodic.
        self._sweep()
        while not self._stop.wait(self._interval):
            self._sweep()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="mongo-retention", daemon=True)
        self._thread.start()
        log.info(
            "Mongo retention scheduler started (keep latest %s/op, every %ss).",
            self._max_docs,
            self._interval,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
