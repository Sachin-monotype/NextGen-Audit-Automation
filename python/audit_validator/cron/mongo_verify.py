"""Poll Mongo until cron case correlations have raw (and enriched when expected)."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from ..case_keys import cron_case_key, cron_display_operation
from ..generation_tracker import get_owned_correlation
from ..ingress.mongo_lookup import lookup_pair_by_correlation
from .payloads import CronCase, expects_cron_enrichment, load_cron_cases

log = logging.getLogger(__name__)


@dataclass
class CronMongoStatus:
    case_id: str
    operation: str
    display: str
    correlation_id: str
    raw: bool
    enriched: bool
    expects_enrich: bool


def cron_verify_timeout_sec() -> float:
    raw = (os.getenv("CRON_VERIFY_TIMEOUT_SEC") or os.getenv("GENERATE_VERIFY_TIMEOUT_SEC") or "90").strip()
    try:
        return max(15.0, float(raw))
    except ValueError:
        return 90.0


def wait_for_cron_cases_in_mongo(
    case_ids: list[str],
    *,
    project_root: Any = None,
    db: Any | None = None,
    wait_sec: float | None = None,
    poll_sec: float = 3.0,
) -> list[CronMongoStatus]:
    """Block until each case has raw in Mongo (and enriched when expected)."""
    timeout = wait_sec if wait_sec is not None else cron_verify_timeout_sec()
    cases_by_id: dict[str, CronCase] = {c.case_id: c for c in load_cron_cases()}
    pending = [cid for cid in case_ids if cid in cases_by_id]
    if not pending:
        return []

    statuses: dict[str, CronMongoStatus] = {}
    deadline = time.monotonic() + timeout

    while pending and time.monotonic() < deadline:
        still: list[str] = []
        for cid in pending:
            case = cases_by_id[cid]
            owned = get_owned_correlation(
                case.operation,
                project_root=project_root,
                case_key=cron_case_key(cid),
            ) or ""
            if not owned:
                still.append(cid)
                continue
            raw_payload = None
            if db is not None:
                try:
                    raw_payload, enriched, _ = db.latest_pair(
                        case.operation, require_pair=False, correlation_id=owned
                    )
                except Exception as exc:
                    log.debug("Mongo latest_pair failed for cron:%s: %s", cid, exc)
                    raw_payload, enriched = None, None
            else:
                raw_payload, enriched = lookup_pair_by_correlation(case.operation, owned, db=db)

            exp_enrich = expects_cron_enrichment(case.operation)
            raw_ok = bool(raw_payload)
            enr_ok = bool(enriched) if exp_enrich else True
            display = cron_display_operation(case.operation, cid)
            statuses[cid] = CronMongoStatus(
                case_id=cid,
                operation=case.operation,
                display=display,
                correlation_id=owned,
                raw=raw_ok,
                enriched=bool(enriched),
                expects_enrich=exp_enrich,
            )
            if not raw_ok or not enr_ok:
                still.append(cid)
        pending = still
        if pending:
            time.sleep(poll_sec)

    # Final pass — record whatever we have
    out: list[CronMongoStatus] = []
    for cid in case_ids:
        if cid in statuses:
            out.append(statuses[cid])
            continue
        case = cases_by_id.get(cid)
        if not case:
            continue
        owned = get_owned_correlation(
            case.operation, project_root=project_root, case_key=cron_case_key(cid)
        ) or ""
        out.append(
            CronMongoStatus(
                case_id=cid,
                operation=case.operation,
                display=cron_display_operation(case.operation, cid),
                correlation_id=owned,
                raw=False,
                enriched=False,
                expects_enrich=expects_cron_enrichment(case.operation),
            )
        )
    return out
