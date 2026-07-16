"""
Full E2E pipeline:
  1. Start RabbitMQ consumer (raw + enriched + dead-letter queues)
  2. Run Python GraphQL simulation (70+ operations via simulation/runner.py)
  3. Wait for enriched events to settle
  4. Pair raw ↔ enriched by xCorrelationId and validate (3 layers)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from ..coverage.matrix import CoverageMatrix, build_coverage_matrix
from ..coverage.report import print_coverage_report, write_coverage_json
from ..config import AppConfig, load_config
from ..event_retention import prepare_payload_capture, prune_captured_events
from ..models import JsonDict, ValidationResult, ValidationStatus
from ..operation_catalog import expects_enriched_event
from ..rabbitmq.collector import QueueEventCollector
from ..rabbitmq.resolver_routing_map import expected_routing_key, routing_key_matches
from ..report import print_raw_enriched_report, print_report, write_json_report
from ..report_paths import (
    backlog_coverage_json,
    backlog_results_csv,
    backlog_validation_json,
    cron_results_json,
    e2e_compare_json,
    e2e_coverage_json,
    e2e_cron_results_json,
    e2e_flows_json,
    e2e_results_csv,
    e2e_validation_json,
    ensure_report_dirs,
    flows_results_json,
    result_xlsx,
    results_csv,
    validation_json,
)
from ..operation_registry import e2e_expected_operations
from ..template_registry import OPERATION_TEMPLATE_MAP
from ..coverage.correlation_selection import reconcile_correlation_pairs
from ..validator import validate_event_pair

log = logging.getLogger(__name__)


def _console_step(message: str) -> None:
    """Visible pipeline milestones (also captured in reports/e2e/latest-run.log)."""
    print(f" - {message}", flush=True)


def _print_run_banner(mode: str) -> None:
    print("\n" + "=" * 72, flush=True)
    print(f"  E2E PIPELINE ({mode})", flush=True)
    print("=" * 72, flush=True)


def _summarize_flows(project_root) -> tuple[int, int, int, int]:
    """Return (flow_count, operation_count, pass_count, fail_count)."""
    path = e2e_flows_json(project_root)
    if not path.is_file():
        return 0, 0, 0, 0
    data = json.loads(path.read_text(encoding="utf-8"))
    flows = data.get("flows") or []
    n_ops = n_pass = n_fail = 0
    for flow in flows:
        for row in flow.get("results") or []:
            n_ops += 1
            if row.get("status") == "PASS":
                n_pass += 1
            elif row.get("status") == "FAIL":
                n_fail += 1
    return len(flows), n_ops, n_pass, n_fail


@dataclass
class E2EResult:
    validation_results: list[ValidationResult]
    raw_received: int
    enriched_received: int
    enriched_routing_keys_received: int
    enriched_routing_keys_expected: int
    missing_routing_keys: list[str]
    dead_letter_received: int
    flows_exit_code: int
    missing_operations: list[str]
    timeout_operations: list[str]
    query_ops_raw_only: list[str]
    coverage_matrix: CoverageMatrix | None = None
    queue_depths_start: dict[str, int] | None = None
    queue_depths_end: dict[str, int] | None = None
    mode: str = "e2e"
    cron_published: list | None = None
    cron_publish_failures: list | None = None


def run_flows_simulation(
    project_root,
    *,
    skip: bool = False,
    flow_filter: frozenset[str] | None = None,
) -> int:
    if skip:
        log.info("Skipping flows simulation")
        return 0

    flows_results = e2e_flows_json(project_root)
    from ..simulation.runner import run_flows_simulation as run_python_flows

    log.info("Starting Python GraphQL simulation")
    return run_python_flows(project_root, results_path=flows_results, flow_filter=flow_filter)


def _operation_from_raw(raw: JsonDict) -> tuple[str, str]:
    source = raw.get("source") or {}
    return (
        str(source.get("operation") or "unknown"),
        str(source.get("service") or "mtconnect-api"),
    )


def _warn_if_wrong_vhost(cfg: AppConfig) -> None:
    """Warn when tap queues have traffic on a sibling vhost (PP: mt-connect-preprod vs mt-connect)."""
    from urllib.parse import quote, urlparse, urlunparse

    import pika

    parsed = urlparse(cfg.rabbitmq.url)
    current_vhost = parsed.path.strip("/") or "/"
    siblings = {
        "mt-connect-preprod": "mt-connect",
        "mt-connect": "mt-connect-preprod",
        "%2F": "mt-connect",
    }
    alt_vhost = siblings.get(current_vhost)
    if not alt_vhost:
        return

    alt_url = urlunparse(parsed._replace(path=f"/{quote(alt_vhost, safe='')}"))

    try:
        conn = pika.BlockingConnection(pika.URLParameters(cfg.rabbitmq.url))
        ch = conn.channel()
        cur_raw = ch.queue_declare(queue=cfg.rabbitmq.raw_queue, passive=True).method.message_count
        cur_enr = ch.queue_declare(queue=cfg.rabbitmq.enriched_queue, passive=True).method.message_count
        conn.close()

        conn2 = pika.BlockingConnection(pika.URLParameters(alt_url))
        ch2 = conn2.channel()
        try:
            alt_raw = ch2.queue_declare(queue=cfg.rabbitmq.raw_queue, passive=True).method.message_count
            alt_enr = ch2.queue_declare(queue=cfg.rabbitmq.enriched_queue, passive=True).method.message_count
        except Exception:
            alt_raw = alt_enr = -1
        conn2.close()

        if (cur_raw == 0 and cur_enr == 0) and (alt_raw > 0 or alt_enr > 0):
            log.warning(
                "Tap queues are empty on vhost `%s` but %s has raw=%s enriched=%s — "
                "set RABBITMQ_URL vhost to %s",
                current_vhost,
                alt_vhost,
                alt_raw,
                alt_enr,
                alt_vhost,
            )
    except Exception as exc:
        log.debug("Vhost mismatch check skipped: %s", exc)


def _validate_correlation_pairs(
    cfg: AppConfig,
    collector: QueueEventCollector,
    correlation_pairs: dict[str, tuple[JsonDict | None, JsonDict | None]],
    *,
    validate_captured_only: bool,
    timeout_label: str,
) -> tuple[
    list[ValidationResult],
    list[str],
    list[str],
    list[str],
    set[str],
]:
    results: list[ValidationResult] = []
    timeout_ops: list[str] = []
    query_ops_raw_only: list[str] = []
    validated_correlation_ids: set[str] = set()

    for cid in sorted(correlation_pairs.keys()):
        raw_payload, enriched_payload = correlation_pairs[cid]
        if not raw_payload:
            continue

        operation, service = _operation_from_raw(raw_payload)
        template_id = OPERATION_TEMPLATE_MAP.get(operation, "unknown")
        validated_correlation_ids.add(cid)

        _, _, dl_payload = collector.get_by_correlation(cid)

        if dl_payload and not enriched_payload:
            r = ValidationResult(
                operation=operation,
                service=service,
                template_id=template_id,
                status=ValidationStatus.WARN,
            )
            r.add(
                "pipeline",
                "dead_letter",
                ValidationStatus.WARN,
                f"Event dead-lettered (correlation={cid})",
            )
            results.append(r)
            continue

        if not expects_enriched_event(operation) and not enriched_payload:
            query_ops_raw_only.append(operation)
            r = ValidationResult(
                operation=operation,
                service=service,
                template_id=template_id,
                status=ValidationStatus.PASS,
            )
            r.add(
                "pipeline",
                "query_no_enrichment",
                ValidationStatus.PASS,
                "Query/read op — raw audit only, enrichment not expected",
            )
            results.append(r)
            continue

        if not enriched_payload:
            timeout_ops.append(operation)
            r = ValidationResult(
                operation=operation,
                service=service,
                template_id=template_id,
                status=ValidationStatus.FAIL,
            )
            r.add(
                "pipeline",
                "enriched_timeout",
                ValidationStatus.FAIL,
                f"Raw received but enriched not received ({timeout_label}, correlation={cid})",
            )
            results.append(r)
            continue

        expected_rk = expected_routing_key(operation)
        enr_cid = (
            str(enriched_payload.get("xCorrelationId"))
            if enriched_payload and enriched_payload.get("xCorrelationId")
            else cid
        )
        actual_rk = collector.routing_key_for_correlation(enr_cid) or collector.routing_key_for_correlation(cid)
        if expected_rk and actual_rk and not routing_key_matches(operation, actual_rk):
            r = validate_event_pair(
                operation,
                service,
                enriched_payload,
                raw_payload,
                structure_only=False,
            )
            r.add(
                "pipeline",
                "routing_key_mismatch",
                ValidationStatus.WARN,
                f"Expected routing key `{expected_rk}`, got `{actual_rk}`",
            )
            results.append(r)
            continue

        results.append(
            validate_event_pair(
                operation,
                service,
                enriched_payload,
                raw_payload,
                structure_only=False,
            )
        )

    orphan_ops_seen: set[str] = set()
    for cid, (raw_payload, enriched_payload) in correlation_pairs.items():
        if raw_payload or not enriched_payload or cid in validated_correlation_ids:
            continue
        source = enriched_payload.get("source") or {}
        operation = str(source.get("operation") or "unknown")
        if operation in orphan_ops_seen:
            continue
        orphan_ops_seen.add(operation)
        service = str(source.get("service") or "unknown")
        r = validate_event_pair(
            operation,
            service,
            enriched_payload,
            None,
            structure_only=True,
        )
        r.add(
            "pipeline",
            "missing_raw",
            ValidationStatus.WARN,
            f"Enriched without matching raw (correlation={cid})",
        )
        results.append(r)

    captured_ops = {
        _operation_from_raw(raw)[0]
        for raw, _ in correlation_pairs.values()
        if raw is not None
    }
    missing: list[str] = []
    if not validate_captured_only:
        expected_ops = set(e2e_expected_operations())
        missing = sorted(expected_ops - captured_ops)
        for op in missing:
            r = ValidationResult(
                operation=op,
                service="mtconnect-api",
                template_id=OPERATION_TEMPLATE_MAP.get(op, "unknown"),
                status=ValidationStatus.SKIP,
            )
            r.add(
                "pipeline",
                "not_simulated",
                ValidationStatus.SKIP,
                "Operation not triggered in this flows run (skipped/env/prerequisite)",
            )
            results.append(r)

    missing_rk: list[str] = []
    if not cfg.rabbitmq.wildcard_bind_mode:
        missing_rk = collector.missing_routing_keys()
        for rk in missing_rk:
            r = ValidationResult(
                operation=rk,
                service="routing-key",
                template_id="routing-key-coverage",
                status=ValidationStatus.SKIP,
            )
            r.add(
                "pipeline",
                "routing_key_not_received",
                ValidationStatus.SKIP,
                f"Resolver routing key `{rk}` not received in this run",
            )
            results.append(r)

    return results, timeout_ops, query_ops_raw_only, missing_rk, validated_correlation_ids


def _supplement_missing_enriched(cfg: AppConfig, collector: QueueEventCollector) -> int:
    from ..operation_catalog import expects_enriched_event
    from ..rabbitmq.queue_peek import supplement_collector_from_queue

    missing_cids = collector.missing_enriched_correlation_ids()
    if not missing_cids:
        return 0
    missing_ops = {
        _operation_from_raw(collector.get_by_correlation(cid)[0] or {})[0]
        for cid in missing_cids
    }
    missing_ops = {
        op for op in missing_ops if op != "unknown" and expects_enriched_event(op)
    }
    return supplement_collector_from_queue(
        collector,
        missing_correlation_ids=missing_cids,
        missing_operations=missing_ops,
    )


def collect_and_validate(
    config: AppConfig | None = None,
    *,
    purge_before: bool | None = None,
    purge_after: bool | None = None,
    skip_flows: bool = False,
    backlog_only: bool = False,
    flow_filter: frozenset[str] | None = None,
    cron_case_filter: frozenset[str] | None = None,
    include_cron: bool | None = None,
    settle_operations: frozenset[str] | None = None,
) -> E2EResult:
    import os

    cfg = config or load_config()
    if include_cron is None:
        include_cron = os.getenv("SKIP_CRON_INJECTION", "").lower() not in {
            "1",
            "true",
            "yes",
        }
    ensure_report_dirs(cfg.project_root)
    if not backlog_only:
        prepare_payload_capture(cfg.project_root)
        _console_step("Reset payload/raw and payload/enrich for fresh capture")
    mode = "backlog" if backlog_only else "e2e"
    validate_captured_only = cfg.validate_captured_only or backlog_only

    _print_run_banner(mode)
    _console_step(f"RabbitMQ vhost URL configured (raw=`{cfg.rabbitmq.raw_queue}` enriched=`{cfg.rabbitmq.enriched_queue}`)")

    if backlog_only:
        do_purge_before = False
        do_purge_after = False
        do_purge_test = False
    else:
        do_purge_before = (
            purge_before if purge_before is not None else cfg.purge_queues_on_e2e
        )
        do_purge_after = purge_after if purge_after is not None else cfg.purge_queues_on_e2e
        do_purge_test = cfg.purge_test_queues_on_e2e

    if do_purge_test:
        from ..rabbitmq.purge import purge_test_queues

        log.info(
            "Purging test tap queues before E2E: raw=`%s` enriched=`%s`",
            cfg.rabbitmq.raw_queue,
            cfg.rabbitmq.enriched_queue,
        )
        purged = purge_test_queues(cfg.rabbitmq)
        log.info("Test queue purge: %s", purged)
        _console_step(f"Purged test tap queues before run: {purged}")

    _warn_if_wrong_vhost(cfg)

    if do_purge_before:
        from ..rabbitmq.purge import purge_queues

        log.info(
            "Purging RabbitMQ queues before E2E run (enriched=%s)",
            cfg.purge_enriched_queue,
        )
        purge_queues(cfg.rabbitmq, include_enriched=cfg.purge_enriched_queue)
        _console_step(
            f"Purged automation queues before run (enriched={'yes' if cfg.purge_enriched_queue else 'no'})"
        )
    else:
        _console_step("Queue purge before run: skipped (--no-purge or PURGE_QUEUES_ON_E2E=false)")

    if not backlog_only:
        from ..cleanup import run_pre_run_cleanup

        pre_cleanup = run_pre_run_cleanup(cfg.project_root)
        if pre_cleanup.notifications_deleted:
            log.info(
                "Pre-run notification cleanup: deleted %d row(s)",
                pre_cleanup.notifications_deleted,
            )
            _console_step(
                f"Notification DB cleanup (pre-run): deleted {pre_cleanup.notifications_deleted} row(s)"
            )
        else:
            _console_step(
                "Notification DB cleanup: skipped — rows kept for DB/UI verification "
                "(set CLEANUP_NOTIFICATIONS_BEFORE_E2E=true to wipe before run)"
            )
        for err in pre_cleanup.errors or []:
            log.warning("Pre-run cleanup: %s", err)
    else:
        log.info("Backlog mode — skipping simulation and pre-run cleanup")

    from ..rabbitmq.queue_stats import get_queue_depths

    queue_depths_start: dict[str, int] = {}
    try:
        queue_depths_start = get_queue_depths(cfg.rabbitmq)
        log.info("Queue depths at start (%s): %s", mode, queue_depths_start)
        raw_d = queue_depths_start.get(cfg.rabbitmq.raw_queue, -1)
        enr_d = queue_depths_start.get(cfg.rabbitmq.enriched_queue, -1)
        _console_step(
            f"Queue depths at start — raw `{cfg.rabbitmq.raw_queue}`: {raw_d}, "
            f"enriched `{cfg.rabbitmq.enriched_queue}`: {enr_d}"
        )
    except Exception as exc:
        log.warning("Could not read queue depths at start: %s", exc)

    collector = QueueEventCollector(cfg)

    log.info("Connecting to RabbitMQ at %s", cfg.rabbitmq.url)
    collector.start(write_files=True)
    time.sleep(1)
    dlq_note = (
        f", DLQ `{cfg.rabbitmq.dead_letter_queue}`"
        if cfg.rabbitmq.consume_dead_letter_queue
        else ""
    )
    _console_step(
        f"RabbitMQ consumer started — listening on raw `{cfg.rabbitmq.raw_queue}` "
        f"and enriched `{cfg.rabbitmq.enriched_queue}`{dlq_note}"
    )

    flows_exit = 0
    cron_published: list = []
    cron_publish_failures: list = []
    if backlog_only:
        log.info(
            "Draining existing queue backlog for up to %.0fs (no simulation)",
            cfg.backlog_drain_sec,
        )
        collector.wait_until_queues_empty(cfg.backlog_drain_sec)
        log.info(
            "Backlog drain complete: raw=%d enriched_correlations=%d routing_keys=%d",
            collector.raw_correlation_count,
            collector.enriched_correlation_count,
            collector.enriched_count,
        )
        _console_step(
            f"Backlog drain complete — captured raw correlations: {collector.raw_correlation_count}, "
            f"enriched correlations: {collector.enriched_correlation_count}"
        )
        injected = _supplement_missing_enriched(cfg, collector)
        if injected:
            _console_step(
                f"Queue peek supplement — injected {injected} enriched event(s) from backlog"
            )
    else:
        if (
            cfg.enriched_backlog_drain_sec > 0
            and not cfg.purge_enriched_queue
            and not do_purge_test
            and not skip_flows
        ):
            log.info(
                "Draining existing enriched backlog for up to %.0fs before flows",
                cfg.enriched_backlog_drain_sec,
            )
            collector.wait_until_settled(
                cfg.enriched_backlog_drain_sec,
                min_elapsed_sec=min(30.0, cfg.enriched_backlog_drain_sec * 0.5),
            )
            log.info(
                "Backlog drain complete: raw=%d enriched_correlations=%d routing_keys=%d",
                collector.raw_correlation_count,
                collector.enriched_correlation_count,
                collector.enriched_count,
            )
            _console_step(
                f"Pre-flow enriched backlog drain — raw: {collector.raw_correlation_count}, "
                f"enriched: {collector.enriched_correlation_count}"
            )

        collector.clear_capture()
        _console_step("Cleared pre-flow queue captures — validating fresh GQL run only")

        _console_step("GQL simulation starting (24 flows via NextGen /graph + API /graphql)")
        flows_exit = run_flows_simulation(
            cfg.project_root,
            skip=skip_flows,
            flow_filter=flow_filter,
        )
        n_flows, n_ops, n_pass, n_fail = _summarize_flows(cfg.project_root)
        _console_step(
            f"GQL simulation finished — {n_ops} operations across {n_flows} flows "
            f"(PASS={n_pass} FAIL={n_fail}, exit={flows_exit})"
        )

        cron_published = []
        cron_publish_failures = []
        if include_cron:
            from ..cron.runner import inject_cron_payloads

            cron_published, cron_publish_failures = inject_cron_payloads(
                cfg, case_filter=cron_case_filter
            )
            _console_step(
                f"Cron injection — published {len(cron_published)} scheduler payload(s) "
                f"to raw queue ({len(cron_publish_failures)} publish error(s))"
            )

        log.info(
            "Flows finished (exit=%d). Waiting up to %.0fs for enriched events…",
            flows_exit,
            cfg.settle_after_flows_sec,
        )
        if settle_operations:
            # Targeted run: stop as soon as the *selected* operations have pairs,
            # rather than waiting for the whole (shared) queue to go idle.
            _console_step(
                f"Waiting up to {cfg.settle_after_flows_sec:.0f}s for the "
                f"{len(settle_operations)} selected operation(s) to enrich…"
            )
            still_missing = collector.wait_for_operations(
                settle_operations, cfg.settle_after_flows_sec
            )
            if still_missing:
                log.warning(
                    "%d selected operation(s) still without enriched after %.0fs: %s",
                    len(still_missing),
                    cfg.settle_after_flows_sec,
                    ", ".join(sorted(still_missing)),
                )
        else:
            _console_step(
                f"Waiting up to {cfg.settle_after_flows_sec:.0f}s for resolver enriched events to settle…"
            )
            collector.wait_until_settled(cfg.settle_after_flows_sec)
            if cfg.enriched_catchup_sec > 0:
                remaining = collector.wait_for_missing_enriched(cfg.enriched_catchup_sec)
                if remaining:
                    log.warning(
                        "%d raw correlation(s) still without enriched after catch-up (%.0fs)",
                        remaining,
                        cfg.enriched_catchup_sec,
                    )
                else:
                    log.info("All raw correlations have matching enriched events")

        injected = _supplement_missing_enriched(cfg, collector)
        if injected:
            _console_step(
                f"Queue peek supplement — injected {injected} enriched event(s) "
                f"still on `{cfg.rabbitmq.enriched_queue}`"
            )

        _console_step(
            f"Settle complete — raw correlations: {collector.raw_correlation_count}, "
            f"enriched correlations: {collector.enriched_correlation_count}, "
            f"distinct enriched routing keys: {collector.enriched_count}"
        )

    correlation_pairs = reconcile_correlation_pairs(
        dict(collector._raw_by_correlation),
        dict(collector._enriched_by_correlation),
    )
    timeout_label = (
        "backlog drain"
        if backlog_only
        else f"within {cfg.settle_after_flows_sec}s"
    )
    results, timeout_ops, query_ops_raw_only, missing_rk, _ = _validate_correlation_pairs(
        cfg,
        collector,
        correlation_pairs,
        validate_captured_only=validate_captured_only,
        timeout_label=timeout_label,
    )
    paired = sum(
        1
        for r in results
        if r.status in {ValidationStatus.PASS, ValidationStatus.FAIL, ValidationStatus.WARN}
        and r.template_id != "routing-key-coverage"
    )
    _console_step(
        f"Validation complete — {paired} correlation pair(s), "
        f"{len(timeout_ops)} enriched timeout(s), {len(query_ops_raw_only)} query-only raw"
    )

    missing = sorted(
        {
            r.operation
            for r in results
            if any(c.check == "not_simulated" for c in r.checks)
        }
    )

    coverage = build_coverage_matrix(
        collector, results, captured_only=validate_captured_only
    )
    collector.stop()

    queue_depths_end: dict[str, int] = {}
    try:
        queue_depths_end = get_queue_depths(cfg.rabbitmq)
        log.info("Queue depths at end (%s): %s", mode, queue_depths_end)
    except Exception as exc:
        log.warning("Could not read queue depths at end: %s", exc)

    if do_purge_after:
        from ..rabbitmq.purge import purge_queues

        log.info("Purging RabbitMQ queues after run (enriched=%s)", cfg.purge_enriched_queue)
        purge_queues(cfg.rabbitmq, include_enriched=cfg.purge_enriched_queue)

    if not backlog_only:
        from ..cleanup import run_post_run_cleanup

        cleanup = run_post_run_cleanup(cfg.project_root)
        if cleanup.assets_deleted or cleanup.projects_deleted:
            log.info(
                "Post-run cleanup: assets=%d projects=%d",
                cleanup.assets_deleted,
                cleanup.projects_deleted,
            )
            _console_step(
                f"Post-run cleanup — removed automation assets={cleanup.assets_deleted}, "
                f"projects={cleanup.projects_deleted}"
            )
        if cleanup.roles_deleted or cleanup.teams_deleted:
            log.info(
                "Post-run cleanup: roles=%d teams=%d (notifications kept for DB/UI check)",
                cleanup.roles_deleted,
                cleanup.teams_deleted,
            )
            _console_step(
                f"Post-run cleanup — removed automation roles={cleanup.roles_deleted}, "
                f"teams={cleanup.teams_deleted}; notifications NOT deleted"
            )
        if not (
            cleanup.assets_deleted
            or cleanup.projects_deleted
            or cleanup.roles_deleted
            or cleanup.teams_deleted
        ):
            _console_step("Post-run cleanup — no automation test data removed")
        for err in cleanup.errors or []:
            log.warning("Cleanup: %s", err)

    if not backlog_only:
        prune_captured_events(cfg.project_root)
        _console_step("Canonicalized payload/raw and payload/enrich (one JSON per operation)")

    if cron_published or cron_publish_failures:
        from ..cron.runner import summarize_cron_results, write_cron_report
        from ..report_paths import e2e_cron_results_json

        cron_run = summarize_cron_results(
            collector,
            cron_published,
            cron_publish_failures,
        )
        cron_path = e2e_cron_results_json(cfg.project_root)
        write_cron_report(cron_run, cron_path)
        _console_step(
            f"Cron validation — {cron_run.pass_count} PASS / {cron_run.fail_count} FAIL "
            f"→ {cron_path}"
        )

    return E2EResult(
        validation_results=results,
        raw_received=collector.raw_correlation_count,
        enriched_received=collector.enriched_correlation_count,
        enriched_routing_keys_received=collector.enriched_count,
        enriched_routing_keys_expected=len(cfg.rabbitmq.enriched_routing_keys),
        missing_routing_keys=missing_rk,
        dead_letter_received=collector.dead_letter_count,
        flows_exit_code=flows_exit,
        missing_operations=missing,
        timeout_operations=timeout_ops,
        query_ops_raw_only=sorted(set(query_ops_raw_only)),
        coverage_matrix=coverage,
        queue_depths_start=queue_depths_start or None,
        queue_depths_end=queue_depths_end or None,
        mode=mode,
        cron_published=cron_published or None,
        cron_publish_failures=cron_publish_failures or None,
    )


def _print_pipeline_summary(cfg: AppConfig, e2e: E2EResult) -> None:
    title = "BACKLOG VALIDATION SUMMARY" if e2e.mode == "backlog" else "E2E PIPELINE SUMMARY"
    print("\n" + "=" * 72)
    print(f"  {title}")
    print("=" * 72)
    if e2e.mode == "backlog":
        print("  Mode                          : backlog only (no simulation)")
    if cfg.rabbitmq.wildcard_bind_mode:
        print("  Queue bind mode               : wildcard # (correlation validation)")
    print(f"  Raw events (by correlation)     : {e2e.raw_received}")
    print(f"  Enriched (by correlation)       : {e2e.enriched_received}")
    paired = len(
        [
            r
            for r in e2e.validation_results
            if r.status in {ValidationStatus.PASS, ValidationStatus.FAIL, ValidationStatus.WARN}
            and r.template_id != "routing-key-coverage"
        ]
    )
    print(f"  Correlation pairs validated     : {paired}")
    print(f"  Raw without enriched (timeout)  : {len(e2e.timeout_operations)}")
    if not cfg.rabbitmq.wildcard_bind_mode:
        print(
            f"  Enriched routing keys received  : {e2e.enriched_routing_keys_received} / "
            f"{e2e.enriched_routing_keys_expected}"
        )
        print(f"  Missing resolver routing keys   : {len(e2e.missing_routing_keys)}")
    else:
        print(
            f"  Distinct enriched routing keys  : {e2e.enriched_routing_keys_received} "
            "(informational — not used for pass/fail)"
        )
    if cfg.rabbitmq.consume_dead_letter_queue:
        print(f"  Dead letters                    : {e2e.dead_letter_received}")
    print(f"  Query ops (raw only, expected)  : {len(e2e.query_ops_raw_only)}")
    if e2e.mode != "backlog":
        print(f"  Flows exit code                 : {e2e.flows_exit_code}")
    captured_only = cfg.validate_captured_only or e2e.mode == "backlog"
    if not captured_only:
        print(f"  Not simulated (skipped)         : {len(e2e.missing_operations)}")
    if e2e.queue_depths_start:
        print("  ── RabbitMQ queue depths (messages ready) ──")
        print("  At start:")
        for q, n in sorted(e2e.queue_depths_start.items()):
            print(f"    {q}: {n}")
    if e2e.queue_depths_end:
        print("  At end:")
        for q, n in sorted(e2e.queue_depths_end.items()):
            print(f"    {q}: {n}")
        start = e2e.queue_depths_start or {}
        end = e2e.queue_depths_end
        raw_q = cfg.rabbitmq.raw_queue
        if raw_q in start and raw_q in end and start[raw_q] >= 0 and end[raw_q] >= 0:
            delta = end[raw_q] - start[raw_q]
            print(
                f"  Note: correlation counts above ≠ queue depth. "
                f"Raw queue delta this run: {delta:+d} "
                f"(resolver DLQ holds failed enrichments)."
            )
    print("=" * 72)


def _write_e2e_reports(
    cfg: AppConfig,
    e2e: E2EResult,
    *,
    report_path: str | None,
    coverage_path: str | None,
    csv_path: str | None,
    xlsx_path: str | None,
) -> None:
    captured_only = cfg.validate_captured_only or e2e.mode == "backlog"
    status_ops = None
    if e2e.mode == "backlog":
        status_ops = sorted(
            {
                r.operation
                for r in e2e.validation_results
                if r.service != "routing-key" and r.template_id != "routing-key-coverage"
            }
        )

    print_raw_enriched_report(e2e.validation_results)

    if e2e.coverage_matrix:
        print_coverage_report(
            e2e.coverage_matrix,
            skip_routing_key_coverage=cfg.rabbitmq.wildcard_bind_mode,
            validate_captured_only=captured_only,
        )

    if report_path is None:
        report_path = str(
            backlog_validation_json(cfg.project_root)
            if e2e.mode == "backlog"
            else e2e_validation_json(cfg.project_root)
        )
    if coverage_path is None and e2e.coverage_matrix:
        coverage_path = str(
            backlog_coverage_json(cfg.project_root)
            if e2e.mode == "backlog"
            else e2e_coverage_json(cfg.project_root)
        )
    if csv_path is None:
        csv_path = str(
            backlog_results_csv(cfg.project_root)
            if e2e.mode == "backlog"
            else e2e_results_csv(cfg.project_root)
        )

    if report_path:
        from pathlib import Path

        write_json_report(e2e.validation_results, Path(report_path))

    if coverage_path and e2e.coverage_matrix:
        from pathlib import Path

        write_coverage_json(e2e.coverage_matrix, Path(coverage_path))
    elif e2e.coverage_matrix and report_path:
        from pathlib import Path

        cov = Path(report_path).with_name("coverage-matrix.json")
        write_coverage_json(e2e.coverage_matrix, cov)

    if csv_path:
        from pathlib import Path

        from ..csv_report import write_e2e_csv

        flows_json = e2e_flows_json(cfg.project_root)
        write_e2e_csv(
            path=Path(csv_path),
            validation_results=e2e.validation_results,
            coverage=e2e.coverage_matrix,
            flows_results_path=flows_json if flows_json.is_file() else None,
        )
        print(f"CSV report written to {csv_path}")

    from pathlib import Path

    from ..csv_report import write_e2e_workbook

    flows_json = e2e_flows_json(cfg.project_root)
    cron_json = e2e_cron_results_json(cfg.project_root)

    if xlsx_path:
        write_e2e_workbook(
            path=Path(xlsx_path),
            validation_results=e2e.validation_results,
            coverage=e2e.coverage_matrix,
            flows_results_path=flows_json if flows_json.is_file() else None,
            cron_results_path=cron_json if cron_json.is_file() else None,
            project_root=cfg.project_root,
        )
        print(f"Excel workbook written to {xlsx_path}")


def run_backlog_validation(
    *,
    report_path: str | None = None,
    coverage_path: str | None = None,
    csv_path: str | None = None,
    project_root=None,
    drain_sec: float | None = None,
) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    cfg = load_config(project_root)
    if drain_sec is not None:
        from dataclasses import replace

        cfg = replace(cfg, backlog_drain_sec=drain_sec)
    ensure_report_dirs(cfg.project_root)
    e2e = collect_and_validate(cfg, backlog_only=True)

    _print_pipeline_summary(cfg, e2e)
    _write_e2e_reports(
        cfg,
        e2e,
        report_path=report_path,
        coverage_path=coverage_path,
        csv_path=csv_path,
        xlsx_path=None,
    )

    any_fail = any(r.status == ValidationStatus.FAIL for r in e2e.validation_results)
    return 1 if any_fail else 0


def run_e2e(
    *,
    report_path: str | None = None,
    coverage_path: str | None = None,
    csv_path: str | None = None,
    xlsx_path: str | None = None,
    project_root=None,
    purge_before: bool | None = None,
    purge_after: bool | None = None,
    retry_failed: bool = False,
    retry_incomplete: bool = False,
    skip_passed: bool = False,
    include_cron: bool | None = None,
) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    cfg = load_config(project_root)
    flow_filter: frozenset[str] | None = None
    cron_case_filter: frozenset[str] | None = None
    if retry_failed:
        from ..csv_report import flows_to_retry

        flow_filter = flows_to_retry(cfg.project_root)
        if flow_filter is None:
            log.warning("No previous results.csv — running full E2E suite")
        elif not flow_filter:
            log.info("Previous run was all PASS — running full E2E suite")
            flow_filter = None
        else:
            log.info(
                "Retry-failed mode — running %d flow(s): %s",
                len(flow_filter),
                ", ".join(sorted(flow_filter)),
            )
    elif retry_incomplete:
        from ..csv_report import flows_to_retry_incomplete

        flow_filter = flows_to_retry_incomplete(cfg.project_root)
        if flow_filter is None:
            log.warning("No result/result.xlsx — running full E2E suite")
        elif not flow_filter:
            log.info("No incomplete GQL flows in workbook — running full E2E suite")
            flow_filter = None
        else:
            log.info(
                "Retry-incomplete mode — running %d flow(s): %s",
                len(flow_filter),
                ", ".join(sorted(flow_filter)),
            )
    elif skip_passed:
        from ..csv_report import (
            cron_case_filter_skip_passed,
            flow_filter_skip_passed,
            passed_keys_from_result_workbook,
        )

        passed = passed_keys_from_result_workbook(cfg.project_root)
        if passed is None:
            log.warning("No result/result.xlsx Pass sheet — running full E2E suite")
        else:
            flow_filter = flow_filter_skip_passed(cfg.project_root)
            cron_case_filter = cron_case_filter_skip_passed(cfg.project_root)
            n_gql_skip = len(passed.gql_operations)
            n_cron_skip = len(passed.cron_operations)
            n_flows = len(flow_filter) if flow_filter is not None else 0
            n_cron = len(cron_case_filter) if cron_case_filter is not None else 0
            log.info(
                "Skip-passed mode — skipping %d GQL op(s), %d cron op(s); "
                "running %d flow(s), %d cron case(s)",
                n_gql_skip,
                n_cron_skip,
                n_flows,
                n_cron,
            )
            if flow_filter is not None and not flow_filter:
                log.info("All GQL flows already PASS — skipping GQL simulation")
            if cron_case_filter is not None and not cron_case_filter:
                log.info("All cron cases already PASS — skipping cron injection")

    ensure_report_dirs(cfg.project_root)
    e2e = collect_and_validate(
        cfg,
        purge_before=purge_before,
        purge_after=purge_after,
        flow_filter=flow_filter,
        cron_case_filter=cron_case_filter,
        include_cron=include_cron,
    )

    _print_pipeline_summary(cfg, e2e)
    _write_e2e_reports(
        cfg,
        e2e,
        report_path=report_path,
        coverage_path=coverage_path,
        csv_path=csv_path,
        xlsx_path=xlsx_path,
    )
    _console_step(
        f"Output — {result_xlsx(cfg.project_root)}; details in temp/"
    )

    any_fail = any(r.status == ValidationStatus.FAIL for r in e2e.validation_results)
    return 1 if any_fail or e2e.flows_exit_code != 0 else 0
