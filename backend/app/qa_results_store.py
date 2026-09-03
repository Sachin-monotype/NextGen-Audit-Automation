"""Persist comparison Results in Atlas — one document per scenario.

Collections (per target):

- QA: ``AuditComparisonResult`` / ``QA Result`` (+ optional ``QA_Original``)
- UAT: ``MosaicCatalog`` / ``NextgenCoparisionResult`` (via ``RESULTS_MONGO_*_UAT``)

Env (global QA defaults, or per-target ``RESULTS_MONGO_*_{TARGET}``):

- ``RESULTS_MONGO_URL`` / ``RESULTS_MONGO_URL_UAT``
- ``RESULTS_MONGO_DB`` / ``RESULTS_MONGO_DB_UAT``
- ``RESULTS_MONGO_COLLECTION`` / ``RESULTS_MONGO_COLLECTION_UAT``
- ``RESULTS_MONGO_ORIGINAL_COLLECTION`` (QA only by default)
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from pymongo import ReadPreference, UpdateOne
from pymongo.collection import Collection
from pymongo.errors import PyMongoError

logger = logging.getLogger(__name__)

_RESULTS_TARGETS = frozenset({"qa", "uat"})
_lock = threading.Lock()
_clients: dict[str, Any] = {}
_last_failure_by_url: dict[str, float] = {}
FAILURE_COOLDOWN_SEC = 15.0


def _target_name(target: str | None = None) -> str:
    raw = (target or os.getenv("AUDIT_TARGET") or "qa").strip().lower()
    return raw if raw in _RESULTS_TARGETS else "qa"


def _env_for_target(target: str, key: str, default: str = "") -> str:
    """``RESULTS_MONGO_{key}_{TARGET}`` then (QA only) ``RESULTS_MONGO_{key}``."""
    t = _target_name(target)
    specific = (os.getenv(f"RESULTS_MONGO_{key}_{t.upper()}") or "").strip()
    if specific:
        return specific
    if t == "qa":
        return (os.getenv(f"RESULTS_MONGO_{key}") or default).strip() or default
    return default


def results_mongo_enabled(target: str | None = None) -> bool:
    return bool(_results_mongo_url(target))


def _db_name(target: str | None = None) -> str:
    t = _target_name(target)
    if t == "uat":
        return _env_for_target(t, "DB", "AutomationResult")
    return _env_for_target(t, "DB", "AuditComparisonResult")


def _live_collection_name(target: str | None = None) -> str:
    t = _target_name(target)
    if t == "uat":
        return _env_for_target(t, "COLLECTION", "NextgenAuditCoparisionResult")
    return _env_for_target(t, "COLLECTION", "QA Result")


def _original_collection_name(target: str | None = None) -> str:
    t = _target_name(target)
    if t == "uat":
        return _env_for_target(t, "ORIGINAL_COLLECTION", "")
    return _env_for_target(t, "ORIGINAL_COLLECTION", "QA_Original")


def _results_mongo_url(target: str | None = None) -> str:
    """Connection URL for the Results store (per ``AUDIT_TARGET`` / ``target``)."""
    url = _env_for_target(_target_name(target), "URL", "")
    if not url:
        return ""
    parsed = urlparse(url)
    q = dict(parse_qsl(parsed.query, keep_blank_values=True))
    # Do not force secondaryPreferred on the client — that broke writes when the
    # previous Results cluster lost its primary and also slowed healthy clusters.
    pref = (q.get("readPreference") or "").strip()
    if pref.lower() == "secondarypreferred":
        q["readPreference"] = "primaryPreferred"
    else:
        q.setdefault("readPreference", "primaryPreferred")
    q.setdefault("retryReads", "true")
    q.setdefault("retryWrites", "true")
    return urlunparse(parsed._replace(query=urlencode(q)))


def _socket_timeout_ms() -> int:
    """Per-op socket timeout. Large scenario docs (~1–2MB field rows) need headroom."""
    raw = (os.getenv("RESULTS_MONGO_SOCKET_TIMEOUT_MS") or "").strip()
    try:
        return max(60_000, int(raw)) if raw else 300_000
    except ValueError:
        return 300_000


def _sync_chunk_size() -> int:
    """Docs per bulk_write. Batches of 25 provide fast sync without timeouts."""
    raw = (os.getenv("RESULTS_MONGO_SYNC_CHUNK") or "").strip()
    try:
        return max(1, min(50, int(raw))) if raw else 25
    except ValueError:
        return 25


_last_failure_time = 0.0


def _get_client(target: str | None = None):
    global _last_failure_time
    url = _results_mongo_url(target)
    if not url:
        return None
    import time

    now = time.time()
    last_fail = _last_failure_by_url.get(url, 0.0)
    if now - last_fail < FAILURE_COOLDOWN_SEC:
        return None
    with _lock:
        client = _clients.get(url)
        if client is None:
            from audit_validator.mongo_client import create_mongo_client

            try:
                client = create_mongo_client(
                    url,
                    serverSelectionTimeoutMS=2000,
                    connectTimeoutMS=2000,
                    socketTimeoutMS=_socket_timeout_ms(),
                    retryReads=True,
                    retryWrites=True,
                )
                _clients[url] = client
            except Exception:
                _last_failure_by_url[url] = time.time()
                _last_failure_time = time.time()
                return None
        return client


def _reset_client(target: str | None = None) -> None:
    """Drop cached client after write timeouts / broken sockets."""
    global _last_failure_time
    import time

    url = _results_mongo_url(target)
    if not url:
        return
    _last_failure_by_url[url] = time.time()
    _last_failure_time = time.time()
    with _lock:
        old = _clients.pop(url, None)
    if old is not None:
        try:
            old.close()
        except Exception:
            pass


def _get_collection(*, original: bool = False, target: str | None = None) -> Collection | None:
    """Lazy Atlas client for live ``QA Result`` or immutable ``QA_Original``.

    List/read path uses ``primaryPreferred`` (falls back to secondary if needed).
    Writes use ``_get_write_collection`` (explicit primary).
    """
    client = _get_client(target)
    if client is None:
        return None
    if original and not _original_collection_name(target):
        return None
    name = _original_collection_name(target) if original else _live_collection_name(target)
    db = client.get_database(_db_name(target), read_preference=ReadPreference.PRIMARY_PREFERRED)
    return db[name]


def _get_write_collection(*, original: bool = False, target: str | None = None) -> Collection | None:
    """Collection bound to PRIMARY for upserts/deletes."""
    col = _get_collection(original=original, target=target)
    if col is None:
        return None
    return col.with_options(read_preference=ReadPreference.PRIMARY)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _platform_environment_from_rows(rows: list[Any] | None) -> str:
    """Prefer enriched ``source.platformEnvironment``, else source value."""
    if not isinstance(rows, list):
        return ""
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("field_path") or "") != "source.platformEnvironment":
            continue
        for key in ("value_in_enriched", "value_in_source"):
            val = str(row.get(key) or "").strip().lower()
            if val:
                return val
    return ""


def _platform_environment_from_item(item: dict[str, Any]) -> str:
    top = str(item.get("platformEnvironment") or item.get("platform_environment") or "").strip().lower()
    if top:
        return top
    return _platform_environment_from_rows(item.get("rows") if isinstance(item.get("rows"), list) else None)


def _clean_summary(raw: Any, rows: list[dict[str, Any]]) -> dict[str, int]:
    if isinstance(raw, dict):
        p = int(raw.get("passed") if raw.get("passed") is not None else raw.get("pass") or 0)
        f = int(raw.get("failed") if raw.get("failed") is not None else raw.get("fail") or 0)
        s = int(raw.get("skipped") if raw.get("skipped") is not None else raw.get("skip") or 0)
        na = int(raw.get("na") or 0)
        return {"passed": p, "failed": f, "skipped": s, "na": na}
    p = sum(1 for r in rows if isinstance(r, dict) and r.get("match_status") == "PASS")
    f = sum(1 for r in rows if isinstance(r, dict) and r.get("match_status") == "FAIL")
    s = sum(1 for r in rows if isinstance(r, dict) and r.get("match_status") == "SKIP")
    na = sum(1 for r in rows if isinstance(r, dict) and str(r.get("match_status") or "").upper() in {"N/A", "NA"})
    return {"passed": p, "failed": f, "skipped": s, "na": na}


def _doc_from_item(
    scenario: str, item: dict[str, Any], *, frozen: bool = False, target: str | None = None
) -> dict[str, Any]:
    audit_target = _target_name(target)
    scenario = str(scenario or item.get("operation") or "").strip()
    rows = item.get("rows") if isinstance(item.get("rows"), list) else []
    summary = _clean_summary(item.get("summary"), rows)
    pe = _platform_environment_from_item(item)
    compared_at = str(item.get("compared_at") or "").strip() or _now_iso()
    job_id = str(item.get("job_id") or "").strip() or "legacy_compare"
    job_kind = str(item.get("job_kind") or "").strip()
    if job_kind not in {"compare", "desktop_actor_ingress", "excel", "excel-import"}:
        job_kind = "compare"

    doc: dict[str, Any] = {
        "scenario": scenario,
        "operation": str(item.get("operation") or scenario).strip(),
        "audit_target": audit_target,
        "compared_at": compared_at,
        "job_id": job_id,
        "job_kind": job_kind,
        "summary": summary,
        "rows": rows,
        "row_count": len(rows),
        "platformEnvironment": pe,
    }
    if frozen:
        doc["frozen"] = True
        doc["captured_at"] = _now_iso()
        doc["note"] = "Immutable baseline — do not update"
    else:
        doc["updated_at"] = _now_iso()
    return doc


def _item_from_doc(doc: dict[str, Any] | None, *, target: str | None = None) -> dict[str, Any] | None:
    if not isinstance(doc, dict):
        return None
    scenario = str(doc.get("scenario") or doc.get("operation") or "").strip()
    if not scenario:
        return None
    rows = doc.get("rows") if isinstance(doc.get("rows"), list) else []
    pe = str(doc.get("platformEnvironment") or "").strip().lower()
    if not pe:
        pe = _platform_environment_from_rows(rows)
    return {
        "operation": str(doc.get("operation") or scenario).strip(),
        "compared_at": str(doc.get("compared_at") or ""),
        "job_id": str(doc.get("job_id") or ""),
        "job_kind": str(doc.get("job_kind") or ""),
        "summary": doc.get("summary") if isinstance(doc.get("summary"), dict) else {},
        "rows": rows,
        "audit_target": str(doc.get("audit_target") or _target_name(target)).strip().lower() or "qa",
        "platformEnvironment": pe,
    }


def upsert_scenario(scenario: str, item: dict[str, Any], *, target: str | None = None) -> bool:
    """Insert or replace one live scenario document. Never touches QA_Original."""
    col = _get_write_collection(original=False, target=target)
    if col is None:
        return False
    scenario = str(scenario or "").strip()
    if not scenario:
        return False
    doc = _doc_from_item(scenario, item, target=target)
    try:
        col.update_one({"scenario": scenario}, {"$set": doc}, upsert=True)
        return True
    except PyMongoError as exc:
        logger.warning("QA Result upsert failed for %s: %s", scenario, exc)
        return False


def _friendly_write_error(exc: BaseException) -> str:
    msg = str(exc)
    low = msg.lower()
    if "no primary" in low or "replicasetnoprimary" in low:
        return (
            "Results Mongo has no PRIMARY (cluster unhealthy / paused). "
            "Writes need a healthy Atlas primary — set RESULTS_MONGO_URL to a "
            "writable cluster (e.g. the same as MONGO_DB_URL) and retry Sync."
        )
    if "quota" in low or "space" in low or "storage" in low:
        return f"Atlas storage/quota blocked the write: {msg[:240]}"
    if "ssl" in low or "tls" in low:
        return (
            "TLS/SSL failed talking to Results Mongo. "
            "Check Atlas cluster health, then retry Sync."
        )
    if "timed out" in low or "timeout" in low:
        return (
            "Results Mongo write timed out (large scenario payloads). "
            "Retry Sync — pending docs are written one-at-a-time with retries. "
            f"Detail: {msg[:280]}"
        )
    return msg[:500]


def upsert_many(items: dict[str, dict[str, Any]], *, target: str | None = None) -> dict[str, Any]:
    """Upsert live ``QA Result`` docs (insert new, replace existing).

    Writes in small chunks (default 1) with per-chunk retries so multi‑MB
    field-row documents do not trip a single bulk_write socket timeout.
    """
    import time

    if _get_write_collection(original=False, target=target) is None:
        return {"ok": False, "upserted": 0, "error": "RESULTS_MONGO_URL not set"}
    t = _target_name(target)
    ops: list[UpdateOne] = []
    for scenario, item in items.items():
        sc = str(scenario or "").strip()
        if not sc or not isinstance(item, dict):
            continue
        doc = _doc_from_item(sc, item, target=t)
        ops.append(UpdateOne({"scenario": sc}, {"$set": doc}, upsert=True))
    if not ops:
        return {"ok": True, "upserted": 0, "matched": 0, "total": 0}

    upserted = modified = matched = 0
    chunk_size = _sync_chunk_size()
    failures: list[str] = []
    for i in range(0, len(ops), chunk_size):
        chunk = ops[i : i + chunk_size]
        last_exc: BaseException | None = None
        for attempt in range(1, 4):
            try:
                col = _get_write_collection(original=False, target=target)
                if col is None:
                    return {
                        "ok": False,
                        "upserted": upserted,
                        "modified": modified,
                        "matched": matched,
                        "total": len(ops),
                        "error": "RESULTS_MONGO_URL not set",
                    }
                result = col.bulk_write(chunk, ordered=False)
                upserted += int(result.upserted_count or 0)
                modified += int(result.modified_count or 0)
                matched += int(result.matched_count or 0)
                last_exc = None
                break
            except PyMongoError as exc:
                last_exc = exc
                logger.warning(
                    "QA Result upsert chunk %s-%s attempt %s failed: %s",
                    i,
                    i + len(chunk),
                    attempt,
                    exc,
                )
                _reset_client(target)
                time.sleep(min(2 * attempt, 6))
        if last_exc is not None:
            failures.append(_friendly_write_error(last_exc))
            # Continue remaining chunks so one timeout does not block the rest.
            continue

    if failures:
        return {
            "ok": False,
            "upserted": upserted,
            "modified": modified,
            "matched": matched,
            "total": len(ops),
            "failed_chunks": len(failures),
            "error": failures[0],
            "database": _db_name(target),
            "collection": _live_collection_name(target),
        }
    return {
        "ok": True,
        "upserted": upserted,
        "modified": modified,
        "matched": matched,
        "total": len(ops),
        "database": _db_name(target),
        "collection": _live_collection_name(target),
    }


def delete_scenario(scenario: str, *, include_original: bool = True, target: str | None = None) -> bool:
    """Delete exact scenario from live Results and optional original collection."""
    scenario_clean = str(scenario or "").strip()
    if not scenario_clean:
        return False
    deleted = False
    for is_orig in ([False, True] if include_original else [False]):
        col = _get_write_collection(original=is_orig, target=target)
        if col is None:
            continue
        try:
            res = col.delete_many({
                "$or": [
                    {"scenario": scenario_clean},
                    {"operation": scenario_clean},
                ]
            })
            if res.deleted_count > 0:
                deleted = True
        except PyMongoError as exc:
            logger.warning(
                "QA Result delete failed for %s (orig=%s): %s",
                scenario_clean,
                is_orig,
                exc,
            )
    return deleted


def clear_all_scenarios(*, target: str | None = None) -> int:
    """Clear live ``QA Result`` only — never deletes QA_Original.

    Disabled by default so one teammate cannot wipe the shared store.
    Set ``RESULTS_MONGO_ALLOW_CLEAR=1`` to enable.
    """
    if (os.getenv("RESULTS_MONGO_ALLOW_CLEAR") or "").strip().lower() not in {
        "1",
        "true",
        "yes",
    }:
        logger.warning("clear_all_scenarios blocked (RESULTS_MONGO_ALLOW_CLEAR not set)")
        return 0
    col = _get_write_collection(original=False, target=target)
    if col is None:
        return 0
    try:
        result = col.delete_many({})
        return int(result.deleted_count or 0)
    except PyMongoError as exc:
        logger.warning("QA Result clear failed: %s", exc)
        return 0


def load_all_scenarios(
    *, original: bool = False, include_rows: bool = True, target: str | None = None
) -> dict[str, dict[str, Any]]:
    """Return ``{scenario: result_item}`` from live or original collection.

    ``include_rows=False`` projects out field rows — used for the Results list
    (full rows are fetched per-scenario when an operation is opened).
    """
    col = _get_collection(original=original, target=target)
    if col is None:
        return {}
    out: dict[str, dict[str, Any]] = {}
    projection: dict[str, int] = {"_id": 0}
    if not include_rows:
        projection["rows"] = 0
    try:
        for doc in col.find({}, projection):
            item = _item_from_doc(doc, target=target)
            if not item:
                continue
            if not include_rows:
                item["rows"] = []
            key = str(doc.get("scenario") or item["operation"]).strip()
            out[key] = item
    except Exception as exc:
        logger.warning("Results load_all failed: %s", exc)
        _reset_client(target)
        return {}
    return out


def load_scenario(scenario: str, *, original: bool = False, target: str | None = None) -> dict[str, Any] | None:
    col = _get_collection(original=original, target=target)
    if col is None:
        return None
    scenario = str(scenario or "").strip()
    if not scenario:
        return None
    try:
        doc = col.find_one({"scenario": scenario}, {"_id": 0})
        if not doc:
            doc = col.find_one({"operation": scenario}, {"_id": 0})
        return _item_from_doc(doc, target=target)
    except PyMongoError as exc:
        logger.warning("QA Results load failed for %s: %s", scenario, exc)
        return None


def merge_original_into_live(*, target: str | None = None) -> dict[str, Any]:
    """Copy scenarios from ``QA_Original`` into live ``QA Result`` when missing.

    Never overwrites an existing live scenario. Restores counts after accidental deletes.
    """
    original = load_all_scenarios(original=True, include_rows=True, target=target)
    if not original:
        return {"ok": False, "inserted": 0, "error": "original collection empty or unreachable"}
    live = load_all_scenarios(original=False, include_rows=False, target=target)
    missing = {k: v for k, v in original.items() if k not in live}
    if not missing:
        return {
            "ok": True,
            "inserted": 0,
            "live": len(live),
            "original": len(original),
            "message": "live already has every original scenario",
        }
    # Strip frozen flags; write as live docs.
    to_write = {
        k: {**v, "job_kind": v.get("job_kind") or "restored-from-original"}
        for k, v in missing.items()
    }
    result = upsert_many(to_write, target=target)
    result["restored"] = len(missing)
    result["live_before"] = len(live)
    result["original"] = len(original)
    return result


def seed_qa_original_once(
    items: dict[str, dict[str, Any]] | None = None, *, target: str | None = None
) -> dict[str, Any]:
    """Insert baseline into ``QA_Original`` only when the collection is empty.

    Never updates existing documents — safe to call repeatedly.
    """
    col = _get_collection(original=True, target=target)
    if col is None:
        return {"ok": False, "inserted": 0, "error": "RESULTS_MONGO_URL not set or no original collection"}
    try:
        # Prefer exact count — estimated_document_count can lag and allow double-seed.
        existing = int(col.count_documents({}))
        if existing > 0:
            return {
                "ok": True,
                "inserted": 0,
                "skipped": True,
                "existing": existing,
                "database": _db_name(target),
                "collection": _original_collection_name(target),
                "message": "QA_Original already populated — left unchanged",
            }
        # Unique scenario key so concurrent seeds cannot double-insert.
        try:
            col.create_index("scenario", unique=True, name="scenario_unique")
        except PyMongoError:
            pass
    except PyMongoError as exc:
        return {"ok": False, "inserted": 0, "error": str(exc)}

    if items is None:
        items = load_all_scenarios(original=False, target=target)
    if not items:
        return {
            "ok": False,
            "inserted": 0,
            "error": "no scenarios to seed into QA_Original",
        }

    docs = [
        _doc_from_item(sc, item, frozen=True, target=target)
        for sc, item in items.items()
        if str(sc or "").strip() and isinstance(item, dict)
    ]
    if not docs:
        return {"ok": False, "inserted": 0, "error": "no valid docs"}
    try:
        result = col.with_options(read_preference=ReadPreference.PRIMARY).insert_many(
            docs, ordered=False
        )
        return {
            "ok": True,
            "inserted": len(result.inserted_ids),
            "skipped": False,
            "database": _db_name(target),
            "collection": _original_collection_name(target),
        }
    except PyMongoError as exc:
        # Duplicate key from a concurrent seed → treat as already populated.
        low = str(exc).lower()
        if "duplicate" in low:
            return {
                "ok": True,
                "inserted": 0,
                "skipped": True,
                "message": "QA_Original already populated (concurrent seed)",
                "database": _db_name(target),
                "collection": _original_collection_name(target),
            }
        logger.warning("QA_Original seed: %s", exc)
        return {
            "ok": True,
            "inserted": 0,
            "skipped": True,
            "error": str(exc),
            "database": _db_name(target),
            "collection": _original_collection_name(target),
        }


def dedupe_qa_original(*, target: str | None = None) -> dict[str, Any]:
    """Keep one document per ``scenario`` in QA_Original; delete extras."""
    col = _get_write_collection(original=True, target=target)
    if col is None:
        return {"ok": False, "deleted": 0, "error": "RESULTS_MONGO_URL not set"}
    try:
        pipeline = [
            {"$group": {"_id": "$scenario", "ids": {"$push": "$_id"}, "n": {"$sum": 1}}},
            {"$match": {"n": {"$gt": 1}}},
        ]
        deleted = 0
        for grp in col.aggregate(pipeline):
            ids = list(grp.get("ids") or [])
            if len(ids) < 2:
                continue
            # Keep the first id; drop the rest.
            drop = ids[1:]
            res = col.delete_many({"_id": {"$in": drop}})
            deleted += int(res.deleted_count or 0)
        try:
            col.create_index("scenario", unique=True, name="scenario_unique")
        except PyMongoError:
            pass
        remaining = int(col.count_documents({}))
        return {
            "ok": True,
            "deleted": deleted,
            "remaining": remaining,
            "collection": _original_collection_name(target),
        }
    except PyMongoError as exc:
        return {"ok": False, "deleted": 0, "error": str(exc)}


def sync_qa_local_store(
    project_root,
    *,
    scenarios: list[str] | None = None,
    target: str | None = None,
) -> dict[str, Any]:
    """Push local ``comparison-latest-{target}.json`` into live Results (upsert)."""
    from pathlib import Path

    from .comparison_store import _load_for_target, _normalize_result_operation

    t = _target_name(target)
    root = Path(project_root)
    local = _load_for_target(root, t)
    if not local:
        return {"ok": True, "upserted": 0, "total": 0, "message": f"no local {t.upper()} results"}

    selected_set = set(scenarios) if scenarios else None

    mongo = load_all_scenarios(original=False, include_rows=False, target=t)
    to_sync: dict[str, dict[str, Any]] = {}
    if not mongo:
        for k, v in local.items():
            if not isinstance(v, dict):
                continue
            canon = _normalize_result_operation(str(k))
            if selected_set is None or canon in selected_set or str(k) in selected_set:
                to_sync[canon] = v
        reason = "seed_empty_mongo" if selected_set is None else "selected_scenarios"
    else:
        for key, item in local.items():
            if not isinstance(item, dict):
                continue
            canon = _normalize_result_operation(str(key))
            if selected_set is not None and canon not in selected_set and str(key) not in selected_set:
                continue
            existing = mongo.get(canon) or mongo.get(str(key))
            local_ts = str(item.get("compared_at") or "")
            if existing is None:
                to_sync[canon] = item
                continue
            mongo_ts = str(existing.get("compared_at") or "")
            if selected_set is not None or (local_ts and local_ts > mongo_ts):
                to_sync[canon] = item
        reason = "selected_scenarios" if selected_set is not None else "pending_only"

    if not to_sync:
        return {
            "ok": True,
            "upserted": 0,
            "modified": 0,
            "matched": 0,
            "total": 0,
            "skipped": len(local),
            "message": "nothing to sync" if selected_set else "everything already in Mongo",
            "mode": reason,
        }

    # Smaller payloads first so one huge doc cannot block the whole sync.
    ordered = dict(
        sorted(
            to_sync.items(),
            key=lambda kv: len(kv[1].get("rows") or [])
            if isinstance(kv[1], dict)
            else 0,
        )
    )
    result = upsert_many(ordered, target=t)
    result["mode"] = reason
    result["pending"] = len(to_sync)
    result["local_total"] = len(local)
    return result


def ping(*, target: str | None = None) -> dict[str, Any]:
    t = _target_name(target)
    col = _get_collection(original=False, target=t)
    if col is None:
        return {"ok": False, "error": "RESULTS_MONGO_URL not set", "audit_target": t}
    try:
        # Do not use admin ping — it can require a primary. Count on secondary.
        live = int(col.estimated_document_count())
        orig_col = _get_collection(original=True, target=t)
        original = int(orig_col.estimated_document_count()) if orig_col is not None else 0
        writable = False
        write_error = ""
        try:
            wcol = _get_write_collection(original=False, target=t)
            if wcol is not None:
                probe_doc = _doc_from_item(
                    "__write_probe__",
                    {
                        "operation": "__write_probe__",
                        "compared_at": "2026-08-21T00:00:00+00:00",
                        "job_id": "probe",
                        "job_kind": "compare",
                        "summary": {"pass": 0, "fail": 0, "skip": 0, "total": 0},
                        "rows": [],
                        "platformEnvironment": "web",
                    },
                    target=t,
                )
                wcol.replace_one(
                    {"scenario": "__write_probe__"},
                    probe_doc,
                    upsert=True,
                )
                wcol.delete_one({"scenario": "__write_probe__"})
                writable = True
        except PyMongoError as wexc:
            write_error = _friendly_write_error(wexc)
        out = {
            "ok": True,
            "audit_target": t,
            "database": _db_name(t),
            "collection": _live_collection_name(t),
            "documents": live,
            "original_collection": _original_collection_name(t),
            "original_documents": original,
            "read_preference": "primaryPreferred",
            "event_hint": "UI 'events' = unique operation bases; 'scenarios' = documents",
            "writable": writable,
        }
        if write_error:
            out["write_error"] = write_error
        return out
    except PyMongoError as exc:
        return {"ok": False, "error": str(exc)}


def backfill_platform_environments(*, original: bool = False, target: str | None = None) -> dict[str, Any]:
    """Set top-level ``platformEnvironment`` from comparison rows when missing."""
    col = _get_collection(original=original, target=target)
    if col is None:
        return {"ok": False, "updated": 0, "error": "RESULTS_MONGO_URL not set"}
    updated = 0
    scanned = 0
    try:
        cursor = col.find(
            {},
            {
                "_id": 1,
                "platformEnvironment": 1,
                "rows": {"$elemMatch": {"field_path": "source.platformEnvironment"}},
            },
        )
        write_col = _get_write_collection(original=original, target=target)
        if write_col is None:
            return {"ok": False, "updated": 0, "error": "RESULTS_MONGO_URL not set"}
        for doc in cursor:
            scanned += 1
            existing = str(doc.get("platformEnvironment") or "").strip().lower()
            pe = existing or _platform_environment_from_rows(
                doc.get("rows") if isinstance(doc.get("rows"), list) else None
            )
            if not pe or pe == existing:
                continue
            write_col.update_one({"_id": doc["_id"]}, {"$set": {"platformEnvironment": pe}})
            updated += 1
        return {
            "ok": True,
            "scanned": scanned,
            "updated": updated,
            "collection": _original_collection_name(target) if original else _live_collection_name(target),
        }
    except PyMongoError as exc:
        return {"ok": False, "updated": updated, "error": str(exc)}
