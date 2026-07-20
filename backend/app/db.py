"""MongoDB access for audit log display."""

from __future__ import annotations

import re
from typing import Any

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

from .config import Settings

FILTER_FIELDS = (
    "xCorrelationId",
    "source.operation",
    "actor.globalUserId",
    "source.platformEnvironment",
    "source.service",
    "source.operationState",
)


class AuditDatabase:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = MongoClient(settings.mongo_url, serverSelectionTimeoutMS=8000)
        self._db: Database = self._client[settings.mongo_db]
        self._sort_index_ready: set[str] = set()

    def ping(self) -> bool:
        try:
            self._client.admin.command("ping")
            return True
        except Exception:
            return False

    def use_database(self, name: str) -> str:
        """Point at another Mongo database (creates on first insert)."""
        db_name = (name or "").strip() or self._settings.mongo_db
        self._settings.mongo_db = db_name
        self._db = self._client[db_name]
        self._sort_index_ready.clear()
        # Ensure collections/indexes exist for raw/enrich/dlq
        for tab in ("raw", "enriched", "dlq"):
            col = self.collection(tab)
            self._ensure_sort_index(col)
            try:
                col.create_index(
                    [("source.operation", ASCENDING), ("occurredAt", DESCENDING)],
                    name="idx_operation_occurredAt",
                    background=True,
                )
            except Exception:
                pass
            try:
                col.create_index(
                    [("xCorrelationId", ASCENDING)],
                    name="idx_xCorrelationId",
                    background=True,
                )
            except Exception:
                pass
        return db_name

    def collection(self, tab: str) -> Collection:
        name = {
            "raw": self._settings.mongo_raw,
            "enriched": self._settings.mongo_enriched,
            "dlq": self._settings.mongo_dlq,
        }.get(tab, self._settings.mongo_raw)
        return self._db[name]

    def _ensure_sort_index(self, col: Collection) -> None:
        """Index occurredAt so the dedupe pipeline's $sort is index-backed.

        Shared Atlas tiers reject allowDiskUse, so a blocking in-memory sort of the
        whole collection hits the 32MB limit. An occurredAt index makes the sort
        non-blocking and avoids that error.
        """
        if col.name in self._sort_index_ready:
            return
        try:
            col.create_index([("occurredAt", DESCENDING)], name="idx_occurredAt_desc", background=True)
        except Exception:
            pass
        self._sort_index_ready.add(col.name)

    # Fields rendered as multi-select dropdowns (accept comma-separated values → $in)
    ENUM_FIELDS = (
        "source.platformEnvironment",
        "source.service",
        "source.operationState",
    )

    @staticmethod
    def build_filter(filters: dict[str, str]) -> dict[str, Any]:
        query: dict[str, Any] = {}
        for key in FILTER_FIELDS:
            value = (filters.get(key) or "").strip()
            if not value:
                continue
            if key == "source.operation":
                values = [v.strip() for v in value.split(",") if v.strip()]
                if len(values) > 1:
                    query[key] = {"$in": values}
                else:
                    query[key] = {"$regex": re.escape(values[0]), "$options": "i"}
            elif key == "xCorrelationId":
                # UI generate uses Cloudflare-safe correlationId; API generate uses xCorrelationId.
                # One filter box should match either envelope field.
                query["$or"] = [
                    {"xCorrelationId": value},
                    {"correlationId": value},
                ]
            elif key in AuditDatabase.ENUM_FIELDS:
                # Comma-separated → match any (multi-select dropdown)
                values = [v.strip() for v in value.split(",") if v.strip()]
                query[key] = values[0] if len(values) == 1 else {"$in": values}
            else:
                query[key] = value
        return query

    def distinct_filter_values(self, tab: str | None = None) -> dict[str, list[str]]:
        """Distinct enum values (env/service/state) and operations for filter dropdowns."""
        out: dict[str, set[str]] = {f: set() for f in self.ENUM_FIELDS}
        tabs = (tab,) if tab in ("raw", "enriched", "dlq") else ("raw", "enriched")
        for t in tabs:
            col = self.collection(t)
            for field in self.ENUM_FIELDS:
                try:
                    for v in col.distinct(field):
                        if v not in (None, ""):
                            out[field].add(str(v))
                except Exception:
                    continue
        result = {f: sorted(vals) for f, vals in out.items()}
        if tab in ("raw", "enriched", "dlq"):
            try:
                ops = self.collection(tab).distinct("source.operation")
                result["source.operation"] = sorted(str(o) for o in ops if o)
            except Exception:
                result["source.operation"] = []
        return result

    @staticmethod
    def _row_from_doc(doc: dict[str, Any]) -> dict[str, Any]:
        source = doc.get("source") or {}
        actor = doc.get("actor") or {}
        return {
            "xCorrelationId": doc.get("xCorrelationId") or doc.get("correlationId") or "",
            "correlationId": doc.get("correlationId") or "",
            "source.operation": source.get("operation", ""),
            "source.operationState": source.get("operationState", ""),
            "source.platformEnvironment": source.get("platformEnvironment", ""),
            "source.service": source.get("service", ""),
            "actor.globalUserId": actor.get("globalUserId", ""),
            "occurredAt": doc.get("occurredAt", ""),
            "message": doc,
        }

    @staticmethod
    def has_active_filters(filters: dict[str, str]) -> bool:
        return any((filters.get(key) or "").strip() for key in FILTER_FIELDS)

    # When filtering by a single operation, only the latest few entries are useful.
    OPERATION_FILTER_CAP = 5

    @staticmethod
    def _single_operation_filter(filters: dict[str, str]) -> bool:
        """True when exactly one operation is selected (browse latest few, not all)."""
        raw = (filters.get("source.operation") or "").strip()
        if not raw:
            return False
        values = [v for v in (x.strip() for x in raw.split(",")) if v]
        if len(values) != 1:
            return False
        # No other narrowing filter active → treat as "browse this operation".
        others = [k for k in FILTER_FIELDS if k != "source.operation"]
        return not any((filters.get(k) or "").strip() for k in others)

    def find_logs(
        self,
        tab: str,
        *,
        filters: dict[str, str],
        limit: int,
        page: int = 1,
        unique: bool = False,
    ) -> dict[str, Any]:
        col = self.collection(tab)
        query = self.build_filter(filters)
        # Browsing a single operation → cap to the latest few entries only.
        cap = self.OPERATION_FILTER_CAP if self._single_operation_filter(filters) else None
        if cap is not None:
            limit = min(limit, cap)
        skip = max(page - 1, 0) * limit
        sort = [("occurredAt", DESCENDING), ("_id", DESCENDING)]
        dedupe = unique and not self.has_active_filters(filters)

        try:
            if dedupe:
                self._ensure_sort_index(col)
                total = len(col.distinct("source.operation", query))
                pipeline = [
                    {"$match": query},
                    # Single-field sort → uses idx_occurredAt_desc (non-blocking),
                    # so it avoids the 32MB in-memory sort limit on shared Atlas tiers.
                    {"$sort": {"occurredAt": -1}},
                    {
                        "$group": {
                            "_id": "$source.operation",
                            "doc": {"$first": "$$ROOT"},
                        }
                    },
                    {"$replaceRoot": {"newRoot": "$doc"}},
                    {"$sort": {"occurredAt": -1}},
                    {"$skip": skip},
                    {"$limit": limit},
                    {"$project": {"_id": 0}},
                ]
                cursor = col.aggregate(pipeline, allowDiskUse=True)
            else:
                total = col.count_documents(query)
                if cap is not None:
                    total = min(total, cap)
                cursor = col.find(query, projection={"_id": 0}).sort(sort).skip(skip).limit(limit)
        except Exception as exc:
            return {
                "total": 0,
                "page": page,
                "limit": limit,
                "results": [],
                "unique": dedupe,
                "error": str(exc),
            }

        results = [self._row_from_doc(doc) for doc in cursor]
        return {
            "total": total,
            "page": page,
            "limit": limit,
            "results": results,
            "unique": dedupe,
        }

    def comparable_operations(self) -> list[str]:
        """Operations present in both raw and enriched collections."""
        try:
            raw_ops = set(self.collection("raw").distinct("source.operation"))
            enr_ops = set(self.collection("enriched").distinct("source.operation"))
        except Exception:
            return []
        ops = sorted(x for x in (raw_ops & enr_ops) if x)
        return ops

    def comparable_operations_detail(self) -> list[dict[str, Any]]:
        """Comparable operations with latest enriched metadata for UI filters.

        Uses a single indexed aggregation (sort by occurredAt, group per operation,
        keep the newest env/service) instead of one find_one per operation — the
        per-op approach took ~80s against Atlas and timed the UI out.
        """
        try:
            from audit_validator.event_categories import resolve_category
        except Exception:
            def resolve_category(_op: str) -> str:  # type: ignore[misc]
                return ""

        try:
            raw_ops = {x for x in self.collection("raw").distinct("source.operation") if x}
        except Exception:
            raw_ops = set()

        enr_col = self.collection("enriched")
        self._ensure_sort_index(enr_col)
        pipeline = [
            {"$match": {"source.operation": {"$nin": [None, ""]}}},
            {"$sort": {"occurredAt": DESCENDING}},
            {
                "$group": {
                    "_id": "$source.operation",
                    "environment": {"$first": "$source.platformEnvironment"},
                    "service": {"$first": "$source.service"},
                    "occurred_at": {"$first": "$occurredAt"},
                }
            },
        ]
        items: list[dict[str, Any]] = []
        try:
            for doc in enr_col.aggregate(pipeline, allowDiskUse=True):
                op = str(doc.get("_id") or "")
                if not op or op not in raw_ops:
                    continue
                items.append(
                    {
                        "operation": op,
                        "category": resolve_category(op),
                        "environment": str(doc.get("environment") or ""),
                        "service": str(doc.get("service") or ""),
                        "occurred_at": doc.get("occurred_at"),
                    }
                )
        except Exception:
            # Fall back to a bare operation list so the UI still populates.
            return [
                {"operation": op, "category": resolve_category(op), "environment": "", "service": "", "occurred_at": None}
                for op in self.comparable_operations()
            ]
        items.sort(key=lambda x: x["operation"])
        return items

    @staticmethod
    def _op_filter(operation: str) -> dict[str, Any]:
        return {"source.operation": {"$regex": f"^{re.escape(operation)}$", "$options": "i"}}

    def latest_pair(
        self,
        operation: str,
        *,
        require_pair: bool = True,
        scan_limit: int = 25,
        correlation_id: str | None = None,
        actor_global_user_id: str | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Return a raw + enriched document for an operation.

        Preference order:
        1. Exact ``operation`` + ``xCorrelationId`` (owned generate run)
        2. Latest raw+enriched pair sharing an ``xCorrelationId``, optionally
           constrained to ``actor.globalUserId`` (our Bearer profile) so we do
           not pick up another tenant's concurrent activation of the same op
        3. Latest pair for the operation (legacy / when no actor filter)

        When ``require_pair`` is False, the latest raw and latest enriched are
        returned independently (legacy behaviour).
        """
        filt = self._op_filter(operation)
        raw_col = self.collection("raw")
        enr_col = self.collection("enriched")
        cid_owned = (correlation_id or "").strip()
        actor_uid = (actor_global_user_id or "").strip()

        if cid_owned:
            from audit_validator.correlation import mongo_correlation_filter

            cid_filt = mongo_correlation_filter(cid_owned, extra=filt)
            raw = raw_col.find_one(cid_filt, projection={"_id": 0}, sort=[("occurredAt", DESCENDING)])
            enriched = enr_col.find_one(
                cid_filt, projection={"_id": 0}, sort=[("occurredAt", DESCENDING)]
            )
            if require_pair:
                return (raw, enriched) if (raw and enriched) else (None, None)
            return raw, enriched

        if not require_pair:
            q = dict(filt)
            if actor_uid:
                q["actor.globalUserId"] = actor_uid
            raw = raw_col.find_one(q, sort=[("occurredAt", DESCENDING)]) or raw_col.find_one(
                filt, sort=[("occurredAt", DESCENDING)]
            )
            enriched = enr_col.find_one(q, sort=[("occurredAt", DESCENDING)]) or enr_col.find_one(
                filt, sort=[("occurredAt", DESCENDING)]
            )
            return raw, enriched

        enr_query = dict(filt)
        if actor_uid:
            enr_query["actor.globalUserId"] = actor_uid

        def _scan(query: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
            from audit_validator.correlation import mongo_correlation_filter

            cursor = (
                enr_col.find(query, projection={"_id": 0})
                .sort([("occurredAt", DESCENDING), ("_id", DESCENDING)])
                .limit(max(scan_limit, 1))
            )
            for enriched in cursor:
                cid = (
                    str(enriched.get("xCorrelationId") or "").strip()
                    or str(enriched.get("correlationId") or "").strip()
                )
                if not cid:
                    continue
                raw = raw_col.find_one(
                    mongo_correlation_filter(cid, extra=filt),
                    projection={"_id": 0},
                    sort=[("occurredAt", DESCENDING)],
                )
                if raw:
                    return raw, enriched
            return None, None

        raw, enriched = _scan(enr_query)
        if raw and enriched:
            return raw, enriched
        if actor_uid:
            # Fall back to any pair for the operation if our actor filter missed.
            return _scan(filt)
        return None, None

    @staticmethod
    def _op_correlation_sets(col: Collection) -> dict[str, set[str]]:
        """Per operation → set of xCorrelationIds present (single aggregation)."""
        pipeline = [
            {
                "$match": {
                    "source.operation": {"$nin": [None, ""]},
                    "xCorrelationId": {"$nin": [None, ""]},
                }
            },
            {
                "$group": {
                    "_id": "$source.operation",
                    "cids": {"$addToSet": "$xCorrelationId"},
                }
            },
        ]
        out: dict[str, set[str]] = {}
        for doc in col.aggregate(pipeline, allowDiskUse=True):
            op = doc.get("_id")
            if op:
                out[str(op)] = {str(c) for c in (doc.get("cids") or [])}
        return out

    def operation_stats(self) -> dict[str, Any]:
        """Explain the funnel: distinct raw ops → in both collections → true pairs.

        Clarifies why a Generate of N operations validates fewer: an operation is only
        validatable when it exists in BOTH raw and enriched sharing an xCorrelationId.
        Uses two aggregations (one per collection) so it stays fast enough for the UI.
        """
        try:
            raw_sets = self._op_correlation_sets(self.collection("raw"))
            enr_sets = self._op_correlation_sets(self.collection("enriched"))
        except Exception as exc:
            return {"error": str(exc)}
        raw_ops = set(raw_sets)
        enr_ops = set(enr_sets)
        both = sorted(raw_ops & enr_ops)
        paired_ops = sorted(op for op in both if raw_sets[op] & enr_sets[op])
        unpaired = sorted(op for op in both if not (raw_sets[op] & enr_sets[op]))
        return {
            "raw_distinct": len(raw_ops),
            "enriched_distinct": len(enr_ops),
            "in_both": len(both),
            "true_pairs": len(paired_ops),
            "raw_only": sorted(raw_ops - enr_ops),
            "enriched_only": sorted(enr_ops - raw_ops),
            "unpaired": unpaired,
            "paired_operations": paired_ops,
        }

    def paired_operations(self, operations: list[str] | None = None) -> dict[str, bool]:
        """Map each operation to whether a true raw+enrich pair (same xCorrelationId) exists."""
        ops = operations or self.comparable_operations()
        out: dict[str, bool] = {}
        for op in ops:
            raw, enriched = self.latest_pair(op, require_pair=True)
            out[op] = bool(raw and enriched)
        return out

    def find_fingerprint_pair(
        self,
        operation: str,
        *,
        actor_global_user_id: str | None = None,
        since_iso: str | None = None,
        event_id: str | None = None,
        subject_id: str | None = None,
        scan_limit: int = 40,
        window_sec: int = 600,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str]:
        """Find a raw+enrich pair when ``xCorrelationId`` was not propagated.

        Preference:
          1. Exact ``eventId`` (ingress/cron always mint one)
          2. ``subject.id`` contains subject_id + actor + recent window
          3. Actor-scoped latest pair near ``since_iso`` (nearest occurredAt)
        Returns ``(raw, enriched, method)``.
        """
        from datetime import datetime, timedelta, timezone

        filt = self._op_filter(operation)
        raw_col = self.collection("raw")
        enr_col = self.collection("enriched")
        actor_uid = (actor_global_user_id or "").strip()
        eid = (event_id or "").strip()
        sid = (subject_id or "").strip()

        def _pair_by_query(query: dict[str, Any], method: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str]:
            enriched = enr_col.find_one(
                query, projection={"_id": 0}, sort=[("occurredAt", DESCENDING)]
            )
            if not enriched:
                raw = raw_col.find_one(
                    query, projection={"_id": 0}, sort=[("occurredAt", DESCENDING)]
                )
                return raw, None, method
            cid = str(enriched.get("xCorrelationId") or "").strip()
            if cid:
                raw = raw_col.find_one(
                    {"xCorrelationId": cid, **filt},
                    projection={"_id": 0},
                    sort=[("occurredAt", DESCENDING)],
                )
                if raw:
                    return raw, enriched, method
            # No shared cid — pair by eventId if present
            enr_eid = str(enriched.get("eventId") or "").strip()
            if enr_eid:
                raw = raw_col.find_one(
                    {"eventId": enr_eid, **filt},
                    projection={"_id": 0},
                    sort=[("occurredAt", DESCENDING)],
                )
                if raw:
                    return raw, enriched, f"{method}+eventId"
            # Nearest raw by occurredAt for same actor
            occ = enriched.get("occurredAt")
            raw_q = dict(filt)
            if actor_uid:
                raw_q["actor.globalUserId"] = actor_uid
            raw = raw_col.find_one(raw_q, projection={"_id": 0}, sort=[("occurredAt", DESCENDING)])
            if raw and occ and raw.get("occurredAt"):
                return raw, enriched, f"{method}+nearest"
            return None, enriched, method

        if eid:
            raw, enr, method = _pair_by_query({**filt, "eventId": eid}, "eventId")
            if raw or enr:
                return raw, enr, method

        base: dict[str, Any] = dict(filt)
        if actor_uid:
            base["actor.globalUserId"] = actor_uid
        if sid:
            base["subject.id"] = sid

        since = (since_iso or "").strip()
        if since:
            try:
                # Accept Z / offset ISO; soften by a small skew before generated_at
                ts = datetime.fromisoformat(since.replace("Z", "+00:00"))
                start = (ts - timedelta(seconds=30)).astimezone(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%S.000Z"
                )
                end = (ts + timedelta(seconds=max(window_sec, 60))).astimezone(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%S.000Z"
                )
                base["occurredAt"] = {"$gte": start, "$lte": end}
            except Exception:
                pass

        if sid or since or actor_uid:
            # Prefer enriched first (pair search), then raw-only
            enr_cursor = (
                enr_col.find(base, projection={"_id": 0})
                .sort([("occurredAt", DESCENDING)])
                .limit(max(scan_limit, 1))
            )
            for enriched in enr_cursor:
                cid = str(enriched.get("xCorrelationId") or "").strip()
                if cid:
                    raw = raw_col.find_one(
                        {"xCorrelationId": cid, **filt},
                        projection={"_id": 0},
                        sort=[("occurredAt", DESCENDING)],
                    )
                    if raw:
                        return raw, enriched, "actor_window+cid"
                enr_eid = str(enriched.get("eventId") or "").strip()
                if enr_eid:
                    raw = raw_col.find_one(
                        {"eventId": enr_eid, **filt},
                        projection={"_id": 0},
                        sort=[("occurredAt", DESCENDING)],
                    )
                    if raw:
                        return raw, enriched, "actor_window+eventId"
                # Fallback: first raw in same window
                raw = raw_col.find_one(
                    base, projection={"_id": 0}, sort=[("occurredAt", DESCENDING)]
                )
                if raw:
                    return raw, enriched, "actor_window+nearest"
                return None, enriched, "actor_window_enrich_only"

            raw = raw_col.find_one(base, projection={"_id": 0}, sort=[("occurredAt", DESCENDING)])
            if raw:
                return raw, None, "actor_window_raw_only"

        # Last resort: actor-scoped latest pair (shared cid), no time window
        if actor_uid:
            raw, enr = self.latest_pair(
                operation, require_pair=False, actor_global_user_id=actor_uid
            )
            if raw or enr:
                return raw, enr, "actor_latest"
        return None, None, "none"

    def prune_collection(self, tab: str, max_retain: int) -> int:
        """Keep only the latest ``max_retain`` docs per source.operation in one collection."""
        col = self.collection(tab)
        self._ensure_sort_index(col)
        pipeline = [
            {"$sort": {"occurredAt": -1}},
            {"$group": {"_id": "$source.operation", "docs": {"$push": "$_id"}, "count": {"$sum": 1}}},
            {"$match": {"count": {"$gt": max_retain}}},
            {
                "$project": {
                    "idsToDelete": {
                        "$slice": ["$docs", max_retain, {"$subtract": ["$count", max_retain]}]
                    }
                }
            },
        ]
        ids_to_delete: list[Any] = []
        for group in col.aggregate(pipeline, allowDiskUse=True):
            ids_to_delete.extend(group.get("idsToDelete") or [])
        if not ids_to_delete:
            return 0
        return col.delete_many({"_id": {"$in": ids_to_delete}}).deleted_count or 0

    def prune_raw_enriched_pairs(self, max_retain: int) -> dict[str, int]:
        """Prune raw + enriched together so a kept enriched always keeps its raw twin.

        There are usually more raw docs than enriched. Independent pruning can delete
        the raw side of a pair while leaving the enriched orphan — Compare then fails
        to find a true pair. Strategy per ``source.operation``:

        1. Keep the latest ``max_retain`` **paired** xCorrelationIds (by enriched time).
        2. Delete older paired cids from **both** collections.
        3. For unpaired docs in each collection, keep the latest ``max_retain``, delete rest.
        """
        raw_col = self.collection("raw")
        enr_col = self.collection("enriched")
        self._ensure_sort_index(raw_col)
        self._ensure_sort_index(enr_col)

        def _by_op(col: Collection) -> dict[str, list[dict[str, Any]]]:
            pipeline = [
                {
                    "$match": {
                        "source.operation": {"$nin": [None, ""]},
                    }
                },
                {"$sort": {"occurredAt": -1}},
                {
                    "$group": {
                        "_id": "$source.operation",
                        "docs": {
                            "$push": {
                                "_id": "$_id",
                                "cid": "$xCorrelationId",
                                "occurredAt": "$occurredAt",
                            }
                        },
                    }
                },
            ]
            out: dict[str, list[dict[str, Any]]] = {}
            for g in col.aggregate(pipeline, allowDiskUse=True):
                op = g.get("_id")
                if op:
                    out[str(op)] = list(g.get("docs") or [])
            return out

        raw_by_op = _by_op(raw_col)
        enr_by_op = _by_op(enr_col)
        ops = set(raw_by_op) | set(enr_by_op)

        raw_delete: list[Any] = []
        enr_delete: list[Any] = []

        for op in ops:
            raw_docs = raw_by_op.get(op) or []
            enr_docs = enr_by_op.get(op) or []
            raw_by_cid: dict[str, list[dict[str, Any]]] = {}
            enr_by_cid: dict[str, list[dict[str, Any]]] = {}
            for d in raw_docs:
                cid = str(d.get("cid") or "").strip()
                if cid:
                    raw_by_cid.setdefault(cid, []).append(d)
            for d in enr_docs:
                cid = str(d.get("cid") or "").strip()
                if cid:
                    enr_by_cid.setdefault(cid, []).append(d)

            paired_cids = sorted(
                set(raw_by_cid) & set(enr_by_cid),
                key=lambda c: str((enr_by_cid[c][0] or {}).get("occurredAt") or ""),
                reverse=True,
            )
            keep_paired = set(paired_cids[:max_retain])
            drop_paired = set(paired_cids[max_retain:])

            for cid in drop_paired:
                raw_delete.extend(d["_id"] for d in raw_by_cid.get(cid, []) if d.get("_id") is not None)
                enr_delete.extend(d["_id"] for d in enr_by_cid.get(cid, []) if d.get("_id") is not None)

            # Unpaired: blank cid or cid never present in both collections
            all_paired = set(paired_cids)
            unpaired_raw = [
                d
                for d in raw_docs
                if (not str(d.get("cid") or "").strip())
                or str(d.get("cid")).strip() not in all_paired
            ]
            unpaired_enr = [
                d
                for d in enr_docs
                if (not str(d.get("cid") or "").strip())
                or str(d.get("cid")).strip() not in all_paired
            ]
            for d in unpaired_raw[max_retain:]:
                if d.get("_id") is not None:
                    raw_delete.append(d["_id"])
            for d in unpaired_enr[max_retain:]:
                if d.get("_id") is not None:
                    enr_delete.append(d["_id"])

        removed = {"raw": 0, "enriched": 0}
        if raw_delete:
            removed["raw"] = raw_col.delete_many({"_id": {"$in": raw_delete}}).deleted_count or 0
        if enr_delete:
            removed["enriched"] = enr_col.delete_many({"_id": {"$in": enr_delete}}).deleted_count or 0
        return removed

    def prune_all(self, max_retain: int) -> dict[str, int]:
        """Prune raw+enriched as pairs; dlq independently by latest N per operation."""
        removed: dict[str, int] = {"raw": 0, "enriched": 0, "dlq": 0}
        try:
            pair_removed = self.prune_raw_enriched_pairs(max_retain)
            removed["raw"] = pair_removed.get("raw", 0)
            removed["enriched"] = pair_removed.get("enriched", 0)
        except Exception:
            # Fall back to independent prune (legacy) if aggregation fails
            try:
                removed["raw"] = self.prune_collection("raw", max_retain)
            except Exception:
                removed["raw"] = 0
            try:
                removed["enriched"] = self.prune_collection("enriched", max_retain)
            except Exception:
                removed["enriched"] = 0
        try:
            removed["dlq"] = self.prune_collection("dlq", max_retain)
        except Exception:
            removed["dlq"] = 0
        return removed

    def close(self) -> None:
        self._client.close()
