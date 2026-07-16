"""Inject scheduler/cron raw audit envelopes and validate resolver enrichment."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeAlias

from ..config import AppConfig, load_config
from ..models import JsonDict, ValidationResult, ValidationStatus
from ..rabbitmq.collector import QueueEventCollector
from ..rabbitmq.publisher import publish_raw_event
from ..report import print_report, write_json_report
from ..report_paths import e2e_cron_results_json, ensure_report_dirs
from ..template_registry import OPERATION_TEMPLATE_MAP
from ..validator import validate_event_pair
from .payloads import (
    CRON_DEFERRED_ENRICHER_OPERATIONS,
    CRON_NO_ENRICHER_OPERATIONS,
    CronCase,
    amqp_routing_key_for_payload,
    expects_cron_enrichment,
    load_cron_cases,
    normalize_cron_payload,
    resolve_byof_contract_id,
    validate_cron_event_pair,
)

log = logging.getLogger(__name__)

PublishedCron: TypeAlias = tuple[CronCase, JsonDict, str]


@dataclass
class CronCaseResult:
    case_id: str
    routing_key: str
    operation: str
    service: str
    correlation_id: str
    publish_status: str
    enrich_status: str
    validation_status: str
    error: str = ""
    jira_refs: tuple[str, ...] = ()


@dataclass
class CronRunResult:
    cases: list[CronCaseResult] = field(default_factory=list)
    validation_results: list[ValidationResult] = field(default_factory=list)

    @property
    def pass_count(self) -> int:
        return sum(1 for c in self.cases if c.validation_status == "PASS")

    @property
    def fail_count(self) -> int:
        return sum(1 for c in self.cases if c.validation_status == "FAIL")

    @property
    def warn_count(self) -> int:
        return sum(1 for c in self.cases if c.validation_status == "WARN")


def _default_gcid() -> str | None:
    for key in (
        "CRON_DEFAULT_GCID",
        "GLOBAL_CUSTOMER_ID",
        "GRAPHQL_CONTEXT_CUSTOMER_ID",
        "NEXTGEN_UI_GCID",
        "OAUTH_GCID",
    ):
        val = (os.getenv(key) or "").strip()
        if val:
            return val
    return None


def _publish_case(cfg: AppConfig, payload: JsonDict) -> None:
    amqp_rk = amqp_routing_key_for_payload(payload)
    publish_raw_event(cfg.rabbitmq, payload, amqp_routing_key=amqp_rk)


def inject_cron_payloads(
    cfg: AppConfig,
    *,
    case_filter: frozenset[str] | None = None,
) -> tuple[list[PublishedCron], list[CronCaseResult]]:
    """
    Publish scheduler/cron samples to the raw-events exchange.

    Intended to run after GQL simulation on an already-started collector (before settle).
    """
    gcid = _default_gcid()
    byof_contract_id = resolve_byof_contract_id()
    cases = load_cron_cases()
    if case_filter:
        cases = [c for c in cases if c.case_id in case_filter]
    published: list[PublishedCron] = []
    failures: list[CronCaseResult] = []

    for case in cases:
        try:
            raw = json.loads(case.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("payload must be a JSON object")
            payload = normalize_cron_payload(
                raw,
                case_id=case.case_id,
                gcid=gcid,
                byof_contract_id=byof_contract_id
                if case.case_id in {"licneseexpiry", "byofLicenceExpiry"}
                else None,
            )
            cid = str(payload["xCorrelationId"])
            _publish_case(cfg, payload)
            published.append((case, payload, cid))
            try:
                from ..generation_tracker import record_generation

                actor = payload.get("actor") if isinstance(payload.get("actor"), dict) else {}
                record_generation(
                    case.operation or case.case_id,
                    cid,
                    kind="cron",
                    project_root=cfg.project_root,
                    meta={
                        "case_id": case.case_id,
                        "eventId": payload.get("eventId"),
                        "profile_id": actor.get("globalUserId"),
                        "customer_id": actor.get("globalCustomerId"),
                    },
                )
            except Exception:
                pass
            log.info("Published cron case %s correlation=%s", case.case_id, cid[:8])
            time.sleep(0.3)
        except Exception as exc:
            log.exception("Failed to publish cron case %s", case.case_id)
            failures.append(
                CronCaseResult(
                    case_id=case.case_id,
                    routing_key=case.routing_key,
                    operation=case.operation,
                    service=case.service,
                    correlation_id="",
                    publish_status="FAIL",
                    enrich_status="SKIP",
                    validation_status="FAIL",
                    error=str(exc),
                    jira_refs=case.jira_refs,
                )
            )
    return published, failures


def summarize_cron_results(
    collector: QueueEventCollector,
    published: list[PublishedCron],
    publish_failures: list[CronCaseResult],
    validation_by_cid: dict[str, ValidationResult] | None = None,
) -> CronRunResult:
    """Build per-case cron summary from collector + E2E validation results."""
    run = CronRunResult(cases=list(publish_failures))

    for case, raw_payload, cid in published:
        raw_seen, enriched, _dl = collector.get_by_correlation(cid)
        operation = str((raw_payload.get("source") or {}).get("operation") or case.operation)
        service = str((raw_payload.get("source") or {}).get("service") or case.service)
        routing_key = str(raw_payload.get("routingKey") or case.routing_key or "")

        vr = (validation_by_cid or {}).get(cid)
        if vr is None and enriched and raw_seen:
            template_id = OPERATION_TEMPLATE_MAP.get(operation, "cron-scheduler")
            vr = validate_cron_event_pair(operation, service, enriched, raw_seen)
            vr.template_id = template_id
            run.validation_results.append(vr)

        if not raw_seen:
            run.cases.append(
                CronCaseResult(
                    case_id=case.case_id,
                    routing_key=routing_key,
                    operation=operation,
                    service=service,
                    correlation_id=cid,
                    publish_status="FAIL",
                    enrich_status="NO",
                    validation_status="FAIL",
                    error="Raw event not seen on tap queue after publish",
                    jira_refs=case.jira_refs,
                )
            )
            continue

        if not enriched:
            no_enricher = operation in CRON_NO_ENRICHER_OPERATIONS or not expects_cron_enrichment(
                operation, raw_payload
            )
            deferred = operation in CRON_DEFERRED_ENRICHER_OPERATIONS
            run.cases.append(
                CronCaseResult(
                    case_id=case.case_id,
                    routing_key=routing_key,
                    operation=operation,
                    service=service,
                    correlation_id=cid,
                    publish_status="PASS",
                    enrich_status="NO",
                    validation_status=(
                        "WARN"
                        if no_enricher or deferred
                        else "FAIL"
                    ),
                    error=(
                        "No resolver enricher — enrichment not expected yet"
                        if no_enricher
                        else (
                            "BYOF enricher registered but no enriched event in preprod "
                            "(deploy populate-enrichers-6 / verify BYOF API)"
                            if deferred
                            else "Enriched event not received"
                        )
                    ),
                    jira_refs=case.jira_refs,
                )
            )
            continue

        status = vr.status.value if vr else "PASS"
        err = ""
        if vr:
            err = "; ".join(c.message for c in vr.checks if c.status == ValidationStatus.FAIL)
        run.cases.append(
            CronCaseResult(
                case_id=case.case_id,
                routing_key=routing_key,
                operation=operation,
                service=service,
                correlation_id=cid,
                publish_status="PASS",
                enrich_status="PASS",
                validation_status=status,
                error=err,
                jira_refs=case.jira_refs,
            )
        )
    return run


def write_cron_report(run: CronRunResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": {
            "pass": run.pass_count,
            "fail": run.fail_count,
            "warn": run.warn_count,
            "total": len(run.cases),
        },
        "cases": [
            {
                "case_id": c.case_id,
                "routing_key": c.routing_key,
                "operation": c.operation,
                "service": c.service,
                "correlation_id": c.correlation_id,
                "publish_status": c.publish_status,
                "enrich_status": c.enrich_status,
                "validation_status": c.validation_status,
                "error": c.error,
                "jira_refs": list(c.jira_refs),
            }
            for c in run.cases
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if run.validation_results:
        write_json_report(run.validation_results, path.with_name("cron-validation.json"))


def run_cron_validation(
    *,
    project_root: Path | None = None,
    case_filter: frozenset[str] | None = None,
    settle_sec: float | None = None,
    report_path: Path | None = None,
    purge_before: bool = False,
) -> CronRunResult:
    """Standalone cron run (own RabbitMQ session). Prefer inject during full E2E."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    cfg = load_config(project_root)
    ensure_report_dirs(cfg.project_root)
    settle = settle_sec if settle_sec is not None else float(os.getenv("CRON_SETTLE_SEC", "90"))

    cases = load_cron_cases()
    if case_filter:
        cases = [c for c in cases if c.case_id in case_filter]
    if not cases:
        log.warning("No cron payload cases found")
        return CronRunResult()

    if purge_before:
        from ..rabbitmq.purge import purge_queues

        purge_queues(cfg.rabbitmq, include_enriched=cfg.purge_enriched_queue)

    collector = QueueEventCollector(cfg)
    collector.start(write_files=True)
    time.sleep(1)

    print("\n" + "=" * 72)
    print("  CRON / SCHEDULER RAW-QUEUE VALIDATION")
    print("=" * 72)

    published, publish_failures = inject_cron_payloads(cfg, case_filter=case_filter)
    print(f" - Published {len(published)} cron payload(s); waiting {settle:.0f}s for enrichment…")
    collector.wait_until_settled(settle)
    if cfg.enriched_catchup_sec > 0:
        collector.wait_for_missing_enriched(min(cfg.enriched_catchup_sec, settle))

    run = summarize_cron_results(collector, published, publish_failures)
    collector.stop()

    out = report_path or e2e_cron_results_json(cfg.project_root)
    write_cron_report(run, out)
    if run.validation_results:
        print_report(run.validation_results)
    print(
        f"\nCron summary: {run.pass_count} PASS / {run.warn_count} WARN / {run.fail_count} FAIL "
        f"(of {len(run.cases)} cases) → {out}"
    )
    return run
