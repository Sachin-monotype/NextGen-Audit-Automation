"""MongoDB writer for ingestion — insert batches, ensure indexes, prune old docs.

Ported from audit-sense (`audit-log.repository.ts`, `cleanup.repository.ts`,
`ensure-indexes.ts`). Uses pymongo directly so it can run either inside the backend
or as a standalone worker.
"""

from __future__ import annotations

import logging
from typing import Any

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import BulkWriteError

log = logging.getLogger(__name__)

# Mirrors audit-sense ensure-indexes.ts.
_INDEX_DEFINITIONS: list[tuple[list[tuple[str, int]], str]] = [
    ([("xCorrelationId", ASCENDING)], "idx_xCorrelationId"),
    ([("eventId", ASCENDING)], "idx_eventId"),
    ([("eventVersion", ASCENDING)], "idx_eventVersion"),
    ([("source.operation", ASCENDING)], "idx_source_operation"),
    ([("source.operationState", ASCENDING)], "idx_source_operationState"),
    ([("source.platform", ASCENDING)], "idx_source_platform"),
    ([("source.platformEnvironment", ASCENDING)], "idx_source_platformEnvironment"),
    ([("source.service", ASCENDING)], "idx_source_service"),
    ([("source.osName", ASCENDING)], "idx_source_osName"),
    ([("actor.globalUserId", ASCENDING)], "idx_actor_globalUserId"),
    ([("actor.globalCustomerId", ASCENDING)], "idx_actor_globalCustomerId"),
    ([("occurredAt", DESCENDING)], "idx_occurredAt_desc"),
]


class MongoWriter:
    def __init__(self, url: str, database: str) -> None:
        self._client: MongoClient = MongoClient(url, serverSelectionTimeoutMS=10000)
        self._db: Database = self._client[database]

    def ping(self) -> bool:
        try:
            self._client.admin.command("ping")
            return True
        except Exception:
            return False

    def collection(self, name: str) -> Collection:
        return self._db[name]

    def ensure_indexes(self, collection_names: list[str]) -> None:
        for name in collection_names:
            col = self.collection(name)
            for keys, index_name in _INDEX_DEFINITIONS:
                try:
                    col.create_index(keys, name=index_name, background=True)
                except Exception as exc:  # noqa: BLE001 — indexing must not crash ingestion
                    log.warning("ensure index %s on %s failed: %s", index_name, name, exc)
            log.info("Ensured indexes for collection: %s", name)

    def insert_many(self, collection_name: str, documents: list[dict[str, Any]]) -> int:
        """Insert a batch, tolerating already-ingested docs.

        Messages carry their Mongo ``_id`` from the source, and the subscription queues
        can redeliver the same event (or a queue backlog overlaps what's already stored).
        With ``ordered=True`` a single duplicate ``_id`` (E11000) aborts the whole batch,
        which then gets retried and re-nacked forever — a hot loop that never drains the
        queue. ``ordered=False`` inserts every new doc and reports duplicates as errors we
        can safely ignore (the doc is already present = success). Any *other* write error
        is re-raised so the caller's retry/nack path still protects real failures.
        """
        if not documents:
            return 0
        try:
            result = self.collection(collection_name).insert_many(documents, ordered=False)
            return len(result.inserted_ids)
        except BulkWriteError as exc:
            write_errors = exc.details.get("writeErrors", []) if isinstance(exc.details, dict) else []
            non_dup = [e for e in write_errors if e.get("code") != 11000]
            inserted = int(exc.details.get("nInserted", 0)) if isinstance(exc.details, dict) else 0
            if non_dup:
                # Real errors (not just duplicates) — surface for retry/nack.
                raise
            # All failures were duplicate _id — those docs are already stored.
            return inserted

    def cleanup_collection(self, collection_name: str, max_retain: int) -> int:
        """Keep only the latest ``max_retain`` docs per source.operation.

        Ported from audit-sense cleanup.repository.ts.
        """
        col = self.collection(collection_name)
        pipeline = [
            {"$sort": {"occurredAt": -1}},
            {
                "$group": {
                    "_id": "$source.operation",
                    "docs": {"$push": "$_id"},
                    "count": {"$sum": 1},
                }
            },
            {"$match": {"count": {"$gt": max_retain}}},
            {
                "$project": {
                    "count": 1,
                    "idsToDelete": {
                        "$slice": ["$docs", max_retain, {"$subtract": ["$count", max_retain]}]
                    },
                }
            },
        ]
        ids_to_delete: list[Any] = []
        for group in col.aggregate(pipeline, allowDiskUse=True):
            ids_to_delete.extend(group.get("idsToDelete") or [])
        if not ids_to_delete:
            return 0
        result = col.delete_many({"_id": {"$in": ids_to_delete}})
        return result.deleted_count or 0

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
