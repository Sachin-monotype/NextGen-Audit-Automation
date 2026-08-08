"""Background Mongo retention sweep.

Keeps each collection (raw / enriched / dlq) trimmed to the latest N docs per
operation. Runs once at startup and then on a fixed interval, independent of the
ingestion service — so a long-running local server never lets Mongo grow
unbounded even when live ingestion is stopped.

QA and Preprod share one Atlas cluster but use different keep windows
(``MONGO_RETENTION_KEEP_HOURS_QA`` vs ``MONGO_RETENTION_KEEP_HOURS_PP``).
"""

from __future__ import annotations

import logging
import os
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .db import AuditDatabase

log = logging.getLogger(__name__)


def _retention_target_dbs() -> list[str]:
    """Databases to sweep on the audit Mongo URL (never invent unknown names)."""
    raw = (os.getenv("MONGO_RETENTION_DATABASES") or "").strip()
    if raw:
        return [x.strip() for x in raw.split(",") if x.strip()]
    # Default: PP + QA on the shared cluster. Active MONGO_DB_NAME is always included.
    defaults = ["AuditLogsPreprod", "AuditLogsQA"]
    active = (os.getenv("MONGO_DB_NAME") or "").strip()
    out: list[str] = []
    for name in defaults + ([active] if active else []):
        if name and name not in out:
            out.append(name)
    return out


class RetentionScheduler:
    def __init__(
        self,
        db: "AuditDatabase",
        max_docs: int,
        interval_sec: int,
        *,
        keep_hours: float = 3.0,
    ) -> None:
        self._db = db
        self._max_docs = max(1, int(max_docs))
        self._interval = max(60, int(interval_sec))
        self._keep_hours = float(keep_hours)  # fallback / active-db default
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_removed: dict[str, int] = {}
        self.last_run: float | None = None

    def _sweep(self) -> None:
        import time

        try:
            from .db import AuditDatabase

            active = self._db._settings.mongo_db
            targets = _retention_target_dbs()
            if active and active not in targets:
                targets.append(active)

            grand: dict[str, int] = {}
            for db_name in targets:
                hours = AuditDatabase.retention_keep_hours_for_db(db_name)
                # PP: do not keep a long tail of older-than-window docs per op.
                max_docs = self._max_docs
                if "preprod" in db_name.lower() or db_name == "AuditLogsPreprod":
                    max_docs = int(
                        os.getenv("MONGO_RETENTION_MAX_DOCS_PER_OPERATION_PP", "0")
                        or "0"
                    )
                try:
                    self._db.use_database(db_name)
                except Exception as exc:
                    log.warning("Retention skip %s (switch failed): %s", db_name, exc)
                    continue
                removed = self._db.prune_all(max_docs, keep_hours=hours)
                for k, v in removed.items():
                    key = f"{db_name}.{k}"
                    grand[key] = int(v or 0)
                total_db = sum(removed.values())
                if total_db:
                    log.info(
                        "Mongo retention %s removed %s docs (keep %sh, older max %s/op): %s",
                        db_name,
                        total_db,
                        hours,
                        max_docs,
                        removed,
                    )

            # Restore the active / UI-selected database.
            try:
                self._db.use_database(active)
            except Exception:
                pass

            self.last_removed = grand
            self.last_run = time.time()
        except Exception as exc:  # noqa: BLE001 — sweep must never crash the server
            log.warning("Mongo retention sweep failed: %s", exc)

    def _loop(self) -> None:
        skip = os.getenv("MONGO_RETENTION_SKIP_STARTUP_SWEEP", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if not skip:
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
            "Mongo retention scheduler started "
            "(QA keep %sh, PP keep %sh, latest %s/op, every %ss).",
            os.getenv("MONGO_RETENTION_KEEP_HOURS_QA")
            or os.getenv("MONGO_RETENTION_KEEP_HOURS", "3"),
            os.getenv("MONGO_RETENTION_KEEP_HOURS_PP", "0.5"),
            self._max_docs,
            self._interval,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
