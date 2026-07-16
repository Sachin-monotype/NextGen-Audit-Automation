#!/usr/bin/env python3
"""CLI for raw vs enriched audit event validation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .models import ValidationStatus
from .report import print_report, write_json_report
from .project_root import find_project_root
from .report_paths import gql_flows_json, offline_validation_json, result_xlsx, source_comparison_xlsx, source_validation_json, temp_path
from .utility.simulate import run_full_pipeline
from .validator import validate_all


def _default_project_root() -> Path:
    return find_project_root()


def cmd_validate(args: argparse.Namespace) -> int:
    from .event_retention import prune_captured_events

    enriched_dir = Path(args.enriched_dir).resolve()
    raw_dir = Path(args.raw_dir).resolve() if args.raw_dir else None
    root = _default_project_root()
    prune_captured_events(root)

    if not enriched_dir.is_dir():
        print(f"Error: enriched dir not found: {enriched_dir}", file=sys.stderr)
        return 1

    results = validate_all(
        enriched_dir,
        raw_dir,
        structure_only=args.structure_only,
    )

    print_report(results)

    if args.report:
        write_json_report(results, Path(args.report).resolve())

    if args.template_summary:
        templates: dict[str, list[str]] = {}
        for r in results:
            templates.setdefault(r.template_id, []).append(r.operation)
        print("Template coverage:")
        for tid, ops in sorted(templates.items()):
            print(f"  {tid}: {', '.join(sorted(set(ops)))}")

    any_fail = any(r.status == ValidationStatus.FAIL for r in results)
    return 1 if any_fail else 0


def cmd_simulate(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve() if args.project_root else _default_project_root()

    _, raw_dir, enriched_dir = run_full_pipeline(
        root,
        skip_simulation=args.skip_flows,
    )

    if args.validate:
        validate_args = argparse.Namespace(
            enriched_dir=str(enriched_dir),
            raw_dir=str(raw_dir) if raw_dir.is_dir() else None,
            structure_only=not raw_dir.is_dir(),
            report=args.report,
            template_summary=args.template_summary,
        )
        return cmd_validate(validate_args)

    print(f"Simulation complete. Events in:\n  raw: {raw_dir}\n  enriched: {enriched_dir}")
    return 0


def cmd_run_backlog(args: argparse.Namespace) -> int:
    from .pipeline.e2e import run_backlog_validation

    return run_backlog_validation(
        report_path=args.report,
        coverage_path=args.coverage_report,
        csv_path=args.csv_report,
        project_root=Path(args.project_root).resolve() if args.project_root else None,
        drain_sec=args.drain_sec,
    )


def cmd_compare_runs(args: argparse.Namespace) -> int:
    import json
    from pathlib import Path

    from .pipeline.compare_runs import compare_runs, print_compare_report
    from .report_paths import backlog_validation_json, e2e_compare_json, e2e_validation_json

    root = Path(args.project_root).resolve() if args.project_root else _default_project_root()
    backlog_path = (
        Path(args.backlog).resolve()
        if args.backlog
        else backlog_validation_json(root)
    )
    fresh_path = (
        Path(args.fresh).resolve()
        if args.fresh
        else e2e_validation_json(root)
    )
    out_path = (
        Path(args.report).resolve()
        if args.report
        else e2e_compare_json(root)
    )

    data = compare_runs(backlog_path, fresh_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print_compare_report(data)
    print(f"\nComparison written to {out_path}")
    return 0


def cmd_run_e2e(args: argparse.Namespace) -> int:
    from .pipeline.e2e import run_e2e

    purge = False if args.no_purge else None
    include_cron = False if getattr(args, "no_cron", False) else None
    skip_passed = bool(getattr(args, "skip_passed", False))
    retry_incomplete = bool(getattr(args, "retry_incomplete", False))
    return run_e2e(
        report_path=args.report,
        coverage_path=args.coverage_report,
        csv_path=args.csv_report,
        xlsx_path=args.xlsx_report,
        project_root=Path(args.project_root).resolve() if args.project_root else None,
        purge_before=purge,
        purge_after=purge,
        retry_failed=args.retry_failed,
        retry_incomplete=retry_incomplete,
        skip_passed=skip_passed,
        include_cron=include_cron,
    )


def cmd_run_full(args: argparse.Namespace) -> int:
    from .pipeline.full import run_full_validation

    purge = False if args.no_purge else None
    skip_passed = bool(getattr(args, "skip_passed", False))
    return run_full_validation(
        project_root=Path(args.project_root).resolve() if args.project_root else None,
        purge_before=purge,
        purge_after=purge,
        skip_source_validation=args.skip_sources,
        skip_ingress=args.no_ingress,
        skip_passed=skip_passed,
        sample_source=args.sample_source or "fresh",
    )


def cmd_run_cron(args: argparse.Namespace) -> int:
    from .cron.runner import run_cron_validation
    from .report_paths import e2e_cron_results_json

    root = Path(args.project_root).resolve() if args.project_root else _default_project_root()
    case_filter = frozenset(args.cases.split(",")) if args.cases else None
    run = run_cron_validation(
        project_root=root,
        case_filter=case_filter,
        settle_sec=args.settle_sec,
        report_path=Path(args.report).resolve() if args.report else e2e_cron_results_json(root),
        purge_before=args.purge,
    )
    return 1 if run.fail_count else 0


def cmd_run_ingress(args: argparse.Namespace) -> int:
    from .ingress.runner import run_ingress_validation
    from .report_paths import ingress_results_json

    root = Path(args.project_root).resolve() if args.project_root else _default_project_root()
    case_filter = frozenset(args.cases.split(",")) if args.cases else None
    category_filter = frozenset(args.categories.split(",")) if args.categories else None
    run = run_ingress_validation(
        project_root=root,
        case_filter=case_filter,
        category_filter=category_filter,
        settle_sec=args.settle_sec,
        report_path=Path(args.report).resolve() if args.report else ingress_results_json(root),
        purge_before=args.purge,
    )
    return 1 if run.fail_count else 0


def cmd_purge_ingress_queues(args: argparse.Namespace) -> int:
    from .config import load_config
    from .ingress.runner import purge_ingress_queues

    root = Path(args.project_root).resolve() if args.project_root else _default_project_root()
    cfg = load_config(root)
    purged = purge_ingress_queues(cfg)
    print("Ingress test queue purge complete:")
    for queue, count in purged.items():
        print(f"  {queue}: {count} message(s)")
    print(f"Total purged: {sum(purged.values())}")
    return 0


def cmd_purge_queues(args: argparse.Namespace) -> int:
    from .config import load_config
    from .rabbitmq.purge import purge_queues

    root = Path(args.project_root).resolve() if args.project_root else _default_project_root()
    cfg = load_config(root)
    purged = purge_queues(
        cfg.rabbitmq,
        include_dead_letter=not args.skip_dead_letter,
    )
    total = sum(purged.values())
    print("Queue purge complete:")
    for queue, count in purged.items():
        print(f"  {queue}: {count} message(s)")
    print(f"Total purged: {total}")
    return 0


def cmd_cleanup(args: argparse.Namespace) -> int:
    from .cleanup import run_post_run_cleanup

    root = Path(args.project_root).resolve() if args.project_root else _default_project_root()
    result = run_post_run_cleanup(root)
    print("Cleanup complete:")
    print(f"  Assets deleted:        {result.assets_deleted}")
    print(f"  Projects deleted:      {result.projects_deleted}")
    print(f"  Roles deleted:         {result.roles_deleted}")
    print(f"  Teams deleted:         {result.teams_deleted}")
    print(f"  Notifications deleted: {result.notifications_deleted}")
    if result.errors:
        for err in result.errors:
            print(f"  Warning: {err}")
    return 1 if result.errors else 0


def cmd_validate_sources(args: argparse.Namespace) -> int:
    from .source_validation.excel_report import write_source_validation_workbook
    from .source_validation.field_specs import operations_for_iteration
    from .source_validation.runner import run_source_validation, write_source_validation_report

    root = Path(args.project_root).resolve() if args.project_root else _default_project_root()
    iteration = args.iteration

    if args.simulate_first:
        from .pipeline.e2e import run_e2e

        print("Running E2E simulation to capture fresh raw/enriched JSON…")
        e2e_code = run_e2e(
            report_path=str(root / "temp" / "validation.json"),
            coverage_path=str(root / "temp" / "coverage-matrix.json"),
            csv_path=str(root / "temp" / "results.csv"),
            xlsx_path=str(result_xlsx(root)),
            project_root=root,
            purge_before=False if args.no_purge else None,
            purge_after=False if args.no_purge else None,
        )
        if e2e_code != 0:
            print(f"Warning: E2E exited {e2e_code}; validating captured events only.")

    sample_source = args.sample_source or "fresh"
    ops = (
        [o.strip() for o in args.operations.split(",") if o.strip()]
        if args.operations
        else list(operations_for_iteration(iteration, project_root=root))
    )
    report = run_source_validation(
        project_root=root,
        operations=ops,
        iteration=iteration,
        sample_source=sample_source,
    )

    out = (
        Path(args.report).resolve()
        if args.report
        else source_validation_json(root)
    )
    write_source_validation_report(report, out)

    final_xlsx = (
        Path(args.xlsx).resolve()
        if args.xlsx
        else source_comparison_xlsx(root)
    )
    if not args.no_xlsx:
        write_source_validation_workbook(
            path=final_xlsx,
            report=report,
            comparison_rows=report.comparison_rows,
            project_root=root,
            operations=ops,
        )

    print(
        f"Source validation: PASS={report.passed} FAIL={report.failed} SKIP={report.skipped} "
        f"rows={len(report.comparison_rows)} discovery={report.discovery_calls} "
        f"pandas={report.pandas_summary}"
    )
    for row in report.operations:
        if row.status != "PASS":
            print(f"  {row.operation}: {row.status} — {row.reason or 'see report'}")
    print(f"JSON: {out}")
    if not args.no_xlsx:
        from .source_validation.mapping_registry import categories_for_operations

        cat_count = len(categories_for_operations(ops))
        print(f"Excel ({cat_count} category tabs, {len(ops)} events): {final_xlsx}")
    return 1 if report.failed else 0


def cmd_sync_repos(args: argparse.Namespace) -> int:
    from .sync.team_repos import print_sync_report, sync_team_repos, write_sync_report_json

    report = sync_team_repos(apply_routing_map=args.apply)
    print_sync_report(report)
    if args.report:
        write_sync_report_json(report, Path(args.report).resolve())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate enriched audit events against raw events (mt-audit-log-automation)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="Validate captured event JSON files")
    p_validate.add_argument(
        "--enriched-dir",
        default=None,
        help="Path to payload/enrich/ (default: <project>/payload/enrich)",
    )
    p_validate.add_argument(
        "--raw-dir",
        default=None,
        help="Path to payload/raw/ (optional — structure-only if missing)",
    )
    p_validate.add_argument(
        "--structure-only",
        action="store_true",
        help="Skip raw vs enriched comparison; validate enriched structure only",
    )
    p_validate.add_argument(
        "--report",
        default=None,
        help="Write JSON report to this path",
    )
    p_validate.add_argument(
        "--template-summary",
        action="store_true",
        help="Print template → operations mapping from this run",
    )
    p_validate.set_defaults(func=cmd_validate)

    p_sim = sub.add_parser(
        "simulate",
        help="Run Python GraphQL flows then validate captured event files",
    )
    p_sim.add_argument(
        "--project-root",
        default=None,
        help="Path to mt-audit-log-automation repo root",
    )
    p_sim.add_argument(
        "--skip-flows",
        action="store_true",
        help="Skip GraphQL simulation; only validate existing captured files",
    )
    p_sim.add_argument(
        "--no-validate",
        dest="validate",
        action="store_false",
        help="Only run simulation, do not validate",
    )
    p_sim.add_argument("--report", default=None, help="Write JSON validation report")
    p_sim.add_argument("--template-summary", action="store_true")
    p_sim.set_defaults(validate=True, func=cmd_simulate)

    p_e2e = sub.add_parser(
        "run-e2e",
        help="Full E2E: consume RabbitMQ queues, simulate all flows, validate raw vs enriched",
    )
    p_e2e.add_argument("--project-root", default=None, help="Repo root (default: auto-detect)")
    p_e2e.add_argument("--report", default=None, help="Write JSON validation report")
    p_e2e.add_argument(
        "--coverage-report",
        default=None,
        help="Write coverage matrix JSON (default: coverage-matrix.json next to --report)",
    )
    p_e2e.add_argument(
        "--csv-report",
        default=None,
        help="Write CSV summary (operation, simulation, raw, enriched, overall)",
    )
    p_e2e.add_argument(
        "--xlsx-report",
        default=None,
        help="Write Excel workbook (Results + Operations sheets). Default: e2e-results.xlsx next to CSV",
    )
    p_e2e.add_argument(
        "--no-purge",
        action="store_true",
        help="Do not purge raw/enriched/dead-letter queues before or after the run",
    )
    p_e2e.add_argument(
        "--retry-failed",
        action="store_true",
        help="Re-run only flows that were not PASS in temp/results.csv",
    )
    p_e2e.add_argument(
        "--retry-incomplete",
        action="store_true",
        help="Re-run flows for GQL ops that are FAIL or SKIP in result/result.xlsx",
    )
    p_e2e.add_argument(
        "--no-cron",
        action="store_true",
        help="Skip scheduler/cron raw-queue injection after GQL simulation",
    )
    p_e2e.set_defaults(func=cmd_run_e2e)

    p_full = sub.add_parser(
        "run-full",
        help="Full pipeline: GQL + cron + raw↔enriched + source comparison (default ./run.sh)",
    )
    p_full.add_argument("--project-root", default=None, help="Repo root (default: auto-detect)")
    p_full.add_argument(
        "--no-purge",
        action="store_true",
        help="Do not purge raw/enriched/dead-letter queues before or after the run",
    )
    p_full.add_argument(
        "--skip-sources",
        action="store_true",
        help="Skip UMS/CMS/Discovery source comparison (E2E + cron only)",
    )
    p_full.add_argument(
        "--no-ingress",
        action="store_true",
        help="Skip Ingress API desktop/plugin injection during full run",
    )
    p_full.add_argument(
        "--skip-passed",
        action="store_true",
        help="Skip events that fully passed in result/result.xlsx (GQL, cron, ingress)",
    )
    p_full.add_argument(
        "--sample-source",
        choices=("fresh", "queue-pairs", "auto"),
        default="fresh",
        help="Enriched JSON source for source validation (default: fresh)",
    )
    p_full.set_defaults(func=cmd_run_full)

    p_backlog = sub.add_parser(
        "validate-backlog",
        help="Consume existing RabbitMQ backlog and validate raw ↔ enriched (no simulation)",
    )
    p_backlog.add_argument("--project-root", default=None, help="Repo root (default: auto-detect)")
    p_backlog.add_argument("--report", default=None, help="Write JSON validation report")
    p_backlog.add_argument(
        "--coverage-report",
        default=None,
        help="Write coverage matrix JSON",
    )
    p_backlog.add_argument(
        "--csv-report",
        default=None,
        help="Write CSV summary",
    )
    p_backlog.add_argument(
        "--drain-sec",
        type=float,
        default=None,
        help="Max seconds to drain queues (default: BACKLOG_DRAIN_SEC from .env, 600)",
    )
    p_backlog.set_defaults(func=cmd_run_backlog)

    p_cron = sub.add_parser(
        "run-cron",
        help="Publish scheduler/cron raw audit payloads to RabbitMQ and validate enrichment",
    )
    p_cron.add_argument("--project-root", default=None, help="Repo root (default: auto-detect)")
    p_cron.add_argument(
        "--report",
        default=None,
        help="Write cron case summary JSON (default: temp/cron-results.json)",
    )
    p_cron.add_argument(
        "--cases",
        default=None,
        help="Comma-separated cron payload stems (default: all under data/cron_payloads/)",
    )
    p_cron.add_argument(
        "--settle-sec",
        type=float,
        default=None,
        help="Seconds to wait for enriched events after publish (default: CRON_SETTLE_SEC or 90)",
    )
    p_cron.add_argument(
        "--purge",
        action="store_true",
        help="Purge automation queues before publishing cron payloads",
    )
    p_cron.set_defaults(func=cmd_run_cron)

    p_ingress = sub.add_parser(
        "run-ingress",
        help="POST desktop/plugin Ingress API audit events and validate PP test queues",
    )
    p_ingress.add_argument("--project-root", default=None)
    p_ingress.add_argument(
        "--report",
        default=None,
        help="Write ingress case summary JSON (default: temp/ingress-results.json)",
    )
    p_ingress.add_argument(
        "--cases",
        default=None,
        help="Comma-separated case ids from data/ingress_payloads/manifest.json",
    )
    p_ingress.add_argument(
        "--categories",
        default=None,
        help="Comma-separated categories (plugin_events, desktop_app_preference_page, font_activations, login)",
    )
    p_ingress.add_argument(
        "--settle-sec",
        type=float,
        default=None,
        help="Seconds to wait for queue capture after POST (default: INGRESS_SETTLE_SEC or 45)",
    )
    p_ingress.add_argument(
        "--purge",
        action="store_true",
        help="Purge PP ingress test queues before posting events",
    )
    p_ingress.set_defaults(func=cmd_run_ingress)

    p_purge_ingress = sub.add_parser(
        "purge-ingress-queues",
        help="Purge mt.platform.resolver.*_test_queue (desktop/plugin ingress tap queues)",
    )
    p_purge_ingress.add_argument("--project-root", default=None)
    p_purge_ingress.set_defaults(func=cmd_purge_ingress_queues)

    p_compare = sub.add_parser(
        "compare-runs",
        help="Compare backlog validation vs fresh E2E validation reports",
    )
    p_compare.add_argument("--project-root", default=None)
    p_compare.add_argument("--backlog", default=None, help="Backlog validation.json path")
    p_compare.add_argument("--fresh", default=None, help="Fresh E2E validation.json path")
    p_compare.add_argument("--report", default=None, help="Output comparison JSON path")
    p_compare.set_defaults(func=cmd_compare_runs)

    p_purge = sub.add_parser(
        "purge-queues",
        help="Purge all messages from raw, enriched, and dead-letter automation queues",
    )
    p_purge.add_argument("--project-root", default=None)
    p_purge.add_argument(
        "--skip-dead-letter",
        action="store_true",
        help="Only purge raw and enriched queues",
    )
    p_purge.set_defaults(func=cmd_purge_queues)

    p_cleanup = sub.add_parser(
        "cleanup",
        help="Remove automation roles/teams and notification DB rows for test user",
    )
    p_cleanup.add_argument("--project-root", default=None)
    p_cleanup.set_defaults(func=cmd_cleanup)

    p_sources = sub.add_parser(
        "validate-sources",
        help="Cross-check enriched JSON vs UMS/CMS/Discovery/AMS (default: 20 events, Excel export)",
    )
    p_sources.add_argument("--project-root", default=None)
    p_sources.add_argument(
        "--report",
        default=None,
        help="Write JSON report (default: temp/source-validation.json)",
    )
    p_sources.add_argument(
        "--xlsx",
        default=None,
        help="Write Excel workbook (default: result/source-comparison.xlsx)",
    )
    p_sources.add_argument(
        "--no-xlsx",
        action="store_true",
        help="Skip Excel workbook export",
    )
    p_sources.add_argument(
        "--iteration",
        type=int,
        default=1,
        help="Iteration metadata (default: 1 — all available samples)",
    )
    p_sources.add_argument(
        "--simulate-first",
        action="store_true",
        help="Run E2E GraphQL simulation + RabbitMQ capture before source validation",
    )
    p_sources.add_argument(
        "--no-purge",
        action="store_true",
        help="With --simulate-first: do not purge queues before/after E2E",
    )
    p_sources.add_argument(
        "--sample-source",
        choices=("fresh", "queue-pairs", "auto"),
        default="fresh",
        help="Enriched JSON source: fresh E2E captures (default), static queue-pairs, or auto",
    )
    p_sources.add_argument(
        "--operations",
        default=None,
        help="Comma-separated operations (default: all fresh captures or queue-pair samples)",
    )
    p_sources.set_defaults(func=cmd_validate_sources)

    p_sync = sub.add_parser(
        "sync-repos",
        help="Sync routing map & check team commits from resolver + connect-api repos",
    )
    p_sync.add_argument(
        "--apply",
        action="store_true",
        help="Update local outbound-routing-map.json from resolver develop branch",
    )
    p_sync.add_argument(
        "--report",
        default=None,
        help="Write sync JSON report (default: reports/temp/upstream-sync.json)",
    )
    p_sync.set_defaults(func=cmd_sync_repos)

    p_refresh = sub.add_parser(
        "refresh-tokens",
        help="Fetch fresh OAuth bearer tokens and update .env (MTConnectAutomation flow)",
    )
    p_refresh.add_argument("--project-root", default=None)
    p_refresh.set_defaults(func=cmd_refresh_tokens)

    p_flows = sub.add_parser(
        "simulate-flows",
        help="Run GraphQL simulation flows in Python (no RabbitMQ)",
    )
    p_flows.add_argument("--project-root", default=None)
    p_flows.add_argument(
        "--report",
        default=None,
        help="Write flows-results.json (default: reports/gql/flows-results.json)",
    )
    p_flows.set_defaults(func=cmd_simulate_flows)

    p_na = sub.add_parser(
        "notification-audit",
        help="Generate notification audit CSV (UI, cURL, raw/enriched JSON)",
    )
    p_na.add_argument("--project-root", default=None)
    p_na.add_argument(
        "--report",
        default=None,
        help="Output CSV path (default: reports/temp/notification-audit.csv)",
    )
    p_na.set_defaults(func=cmd_notification_audit)

    p_postman = sub.add_parser(
        "export-postman",
        help="Export GraphQL simulation flows as Postman collection + environment",
    )
    p_postman.add_argument("--project-root", default=None)
    p_postman.add_argument(
        "--output",
        default=None,
        help="Collection path (default: postman/MT-Audit-Simulation.postman_collection.json)",
    )
    p_postman.add_argument(
        "--environment",
        default=None,
        help="Environment path (default: postman/MT-Audit-Simulation.postman_environment.json)",
    )
    p_postman.add_argument(
        "--validate",
        action="store_true",
        help="Call GraphQL for each request and tag PASS/FAIL/SKIP in request names",
    )
    p_postman.set_defaults(func=cmd_export_postman)

    p_ts = sub.add_parser(
        "testing-sheet",
        help="Generate simulation testing CSV + docs/SIMULATION_TESTING_GUIDE.md",
    )
    p_ts.add_argument("--project-root", default=None)
    p_ts.add_argument(
        "--report",
        default=None,
        help="Full sheet CSV (default: reports/temp/simulation-testing-sheet.csv)",
    )
    p_ts.add_argument(
        "--working",
        default=None,
        help="Working-only CSV (default: reports/temp/simulation-testing-working.csv)",
    )
    p_ts.add_argument(
        "--guide",
        action="store_true",
        help="Also write docs/SIMULATION_TESTING_GUIDE.md",
    )
    p_ts.add_argument(
        "--no-validate",
        action="store_true",
        help="Skip live GraphQL curl validation",
    )
    p_ts.set_defaults(func=cmd_testing_sheet)

    p_epic = sub.add_parser(
        "epic-status",
        help="Generate epic vs actual enrichment status sheet (event routing key names)",
    )
    p_epic.add_argument("--project-root", default=None)
    p_epic.add_argument(
        "--csv",
        default=None,
        help="CSV path (default: temp/epic-vs-actual-status.csv)",
    )
    p_epic.add_argument(
        "--xlsx",
        default=None,
        help="Excel path (default: temp/epic-vs-actual-status.xlsx)",
    )
    p_epic.set_defaults(func=cmd_epic_status)

    return parser


def cmd_refresh_tokens(args: argparse.Namespace) -> int:
    from .auth import refresh_env_tokens

    root = Path(args.project_root).resolve() if args.project_root else _default_project_root()
    tokens = refresh_env_tokens(root)
    print("Refreshed .env tokens:")
    print(f"  BEARER_TOKEN: OAuth ({len(tokens['BEARER_TOKEN'])} chars)")
    if tokens.get("NEXTGEN_BEARER_TOKEN"):
        print(f"  NEXTGEN_BEARER_TOKEN: manual SSO ({len(tokens['NEXTGEN_BEARER_TOKEN'])} chars, unchanged)")
    else:
        print("  NEXTGEN_BEARER_TOKEN: not set — paste browser SSO Bearer for /graph font ops")
    if tokens.get("BEARER_TOKEN_SECONDARY"):
        print(f"  BEARER_TOKEN_SECONDARY: secondary ({len(tokens['BEARER_TOKEN_SECONDARY'])} chars)")
    return 0


def cmd_simulate_flows(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .simulation.runner import run_flows_simulation

    root = Path(args.project_root).resolve() if args.project_root else _default_project_root()
    out = Path(args.report).resolve() if args.report else gql_flows_json(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    exit_code = run_flows_simulation(root, results_path=out)
    print(f"Flow results written to {out} (exit={exit_code})")
    return exit_code


def cmd_notification_audit(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .helpers.notification_audit_report import write_notification_audit_csv

    root = Path(args.project_root).resolve() if args.project_root else _default_project_root()
    out = (
        Path(args.report).resolve()
        if args.report
        else temp_path(root, "notification-audit.csv")
    )
    write_notification_audit_csv(path=out, project_root=root)
    print(f"Notification audit report: {out}")
    return 0


def cmd_export_postman(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .utility.postman_export import write_postman_export

    root = Path(args.project_root).resolve() if args.project_root else _default_project_root()
    collection = (
        Path(args.output).resolve()
        if args.output
        else root / "postman" / "MT-Audit-Simulation.postman_collection.json"
    )
    environment = Path(args.environment).resolve() if args.environment else None
    validation = temp_path(root, "postman-validation.json") if args.validate else None

    result = write_postman_export(
        root,
        collection_path=collection,
        environment_path=environment,
        validate=args.validate,
        validation_path=validation,
    )
    print(f"Postman collection ({result.request_count} requests): {result.collection_path}")
    print(f"Postman environment: {result.environment_path}")
    if result.validation_path:
        print(f"Validation summary: {result.validation_path}")
    return 0


def cmd_testing_sheet(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .helpers.testing_guide import write_testing_guide_md
    from .helpers.testing_sheet import build_simulation_rows, write_testing_sheet_csv, write_working_sheet_csv

    root = Path(args.project_root).resolve() if args.project_root else _default_project_root()
    validate = not args.no_validate
    full = (
        Path(args.report).resolve()
        if args.report
        else temp_path(root, "simulation-testing-sheet.csv")
    )
    working = (
        Path(args.working).resolve()
        if args.working
        else temp_path(root, "simulation-testing-working.csv")
    )
    rows = build_simulation_rows(root, validate_curls=validate)
    ok = write_testing_sheet_csv(path=full, project_root=root, validate_curls=validate, rows=rows)
    working_n = write_working_sheet_csv(path=working, project_root=root, validate_curls=validate, rows=rows)
    print(f"Testing sheet: {full} ({ok} curl OK)")
    print(f"Working curls: {working} ({working_n} rows)")
    if args.guide:
        guide = root / "docs" / "SIMULATION_TESTING_GUIDE.md"
        write_testing_guide_md(path=guide, project_root=root, validate_curls=validate, rows=rows)
        print(f"Testing guide: {guide}")
    return 0


def cmd_epic_status(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .helpers.epic_status_report import write_epic_status_csv, write_epic_status_workbook

    root = Path(args.project_root).resolve() if args.project_root else _default_project_root()
    csv_path = Path(args.csv).resolve() if args.csv else temp_path(root, "epic-vs-actual-status.csv")
    xlsx_path = Path(args.xlsx).resolve() if args.xlsx else temp_path(root, "epic-vs-actual-status.xlsx")
    n = write_epic_status_csv(path=csv_path, project_root=root)
    write_epic_status_workbook(path=xlsx_path, project_root=root)
    print(f"Epic status CSV ({n} rows): {csv_path}")
    print(f"Epic status Excel: {xlsx_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "sync-repos" and not args.report:
        args.report = str(temp_path(_default_project_root(), "upstream-sync.json"))

    if args.command == "validate" and not args.report:
        args.report = str(offline_validation_json(_default_project_root()))

    if args.command == "validate":
        if args.enriched_dir is None:
            root = _default_project_root()
            args.enriched_dir = str(root / "payload" / "enrich")
        if args.raw_dir is None and not args.structure_only:
            root = _default_project_root()
            raw = root / "payload" / "raw"
            if raw.is_dir():
                args.raw_dir = str(raw)

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
