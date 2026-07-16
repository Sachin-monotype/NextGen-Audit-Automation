"""Load Ingress API audit payloads exported from desktop/plugin spreadsheet."""

from __future__ import annotations

import copy
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..models import JsonDict

_INGRESS_DIR = Path(__file__).resolve().parent.parent / "data" / "ingress_payloads"


@dataclass(frozen=True)
class IngressCase:
    case_id: str
    event_name: str
    category: str
    operation: str
    service: str
    path: Path
    curl_path: Path | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _default_gcid() -> str | None:
    for key in (
        "INGRESS_DEFAULT_GCID",
        "CRON_DEFAULT_GCID",
        "GRAPHQL_CONTEXT_CUSTOMER_ID",
        "GLOBAL_CUSTOMER_ID",
        "OAUTH_GCID",
    ):
        val = (os.getenv(key) or "").strip()
        if val:
            return val
    return None


def _default_user_id() -> str | None:
    for key in ("INGRESS_DEFAULT_USER_ID", "NOTIFICATION_CLEANUP_USER_ID"):
        val = (os.getenv(key) or "").strip()
        if val:
            return val
    return None


def normalize_ingress_payload(payload: JsonDict, *, case_id: str) -> JsonDict:
    """Fresh ids/timestamp; patch actor ids from env when configured."""
    out = copy.deepcopy(payload)
    out["xCorrelationId"] = str(uuid.uuid4())
    out["eventId"] = str(uuid.uuid4())
    out["occurredAt"] = _now_iso()

    gcid = _default_gcid()
    uid = _default_user_id()
    actor = out.setdefault("actor", {})
    if isinstance(actor, dict):
        if gcid and not actor.get("globalCustomerId"):
            actor["globalCustomerId"] = gcid
        if uid and not actor.get("globalUserId"):
            actor["globalUserId"] = uid
    return out


def load_ingress_cases(
    ingress_dir: Path | None = None,
    *,
    category_filter: frozenset[str] | None = None,
) -> list[IngressCase]:
    base = ingress_dir or _INGRESS_DIR
    manifest_path = base / "manifest.json"
    if not manifest_path.is_file():
        return []

    meta = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases: list[IngressCase] = []
    for row in meta.get("cases") or []:
        if row.get("skipped"):
            continue
        if category_filter and row.get("category") not in category_filter:
            continue
        case_id = str(row["case_id"])
        path = base / str(row.get("file") or f"{case_id}.json")
        if not path.is_file():
            continue
        curl_rel = row.get("curl_file") or f"curls/{case_id}.sh"
        curl_path = base / str(curl_rel)
        cases.append(
            IngressCase(
                case_id=case_id,
                event_name=str(row.get("event_name") or case_id),
                category=str(row.get("category") or "unknown"),
                operation=str(row.get("operation") or case_id),
                service=str(row.get("service") or "ingress"),
                path=path,
                curl_path=curl_path if curl_path.is_file() else None,
            )
        )
    return cases
