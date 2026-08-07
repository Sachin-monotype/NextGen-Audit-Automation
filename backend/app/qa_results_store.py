"""Persist QA comparison Results in Atlas — one document per scenario.

Collections (same DB ``AuditComparisonResult``):

- ``QA Result`` — live Results source of truth (upsert on every QA compare)
- ``QA_Original`` — immutable baseline snapshot (seed once; never update)

Env:

- ``RESULTS_MONGO_URL``
- ``RESULTS_MONGO_DB`` (default ``AuditComparisonResult``)
- ``RESULTS_MONGO_COLLECTION`` (default ``QA Result``)
- ``RESULTS_MONGO_ORIGINAL_COLLECTION`` (default ``QA_Original``)
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, UpdateOne
from pymongo.collection import Collection
from pymongo.errors import PyMongoError

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_client = None
_indexed_live = False
_indexed_original = False


def results_mongo_enabled() -> bool:
    return bool((os.getenv("RESULTS_MONGO_URL") or "").strip())


def _db_name() -> str:
    return (os.getenv("RESULTS_MONGO_DB") or "AuditComparisonResult").strip() or "AuditComparisonResult"


def _live_collection_name() -> str:
    return (os.getenv("RESULTS_MONGO_COLLECTION") or "QA Result").strip() or "QA Result"


def _original_collection_name() -> str:
    return (
        os.getenv("RESULTS_MONGO_ORIGINAL_COLLECTION") or "QA_Original"
    ).strip() or "QA_Original"


def _get_client():
    global _client
    url = (os.getenv("RESULTS_MONGO_URL") or "").strip()
    if not url:
        return None
    with _lock:
        if _client is None:
            from audit_validator.mongo_client import create_mongo_client

            _client = create_mongo_client(url, serverSelectionTimeoutMS=12000)
        return _client


def _get_collection(*, original: bool = False) -> Collection | None:
    """Lazy Atlas client for live ``QA Result`` or immutable ``QA_Original``."""
    global _indexed_live, _indexed_original
    client = _get_client()
    if client is None:
        return None
    name = _original_collection_name() if original else _live_collection_name()
    col = client[_db_name()][name]
    with _lock:
        if original and not _indexed_original:
            try:
                col.create_index(
                    [("scenario", ASCENDING)],
                    unique=True,
                    name="uniq_scenario",
                )
                _indexed_original = True
            except PyMongoError as exc:
                logger.warning("QA_Original index ensure failed: %s", exc)
        elif not original and not _indexed_live:
            try:
                col.create_index(
                    [("scenario", ASCENDING)],
                    unique=True,
                    name="uniq_scenario",
                )
                _indexed_live = True
            except PyMongoError as exc:
                logger.warning("QA Result index ensure failed: %s", exc)
    return col


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _doc_from_item(scenario: str, item: dict[str, Any], *, frozen: bool = False) -> dict[str, Any]:
    scenario = str(scenario or item.get("operation") or "").strip()
    summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
    rows = item.get("rows") if isinstance(item.get("rows"), list) else []
    doc: dict[str, Any] = {
        "scenario": scenario,
        "operation": str(item.get("operation") or scenario).strip(),
        "audit_target": "qa",
        "compared_at": str(item.get("compared_at") or ""),
        "job_id": str(item.get("job_id") or ""),
        "job_kind": str(item.get("job_kind") or ""),
        "summary": summary,
        "rows": rows,
        "row_count": len(rows),
    }
    if frozen:
        doc["frozen"] = True
        doc["captured_at"] = _now_iso()
        doc["note"] = "Immutable baseline — do not update"
    else:
        doc["updated_at"] = _now_iso()
    return doc


def _item_from_doc(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(doc, dict):
        return None
    scenario = str(doc.get("scenario") or doc.get("operation") or "").strip()
    if not scenario:
        return None
    return {
        "operation": str(doc.get("operation") or scenario).strip(),
        "compared_at": str(doc.get("compared_at") or ""),
        "job_id": str(doc.get("job_id") or ""),
        "job_kind": str(doc.get("job_kind") or ""),
        "summary": doc.get("summary") if isinstance(doc.get("summary"), dict) else {},
        "rows": doc.get("rows") if isinstance(doc.get("rows"), list) else [],
        "audit_target": "qa",
    }


def upsert_scenario(scenario: str, item: dict[str, Any]) -> bool:
    """Insert or replace one live scenario document. Never touches QA_Original."""
    col = _get_collection(original=False)
    if col is None:
        return False
    scenario = str(scenario or "").strip()
    if not scenario:
        return False
    doc = _doc_from_item(scenario, item)
    try:
        col.update_one({"scenario": scenario}, {"$set": doc}, upsert=True)
        return True
    except PyMongoError as exc:
        logger.warning("QA Result upsert failed for %s: %s", scenario, exc)
        return False


def upsert_many(items: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Bulk upsert live ``QA Result`` docs. Never touches QA_Original."""
    col = _get_collection(original=False)
    if col is None:
        return {"ok": False, "upserted": 0, "error": "RESULTS_MONGO_URL not set"}
    ops: list[UpdateOne] = []
    for scenario, item in items.items():
        sc = str(scenario or "").strip()
        if not sc or not isinstance(item, dict):
            continue
        doc = _doc_from_item(sc, item)
        ops.append(UpdateOne({"scenario": sc}, {"$set": doc}, upsert=True))
    if not ops:
        return {"ok": True, "upserted": 0, "matched": 0}
    try:
        result = col.bulk_write(ops, ordered=False)
        return {
            "ok": True,
            "upserted": int(result.upserted_count or 0),
            "modified": int(result.modified_count or 0),
            "matched": int(result.matched_count or 0),
            "total": len(ops),
            "database": _db_name(),
            "collection": _live_collection_name(),
        }
    except PyMongoError as exc:
        logger.warning("QA Result bulk upsert failed: %s", exc)
        return {"ok": False, "upserted": 0, "error": str(exc)}


