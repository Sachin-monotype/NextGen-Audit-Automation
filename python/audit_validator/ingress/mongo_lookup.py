"""Mongo fallback for ingress verify when ingestion drains the tap queues first."""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Protocol

from ..correlation import mongo_correlation_filter
from ..models import JsonDict

log = logging.getLogger(__name__)


class MongoPairLookup(Protocol):
    def latest_pair(
        self,
        operation: str,
        *,
        require_pair: bool = ...,
        correlation_id: str | None = ...,
        actor_global_user_id: str | None = ...,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]: ...


def ingress_verify_via_mongo() -> bool:
    raw = (os.getenv("INGRESS_VERIFY_VIA_MONGO") or "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def lookup_pair_by_correlation(
    operation: str,
    correlation_id: str,
    db: MongoPairLookup | None = None,
) -> tuple[JsonDict | None, JsonDict | None]:
    """Find raw + enriched in Mongo for an owned correlation id."""
    cid = (correlation_id or "").strip()
    op = (operation or "").strip()
    if not cid or not op:
        return None, None

    if db is not None:
        try:
            raw, enriched = db.latest_pair(op, require_pair=False, correlation_id=cid)
            if raw or enriched:
                return raw, enriched
        except Exception as exc:
            log.debug("Mongo db.latest_pair failed for %s: %s", cid[:8], exc)

    if not ingress_verify_via_mongo():
        return None, None

    mongo_url = (os.getenv("MONGO_DB_URL") or "").strip()
    if not mongo_url:
        return None, None

    try:
        from pymongo import DESCENDING, MongoClient

        db_name = (os.getenv("MONGO_DB_NAME") or "AuditLogsPreprod").strip()
        raw_col_name = (os.getenv("MONGO_COLLECTION_RAW") or "raw").strip()
        enr_col_name = (os.getenv("MONGO_COLLECTION_ENRICHED") or "enriched").strip()

        client = MongoClient(mongo_url, serverSelectionTimeoutMS=8000)
        database = client[db_name]
        filt = mongo_correlation_filter(
            cid,
            extra={"source.operation": {"$regex": f"^{re.escape(op)}$", "$options": "i"}},
        )
        raw = database[raw_col_name].find_one(filt, sort=[("occurredAt", DESCENDING)])
        enriched = database[enr_col_name].find_one(filt, sort=[("occurredAt", DESCENDING)])
        if raw or enriched:
            log.info(
                "Ingress Mongo fallback hit for %s correlation=%s (raw=%s enrich=%s)",
                op,
                cid[:8],
                bool(raw),
                bool(enriched),
            )
        return raw, enriched
    except Exception as exc:
        log.warning("Ingress Mongo fallback unavailable: %s", exc)
        return None, None
