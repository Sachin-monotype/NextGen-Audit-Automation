#!/usr/bin/env python3
"""Generate TestRail-format test cases for the Ingress API events we cover.

Reads ``python/audit_validator/data/ingress_payloads/manifest.json`` (+ each payload)
and emits one happy-path case per covered event plus cross-cutting cases, in the same
JSON schema TestRail import expects (title / priority_id / estimate / refs /
custom_preconds / custom_steps_separated / custom_platforms / custom_levels).

Usage:  python scripts/build_ingress_testcases.py
Output: qa_agent/output/test_cases/FDC-14270_<timestamp>.json
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INGRESS_DIR = REPO / "python" / "audit_validator" / "data" / "ingress_payloads"
MANIFEST = INGRESS_DIR / "manifest.json"
OUT_DIR = REPO.parent / "qa_agent" / "output" / "test_cases"

REFS = "https://monotype.atlassian.net/browse/FDC-14270"
PLATFORM_DESKTOP = 1
LEVEL_INTEGRATION = 2

RAW_QUEUE = "mt.platform,resolver.raw_events_test_queue"
ENR_QUEUE = "mt.platform.resolver.enrichpayload"

# Plugin operations whose enrichment is optional in PP preprod (raw-only is acceptable).
ENRICHMENT_OPTIONAL = {
    "pluginPanelOpened",
    "pluginPanelClosed",
    "pluginMissingFontUnresolved",
    "pluginMissingFontResolved",
    "pluginMissingFontDetected",
    "pluginImportedFontRequested",
    "pluginFontManuallyActivated",
    "pluginFontAutoActivated",
    "pluginFontConflictDetected",
    "pluginDocumentOpened",
}

CATEGORY_LABEL = {
    "plugin_events": "Plugin",
    "desktop_app_preference_page": "Desktop app preferences",
    "font_activations": "Font activation",
    "login": "Login / session",
}

BASE_PRECONDS = (
    "1. mt-audit-log-resolver-service (Ingress API) is deployed and healthy in the PP test environment.\n"
    "2. RabbitMQ `{raw}` and `{enr}` tap queues are provisioned and consumable in PP.\n"
    "3. A valid Bearer token (JWT) for the audit test user is configured (BEARER_TOKEN).\n"
    "4. The `x-client-id` header value expected by the Ingress API is available.\n"
    "5. The `{case}` payload fixture (data/ingress_payloads/{file}) and its cURL "
    "(data/ingress_payloads/{curl}) are available.\n"
    "6. A RabbitMQ consumer (or the NextGen-Audit-Automation ingestion service) is running to capture raw/enriched events."
).format(raw=RAW_QUEUE, enr=ENR_QUEUE, case="{case}", file="{file}", curl="{curl}")


def _envelope_expectation(case: dict, payload: dict) -> str:
    src = payload.get("source") or {}
    subj = payload.get("subject") or {}
    types = src.get("type")
    types_str = ", ".join(types) if isinstance(types, list) else str(types)
    subj_type = subj.get("type") or "(none)"
    parts = [
        "The captured raw event conforms to the Inbound (raw) envelope schema.",
        f"`source.operation` == \"{case['operation']}\".",
        f"`source.service` == \"{case['service']}\".",
        f"`source.type` == [{types_str}].",
        "`source.operationState` == \"success\".",
        "`source.platform` / `platformEnvironment` are preserved from the payload.",
    ]
    if subj_type != "(none)":
        parts.append(f"`subject.type` == \"{subj_type}\".")
    if isinstance(subj.get("id"), list) and subj.get("id"):
        parts.append("`subject.id[]` matches the ids sent in the payload.")
    for k in ("activationMode", "activationType", "plugin", "documentName"):
        if subj.get(k) is not None:
            parts.append(f"`subject.{k}` is preserved verbatim.")
    parts.append(
        "`actor.globalUserId` and `actor.globalCustomerId` match the payload actor "
        "(caller identity carried by the Bearer token)."
    )
    parts.append(
        "`xCorrelationId` matches the value POSTed; `eventId` is a unique id; "
        "`occurredAt` is a valid ISO-8601 timestamp."
    )
    return " ".join(parts)


def _enrichment_step(case: dict) -> dict:
    op = case["operation"]
    if op in ENRICHMENT_OPTIONAL:
        return {
            "content": f"Consume `{ENR_QUEUE}` for an enriched event with the same `xCorrelationId`.",
            "expected": (
                f"Enrichment is OPTIONAL for `{op}` (plugin event) in PP preprod. If an enriched "
                "event is published it carries the same `xCorrelationId` and preserves all raw "
                "fields (only resolver-added enrichment fields differ). If no enriched event is "
                "produced, the raw-only outcome is acceptable and the case still passes."
            ),
        }
    return {
        "content": f"Consume `{ENR_QUEUE}` for the enriched event with the same `xCorrelationId`.",
        "expected": (
            f"An enriched event is published for `{op}` with the same `xCorrelationId`. All raw "
            "fields are preserved (excluding resolver-added enrichment fields such as "
            "`enrichedAt`, `enrichmentVersion`, `enrichedEventId`, and any `enrichedSnapshot`)."
        ),
    }


def _event_case(case: dict, payload: dict) -> dict:
    event = case["event_name"]
    op = case["operation"]
    cat = CATEGORY_LABEL.get(case["category"], case["category"])
    preconds = BASE_PRECONDS.replace("{case}", case["case_id"]).replace(
        "{file}", case["file"]
    ).replace("{curl}", case["curl_file"])
    return {
        "title": (
            f"Verify the Ingress API publishes a valid raw audit event for '{event}' "
            f"({op}) [{cat}]"
        ),
        "priority_id": 2,
        "estimate": "15m",
        "refs": REFS,
        "custom_preconds": preconds,
        "custom_steps_separated": [
            {
                "content": (
                    f"POST the `{event}` payload to the Ingress API using the fixture cURL "
                    f"(data/ingress_payloads/{case['curl_file']}) with valid `authorization` "
                    "(Bearer) and `x-client-id` headers."
                ),
                "expected": "The Ingress API accepts the event and returns a success response (2xx).",
            },
            {
                "content": f"Consume `{RAW_QUEUE}` (PP tap queue) for the published raw event.",
                "expected": (
                    "A raw audit event with the same `xCorrelationId` as the POSTed payload is "
                    f"present on `{RAW_QUEUE}`."
                ),
            },
            {
                "content": "Inspect the raw event envelope, source, actor, and subject blocks.",
                "expected": _envelope_expectation(case, payload),
            },
            _enrichment_step(case),
        ],
        "custom_platforms": PLATFORM_DESKTOP,
        "custom_levels": LEVEL_INTEGRATION,
    }


def _cross_cutting() -> list[dict]:
    p = BASE_PRECONDS.replace("{case}", "any ingress").replace(
        "{file}", "<event>.json"
    ).replace("{curl}", "curls/<event>.sh")
    return [
        {
            "title": "Verify Ingress API rejects an audit event with a missing/invalid Bearer token",
            "priority_id": 2,
            "estimate": "10m",
            "refs": REFS,
            "custom_preconds": p,
            "custom_steps_separated": [
                {
                    "content": "POST a valid ingress payload to the Ingress API with NO `authorization` header.",
                    "expected": "The API rejects the request with 401 Unauthorized (or 403 Forbidden); no event is published to the raw queue.",
                },
                {
                    "content": "POST the same payload with a malformed/expired Bearer token.",
                    "expected": "The API rejects the request with 401/403; no raw event is published.",
                },
            ],
            "custom_platforms": PLATFORM_DESKTOP,
            "custom_levels": LEVEL_INTEGRATION,
        },
        {
            "title": "Verify Ingress API rejects a malformed audit-event payload (schema validation)",
            "priority_id": 2,
            "estimate": "15m",
            "refs": REFS,
            "custom_preconds": p,
            "custom_steps_separated": [
                {
                    "content": "POST a payload missing required envelope fields (e.g. no `source.operation` / no `xCorrelationId`).",
                    "expected": "The API rejects the request with 400 Bad Request; no raw event is published.",
                },
                {
                    "content": "POST a payload with a well-formed envelope but an unknown `source.operation`.",
                    "expected": "The event is either rejected or routed with `audit.operation.unknown`; it is NOT enriched with a known enricher. Behaviour is consistent and documented.",
                },
            ],
            "custom_platforms": PLATFORM_DESKTOP,
            "custom_levels": LEVEL_INTEGRATION,
        },
        {
            "title": "Verify `xCorrelationId` is preserved and `eventId` is unique across ingress events",
            "priority_id": 3,
            "estimate": "15m",
            "refs": REFS,
            "custom_preconds": p,
            "custom_steps_separated": [
                {
                    "content": "POST two distinct ingress events, each with its own unique `xCorrelationId`.",
                    "expected": "Both requests are accepted (2xx).",
                },
                {
                    "content": f"Consume `{RAW_QUEUE}` and retrieve both raw events.",
                    "expected": "Each raw event echoes the exact `xCorrelationId` it was sent with; the two `eventId` values are present and distinct.",
                },
            ],
            "custom_platforms": PLATFORM_DESKTOP,
            "custom_levels": LEVEL_INTEGRATION,
        },
        {
            "title": "Verify actor identity on ingress events matches the Bearer token (JWT) claims",
            "priority_id": 3,
            "estimate": "15m",
            "refs": REFS,
            "custom_preconds": p,
            "custom_steps_separated": [
                {
                    "content": "POST an ingress event using a Bearer token whose JWT carries a known gcid/org_id.",
                    "expected": "The request is accepted (2xx).",
                },
                {
                    "content": f"Consume the raw event from `{RAW_QUEUE}` and inspect the `actor` block.",
                    "expected": (
                        "`actor.globalUserId`, `actor.globalCustomerId`, and `actor.orgId` reflect the "
                        "caller identity carried by the Bearer token (these are asserted by the token, "
                        "not fetched from an external source)."
                    ),
                },
            ],
            "custom_platforms": PLATFORM_DESKTOP,
            "custom_levels": LEVEL_INTEGRATION,
        },
        {
            "title": "Verify enrichment freshness: the latest enriched snapshot is validated (enrichedAt)",
            "priority_id": 3,
            "estimate": "10m",
            "refs": REFS,
            "custom_preconds": p,
            "custom_steps_separated": [
                {
                    "content": "POST an ingress event for an operation that IS enriched, then re-POST the same operation with a new `xCorrelationId`.",
                    "expected": "Both are accepted; two enriched events are produced with distinct `enrichedAt` timestamps.",
                },
                {
                    "content": f"Consume `{ENR_QUEUE}` and inspect `enrichedAt` / `enrichmentVersion` on the enriched events.",
                    "expected": (
                        "Each enriched event carries `enrichmentVersion: 1`, a fresh `enrichedAt` timestamp, "
                        "and a unique `enrichedEventId`. Validation always uses the latest enriched snapshot "
                        "for a given `xCorrelationId` (the resolver enriches once and does not re-enrich)."
                    ),
                },
            ],
            "custom_platforms": PLATFORM_DESKTOP,
            "custom_levels": LEVEL_INTEGRATION,
        },
    ]


def build() -> list[dict]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cases: list[dict] = []
    for row in manifest.get("cases") or []:
        if row.get("skipped"):
            continue
        payload_path = INGRESS_DIR / row["file"]
        payload = json.loads(payload_path.read_text(encoding="utf-8")) if payload_path.is_file() else {}
        cases.append(_event_case(row, payload))
    cases.extend(_cross_cutting())
    return cases


def main() -> None:
    cases = build()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = OUT_DIR / f"FDC-14270_{stamp}.json"
    out.write_text(json.dumps(cases, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(cases)} test case(s) → {out}")


if __name__ == "__main__":
    main()