def delete_scenario(scenario: str) -> bool:
    """Delete from live ``QA Result`` only."""
    col = _get_collection(original=False)
    if col is None:
        return False
    scenario = str(scenario or "").strip()
    if not scenario:
        return False
    try:
        col.delete_one({"scenario": scenario})
        return True
    except PyMongoError as exc:
        logger.warning("QA Result delete failed for %s: %s", scenario, exc)
        return False


def clear_all_scenarios() -> int:
    """Clear live ``QA Result`` only — never deletes QA_Original."""
    col = _get_collection(original=False)
    if col is None:
        return 0
    try:
        result = col.delete_many({})
        return int(result.deleted_count or 0)
    except PyMongoError as exc:
        logger.warning("QA Result clear failed: %s", exc)
        return 0


def load_all_scenarios(
    *, original: bool = False, include_rows: bool = True
) -> dict[str, dict[str, Any]]:
    """Return ``{scenario: result_item}`` from live or original collection.

    ``include_rows=False`` projects out field rows — used for the Results list
    (full rows are fetched per-scenario when an operation is opened).
    """
    col = _get_collection(original=original)
    if col is None:
        return {}
    out: dict[str, dict[str, Any]] = {}
    projection: dict[str, int] = {"_id": 0}
    if not include_rows:
        projection["rows"] = 0
    try:
        for doc in col.find({}, projection):
            item = _item_from_doc(doc)
            if not item:
                continue
            if not include_rows:
                item["rows"] = []
            key = str(doc.get("scenario") or item["operation"]).strip()
            out[key] = item
    except PyMongoError as exc:
        logger.warning("QA Results load_all failed: %s", exc)
        return {}
    return out


def load_scenario(scenario: str, *, original: bool = False) -> dict[str, Any] | None:
    col = _get_collection(original=original)
    if col is None:
        return None
    scenario = str(scenario or "").strip()
    if not scenario:
        return None
    try:
        doc = col.find_one({"scenario": scenario}, {"_id": 0})
        if not doc:
            # Tolerate operation-key lookups.
            doc = col.find_one({"operation": scenario}, {"_id": 0})
        return _item_from_doc(doc)
    except PyMongoError as exc:
        logger.warning("QA Results load failed for %s: %s", scenario, exc)
        return None


def seed_qa_original_once(items: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Insert baseline into ``QA_Original`` only when the collection is empty.

    Never updates existing documents — safe to call repeatedly.
    """
    col = _get_collection(original=True)
    if col is None:
        return {"ok": False, "inserted": 0, "error": "RESULTS_MONGO_URL not set"}
    try:
        existing = col.estimated_document_count()
        if existing > 0:
            return {
                "ok": True,
                "inserted": 0,
                "skipped": True,
                "existing": int(existing),
                "database": _db_name(),
                "collection": _original_collection_name(),
                "message": "QA_Original already populated — left unchanged",
            }
    except PyMongoError as exc:
        return {"ok": False, "inserted": 0, "error": str(exc)}

    if items is None:
        # Prefer current live QA Result; fall back to local JSON caller-supplied.
        items = load_all_scenarios(original=False)
    if not items:
        return {
            "ok": False,
            "inserted": 0,
            "error": "no scenarios to seed into QA_Original",
        }

    docs = [
        _doc_from_item(sc, item, frozen=True)
        for sc, item in items.items()
        if str(sc or "").strip() and isinstance(item, dict)
    ]
    if not docs:
        return {"ok": False, "inserted": 0, "error": "no valid docs"}
    try:
        result = col.insert_many(docs, ordered=False)
        return {
            "ok": True,
            "inserted": len(result.inserted_ids),
            "skipped": False,
            "database": _db_name(),
            "collection": _original_collection_name(),
        }
    except PyMongoError as exc:
        # Partial insert on duplicate key — treat as already seeded.
        logger.warning("QA_Original seed: %s", exc)
        return {
            "ok": True,
            "inserted": 0,
            "skipped": True,
            "error": str(exc),
            "database": _db_name(),
            "collection": _original_collection_name(),
        }


def sync_qa_local_store(project_root) -> dict[str, Any]:
    """Push every local ``comparison-latest-qa.json`` scenario into live QA Result."""
    from pathlib import Path

    from .comparison_store import _load_for_target

    root = Path(project_root)
    data = _load_for_target(root, "qa")
    if not data:
        return {"ok": True, "upserted": 0, "total": 0, "message": "no local QA results"}
    return upsert_many(data)


def ping() -> dict[str, Any]:
    col = _get_collection(original=False)
    if col is None:
        return {"ok": False, "error": "RESULTS_MONGO_URL not set"}
    try:
        col.database.client.admin.command("ping")
        live = int(col.estimated_document_count())
        orig_col = _get_collection(original=True)
        original = int(orig_col.estimated_document_count()) if orig_col is not None else 0
        return {
            "ok": True,
            "database": _db_name(),
            "collection": _live_collection_name(),
            "documents": live,
            "original_collection": _original_collection_name(),
            "original_documents": original,
        }
    except PyMongoError as exc:
        return {"ok": False, "error": str(exc)}
