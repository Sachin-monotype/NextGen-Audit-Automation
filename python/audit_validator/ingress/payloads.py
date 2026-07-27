"""Load Ingress API audit payloads exported from desktop/plugin spreadsheet."""

from __future__ import annotations

import copy
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..models import JsonDict
from .runtime_context import apply_ingress_runtime_context

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


def apply_ingress_runtime_identity(payload: JsonDict, *, now_iso: str | None = None) -> None:
    """In-place: actor/source/device fields from JWT + host + Monotype app prefs."""
    apply_ingress_runtime_context(payload)


def normalize_ingress_payload(payload: JsonDict, *, case_id: str) -> JsonDict:
    """Fresh ids/timestamp; dynamic actor/source/device context before POST."""
    out = copy.deepcopy(payload)
    now_iso = _now_iso()
    out["xCorrelationId"] = str(uuid.uuid4())
    out["eventId"] = str(uuid.uuid4())
    out["occurredAt"] = now_iso
    apply_ingress_runtime_context(out)
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
