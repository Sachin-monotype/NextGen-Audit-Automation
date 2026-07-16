"""Run Ingress API desktop/plugin audit events and validate PP test queues."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeAlias

from ..config import AppConfig, RabbitMQConfig, load_config
from ..models import JsonDict, ValidationResult, ValidationStatus
from ..rabbitmq.collector import QueueEventCollector
from ..report import print_report, write_json_report
from ..report_paths import ensure_report_dirs, temp_path
from .client import IngressClient, load_ingress_client_config
from .config import IngressConfigError, ingress_queue_names, ingress_rabbitmq_url
from .payloads import IngressCase, load_ingress_cases, normalize_ingress_payload
from .validation import expects_ingress_enrichment, validate_ingress_event_pair

log = logging.getLogger(__name__)

PublishedIngress: TypeAlias = tuple[IngressCase, JsonDict, str]


@dataclass
class IngressCaseResult:
    case_id: str
    event_name: str
    category: str
    operation: str
    service: str
    correlation_id: str
    http_status: int
    publish_status: str
    raw_status: str
    enrich_status: str
    validation_status: str
    error: str = ""


@dataclass
class IngressRunResult:
    cases: list[IngressCaseResult] = field(default_factory=list)
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


def ingress_app_config(base: AppConfig | None = None) -> AppConfig:
    """AppConfig tuned for PP ingress test queues and payload/ingress capture dirs."""
    cfg = base or load_config()
    raw_q, enr_q = ingress_queue_names()
    rmq = cfg.rabbitmq
    ingress_rmq = RabbitMQConfig(
        url=ingress_rabbitmq_url(rmq.url),
        raw_queue=raw_q,
        enriched_queue=enr_q,
        dead_letter_queue=rmq.dead_letter_queue,
        raw_exchange=rmq.raw_exchange,
        enriched_exchange=rmq.enriched_exchange,
        dead_letter_exchange=rmq.dead_letter_exchange,
        raw_queue_passive=True,
        enriched_queue_passive=True,
        enriched_use_wildcard_bind=True,
        consume_dead_letter_queue=False,
        enriched_routing_keys=rmq.enriched_routing_keys,
        platform_notification_queue=rmq.platform_notification_queue,
    )
    root = cfg.project_root
    return AppConfig(
        project_root=root,
        rabbitmq=ingress_rmq,
        event_wait_timeout_ms=cfg.event_wait_timeout_ms,
        settle_after_flows_sec=cfg.settle_after_flows_sec,
        enriched_catchup_sec=cfg.enriched_catchup_sec,
        purge_queues_on_e2e=False,
        purge_enriched_queue=False,
        purge_test_queues_on_e2e=False,
        enriched_backlog_drain_sec=0.0,
        backlog_drain_sec=0.0,
        validate_captured_only=False,
        raw_events_dir=root / "payload" / "ingress" / "raw",
        enriched_events_dir=root / "payload" / "ingress" / "enrich",
        dead_letter_events_dir=root / "temp" / "ingress-dlq",
    )


def purge_ingress_queues(cfg: AppConfig | None = None) -> dict[str, int]:
    from ..rabbitmq.purge import purge_queues

    app_cfg = ingress_app_config(cfg)
    return purge_queues(
        app_cfg.rabbitmq,
        include_enriched=True,
        include_dead_letter=False,
        queues=[app_cfg.rabbitmq.raw_queue, app_cfg.rabbitmq.enriched_queue],
    )


def _post_cases(
    client: IngressClient,
    cases: list[IngressCase],
) -> tuple[list[PublishedIngress], list[IngressCaseResult]]:
    published: list[PublishedIngress] = []
    failures: list[IngressCaseResult] = []

    for case in cases:
        try:
            raw = json.loads(case.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("payload must be a JSON object")
            payload = normalize_ingress_payload(raw, case_id=case.case_id)
            cid = str(payload["xCorrelationId"])
            status, body = client.post_event(payload)
            if status >= 400:
                failures.append(
                    IngressCaseResult(
                        case_id=case.case_id,
                        event_name=case.event_name,
                        category=case.category,
                        operation=case.operation,
                        service=case.service,
                        correlation_id=cid,
                        http_status=status,
                        publish_status="FAIL",
                        raw_status="NO",
                        enrich_status="NO",
                        validation_status="FAIL",
                        error=f"HTTP {status}: {body[:200]}",
                    )
                )
                continue
            published.append((case, payload, cid))
            try:
                from ..generation_tracker import record_generation

                actor = payload.get("actor") if isinstance(payload.get("actor"), dict) else {}
                record_generation(
                    case.operation or case.case_id,
                    cid,
                    kind="ingress",
                    meta={
                        "case_id": case.case_id,
                        "eventId": payload.get("eventId"),
                        "profile_id": actor.get("globalUserId"),
                        "customer_id": actor.get("globalCustomerId"),
                        "event_name": case.event_name,
                    },
                )
            except Exception:
                pass
            time.sleep(0.4)
        except Exception as exc:
            log.exception("Ingress case failed: %s", case.case_id)
            failures.append(
                IngressCaseResult(
                    case_id=case.case_id,
                    event_name=case.event_name,
                    category=case.category,
                    operation=case.operation,
                    service=case.service,
                    correlation_id="",
                    http_status=0,
                    publish_status="FAIL",
                    raw_status="NO",
                    enrich_status="NO",
                    validation_status="FAIL",
                    error=str(exc),
                )
            )
    return published, failures


def summarize_ingress_results(
    published: list[PublishedIngress],
    publish_failures: list[IngressCaseResult],
    collector: QueueEventCollector,
) -> IngressRunResult:
    run = IngressRunResult(cases=list(publish_failures))

    for case, raw_payload, cid in published:
        raw_seen, enriched, _dl = collector.get_by_correlation(cid)
        operation = str((raw_payload.get("source") or {}).get("operation") or case.operation)
        service = str((raw_payload.get("source") or {}).get("service") or case.service)

        if not raw_seen:
            run.cases.append(
                IngressCaseResult(
                    case_id=case.case_id,
                    event_name=case.event_name,
                    category=case.category,
                    operation=operation,
                    service=service,
                    correlation_id=cid,
                    http_status=200,
                    publish_status="PASS",
                    raw_status="NO",
                    enrich_status="NO",
                    validation_status="FAIL",
                    error=(
                        "Raw event not seen on ingress raw queue "
                        "(Ingress API accepted but queue tap missed event)"
                    ),
                )
            )
            continue

        if not enriched:
            optional = not expects_ingress_enrichment(operation)
            run.cases.append(
                IngressCaseResult(
                    case_id=case.case_id,
                    event_name=case.event_name,
                    category=case.category,
                    operation=operation,
                    service=service,
                    correlation_id=cid,
                    http_status=200,
                    publish_status="PASS",
                    raw_status="PASS",
                    enrich_status="NO",
                    validation_status="WARN" if optional else "FAIL",
                    error=(
                        "Enriched event optional for desktop/plugin ingress sample"
                        if optional
                        else "Enriched event not received on ingress test queue"
                    ),
                )
            )
            continue

        vr = validate_ingress_event_pair(operation, service, enriched, raw_seen)
        run.validation_results.append(vr)
        status = vr.status.value
        err = "; ".join(c.message for c in vr.checks if c.status == ValidationStatus.FAIL)
        run.cases.append(
            IngressCaseResult(
                case_id=case.case_id,
                event_name=case.event_name,
                category=case.category,
                operation=operation,
                service=service,
                correlation_id=cid,
                http_status=200,
                publish_status="PASS",
                raw_status="PASS",
                enrich_status="PASS",
                validation_status=status,
                error=err,
            )
        )
    return run


def write_ingress_report(run: IngressRunResult, path: Path) -> None:
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
                "event_name": c.event_name,
                "category": c.category,
                "operation": c.operation,
                "service": c.service,
                "correlation_id": c.correlation_id,
                "http_status": c.http_status,
                "publish_status": c.publish_status,
                "raw_status": c.raw_status,
                "enrich_status": c.enrich_status,
                "validation_status": c.validation_status,
                "error": c.error,
            }
            for c in run.cases
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if run.validation_results:
        write_json_report(run.validation_results, path.with_name("ingress-validation.json"))


def run_ingress_validation(
    *,
    project_root: Path | None = None,
    category_filter: frozenset[str] | None = None,
    case_filter: frozenset[str] | None = None,
    settle_sec: float | None = None,
    report_path: Path | None = None,
    purge_before: bool = False,
) -> IngressRunResult:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    base_cfg = load_config(project_root)
    cfg = ingress_app_config(base_cfg)
    ensure_report_dirs(cfg.project_root)
    settle = settle_sec if settle_sec is not None else float(os.getenv("INGRESS_SETTLE_SEC", "45"))

    try:
        ingress_queue_names()
    except IngressConfigError as exc:
        log.error("%s", exc)
        return IngressRunResult()

    client_cfg = load_ingress_client_config()
    if not client_cfg.ready:
        log.error("Ingress bearer token missing — set INGRESS_BEARER_TOKEN or BEARER_TOKEN_PP")
        return IngressRunResult()

    cases = load_ingress_cases(category_filter=category_filter)
    if case_filter:
        cases = [c for c in cases if c.case_id in case_filter]
    if not cases:
        log.warning("No ingress payload cases found under data/ingress_payloads/")
        return IngressRunResult()

    if purge_before:
        purged = purge_ingress_queues(base_cfg)
        print(f"Purged ingress test queues: {purged}")

    collector = QueueEventCollector(cfg)
    collector.start(write_files=True)
    time.sleep(1)

    print("\n" + "=" * 72)
    print("  INGRESS API — DESKTOP / PLUGIN AUDIT EVENTS")
    print("=" * 72)
    print(f" API: {client_cfg.base_url}")
    print(f" Raw queue: `{cfg.rabbitmq.raw_queue}`")
    print(f" Enriched queue: `{cfg.rabbitmq.enriched_queue}`")
    print(f" Cases: {len(cases)}")

    client = IngressClient(client_cfg)
    published, publish_failures = _post_cases(client, cases)
    print(f" - Posted {len(published)} event(s); waiting {settle:.0f}s for queue capture…")
    collector.wait_until_settled(settle)
    if cfg.enriched_catchup_sec > 0:
        collector.wait_for_missing_enriched(min(cfg.enriched_catchup_sec, settle))

    run = summarize_ingress_results(published, publish_failures, collector)
    collector.stop()

    out = report_path or temp_path(cfg.project_root, "ingress-results.json")
    write_ingress_report(run, out)
    if run.validation_results:
        print_report(run.validation_results)
    print(
        f"\nIngress summary: {run.pass_count} PASS / {run.warn_count} WARN / {run.fail_count} FAIL "
        f"(of {len(run.cases)} cases) → {out}"
    )
    return run
