"""MongoDB writer for ingestion — insert batches, ensure indexes, prune old docs.

Ported from audit-sense (`audit-log.repository.ts`, `cleanup.repository.ts`,
`ensure-indexes.ts`). Uses pymongo directly so it can run either inside the backend
or as a standalone worker.
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
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


_BSON_INT64_MAX = 9_223_372_036_854_775_807
_BSON_INT64_MIN = -9_223_372_036_854_775_808


def _parse_occurred_at(raw: object) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        dt = raw
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    text = str(raw).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if "." in text:
        head, rest = text.split(".", 1)
        frac = ""
        tz = ""
        for i, ch in enumerate(rest):
            if ch.isdigit():
                frac += ch
            else:
                tz = rest[i:]
                break
        text = f"{head}.{frac[:6]}{tz}" if frac else f"{head}{tz}"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _is_within_keep_hours(occurred_at: object, keep_hours: float, *, now: datetime | None = None) -> bool:
    if keep_hours <= 0:
        return False
    dt = _parse_occurred_at(occurred_at)
    if dt is None:
        return False
    anchor = now or datetime.now(timezone.utc)
    return dt >= anchor - timedelta(hours=keep_hours)


def _sanitize_doc(obj: Any) -> Any:
    """Recursively convert out-of-range ints to float so BSON can store them."""
    if isinstance(obj, dict):
        return {k: _sanitize_doc(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_doc(v) for v in obj]
    if isinstance(obj, int) and not isinstance(obj, bool):
        if obj > _BSON_INT64_MAX or obj < _BSON_INT64_MIN:
            return float(obj)
    return obj


def _extract_op(d: dict[str, Any]) -> str:
    src = d.get("source")
    if isinstance(src, dict) and src.get("operation"):
        return str(src.get("operation"))
    if d.get("operation"):
        return str(d.get("operation"))
    if d.get("event_name"):
        return str(d.get("event_name"))
    if d.get("eventName"):
        return str(d.get("eventName"))
    return "unclassified"


class MongoWriter:
    def __init__(self, url: str, database: str) -> None:
        self._database_name: str = database
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

    def _get_max_retain(self, collection_name: str) -> int:
        try:
            from .config import max_docs_for_mongo_db

            cap = max_docs_for_mongo_db(getattr(self, "_database_name", ""))
            if cap > 0:
                return cap
        except Exception:
            pass
        raw = os.getenv("MONGO_RETENTION_MAX_DOCS_PER_OPERATION")
        if raw:
            try:
                val = int(raw)
                if val > 0:
                    return val
            except ValueError:
                pass
        return 5

    def _prune_older_data(
        self, collection_name: str, documents: list[dict[str, Any]], max_retain: int
    ) -> None:
        if max_retain <= 0 or not documents:
            return
        try:
            col = self.collection(collection_name)
            op_counts: dict[str, int] = defaultdict(int)
            for d in documents:
                op = _extract_op(d)
                op_counts[op] += 1

            stale_ids: list[Any] = []
            for op, incoming_cnt in op_counts.items():
                keep = max(0, max_retain - incoming_cnt)
                if op == "unclassified":
                    query = {
                        "$or": [
                            {"source.operation": {"$exists": False}},
                            {"source.operation": None},
                            {"source.operation": ""},
                        ]
                    }
                else:
                    query = {"source.operation": op}
                cursor = (
                    col.find(query, {"_id": 1})
                    .sort([("occurredAt", DESCENDING), ("_id", DESCENDING)])
                    .skip(keep)
                )
                stale_ids.extend(d["_id"] for d in cursor)

            if stale_ids:
                col.delete_many({"_id": {"$in": stale_ids}})
        except Exception as exc:  # noqa: BLE001
            log.warning("Pruning older data before insert into %s failed: %s", collection_name, exc)

    def insert_many(
        self,
        collection_name: str,
        documents: list[dict[str, Any]],
        *,
        max_retain_per_op: int | None = None,
    ) -> int:
        """Insert a batch, removing older data for matching operations and tolerating already-ingested docs.

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
        safe_docs = [_sanitize_doc(d) for d in documents]
        max_retain = (
            max_retain_per_op
            if max_retain_per_op is not None
            else self._get_max_retain(collection_name)
        )

        if max_retain > 0:
            self._prune_older_data(collection_name, safe_docs, max_retain)

        try:
            result = self.collection(collection_name).insert_many(safe_docs, ordered=False)
            inserted = len(result.inserted_ids)
        except BulkWriteError as exc:
            write_errors = exc.details.get("writeErrors", []) if isinstance(exc.details, dict) else []
            non_dup = [e for e in write_errors if e.get("code") != 11000]
            inserted = int(exc.details.get("nInserted", 0)) if isinstance(exc.details, dict) else 0
            if non_dup:
                raise
        except Exception as exc:
            if "space quota" in str(exc) or getattr(exc, "code", None) == 8000:
                log.warning("Space quota hit on %s — aggressively pruning older docs...", collection_name)
                self.cleanup_collection(collection_name, max_retain=max(1, max_retain // 2))
                result = self.collection(collection_name).insert_many(safe_docs, ordered=False)
                return len(result.inserted_ids)
            raise

        # Clean up any potential overshoot if the incoming batch itself had more than max_retain per op
        if max_retain > 0:
            try:
                col = self.collection(collection_name)
                ops = {_extract_op(d) for d in safe_docs}
                overshoot_ids: list[Any] = []
                for op in ops:
                    query = (
                        {"source.operation": op}
                        if op != "unclassified"
                        else {
                            "$or": [
                                {"source.operation": {"$exists": False}},
                                {"source.operation": None},
                                {"source.operation": ""},
                            ]
                        }
                    )
                    cursor = (
                        col.find(query, {"_id": 1})
                        .sort([("occurredAt", DESCENDING), ("_id", DESCENDING)])
                        .skip(max_retain)
                    )
                    overshoot_ids.extend(d["_id"] for d in cursor)
                if overshoot_ids:
                    col.delete_many({"_id": {"$in": overshoot_ids}})
            except Exception as exc:  # noqa: BLE001
                log.warning("Post-insert prune on %s failed: %s", collection_name, exc)

        return inserted

    def cleanup_collection(
        self,
        collection_name: str,
        max_retain: int,
        *,
        keep_hours: float = 0.0,
    ) -> int:
        """Bound each operation, but never delete docs inside ``keep_hours``.

        Matches backend retention: keep *all* recent docs, then keep the newest
        ``max_retain`` older docs per ``source.operation``. A hard cap of 30 with
        no time window deletes triggered correlation ids within seconds when a
        noisy op (e.g. ``getBatchProgress``) floods the tap queues.
        """
        col = self.collection(collection_name)
        hours = float(keep_hours or 0.0)
        now = datetime.now(timezone.utc)
        pipeline = [
            {"$sort": {"occurredAt": -1}},
            {
                "$group": {
                    "_id": "$source.operation",
                    "docs": {"$push": {"_id": "$_id", "occurredAt": "$occurredAt"}},
                    "count": {"$sum": 1},
                }
            },
        ]
        ids_to_delete: list[Any] = []
        for group in col.aggregate(pipeline, allowDiskUse=True):
            docs = list(group.get("docs") or [])
            recent_ids = {
                d.get("_id")
                for d in docs
                if _is_within_keep_hours(d.get("occurredAt"), hours, now=now)
            }
            older = [d for d in docs if d.get("_id") not in recent_ids]
            drop_older = older[max(0, int(max_retain)) :]
            ids_to_delete.extend(d["_id"] for d in drop_older if d.get("_id") is not None)
        if not ids_to_delete:
            return 0
        result = col.delete_many({"_id": {"$in": ids_to_delete}})
        return result.deleted_count or 0

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
