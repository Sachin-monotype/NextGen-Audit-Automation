"""Formal Ingress API test case definitions (no hardcoded queue names)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_INGRESS_DIR = Path(__file__).resolve().parent.parent / "data" / "ingress_payloads"


@dataclass(frozen=True)
class IngressTestCase:
    case_id: str
    title: str
    category: str
    operation: str
    service: str
    trigger: str
    description: str
    payload_file: str
    curl_file: str
    expected_http: str
    raw_queue_env: str
    enriched_queue_env: str
    skipped: bool = False
    duplicate_of: str | None = None


def _description(case_id: str, event_name: str, category: str) -> str:
    return (
        f"POST `{event_name}` via Ingress API ({category.replace('_', ' ')}). "
        "Verify HTTP 2xx, raw message on ${INGRESS_RAW_QUEUE}, "
        "and enriched message on ${INGRESS_ENRICHED_QUEUE} when enrichment applies."
    )


def load_ingress_test_cases(ingress_dir: Path | None = None) -> list[IngressTestCase]:
    base = ingress_dir or _INGRESS_DIR
    path = base / "test_cases.json"
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        out: list[IngressTestCase] = []
        for row in data.get("cases") or []:
            out.append(
                IngressTestCase(
                    case_id=str(row["case_id"]),
                    title=str(row.get("title") or row["case_id"]),
                    category=str(row.get("category") or "unknown"),
                    operation=str(row.get("operation") or row["case_id"]),
                    service=str(row.get("service") or "ingress"),
                    trigger=str(row.get("trigger") or "ingress-api"),
                    description=str(row.get("description") or ""),
                    payload_file=str(row.get("payload_file") or f"{row['case_id']}.json"),
                    curl_file=str(row.get("curl_file") or f"curls/{row['case_id']}.sh"),
                    expected_http=str(row.get("expected_http") or "2xx"),
                    raw_queue_env=str(row.get("raw_queue_env") or "INGRESS_RAW_QUEUE"),
                    enriched_queue_env=str(row.get("enriched_queue_env") or "INGRESS_ENRICHED_QUEUE"),
                    skipped=bool(row.get("skipped")),
                    duplicate_of=row.get("duplicate_of"),
                )
            )
        return [c for c in out if not c.skipped]

    manifest = base / "manifest.json"
    if not manifest.is_file():
        return []
    meta = json.loads(manifest.read_text(encoding="utf-8"))
    cases: list[IngressTestCase] = []
    for row in meta.get("cases") or []:
        if row.get("skipped"):
            continue
        case_id = str(row["case_id"])
        event_name = str(row.get("event_name") or case_id)
        category = str(row.get("category") or "unknown")
        cases.append(
            IngressTestCase(
                case_id=case_id,
                title=event_name,
                category=category,
                operation=str(row.get("operation") or case_id),
                service=str(row.get("service") or "ingress"),
                trigger="ingress-api",
                description=_description(case_id, event_name, category),
                payload_file=str(row.get("file") or f"{case_id}.json"),
                curl_file=str(row.get("curl_file") or f"curls/{case_id}.sh"),
                expected_http="2xx",
                raw_queue_env="INGRESS_RAW_QUEUE",
                enriched_queue_env="INGRESS_ENRICHED_QUEUE",
                duplicate_of=row.get("duplicate_of"),
            )
        )
    return cases
