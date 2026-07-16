"""Unified full validation: GQL + cron + ingress + raw↔enriched + source probes."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from ..config import load_config
from ..coverage.report import load_coverage_json
from ..models import ValidationStatus
from ..report import append_validation_results, load_validation_results
from ..report_paths import (
    coverage_json,
    e2e_cron_results_json,
    e2e_flows_json,
    ensure_output_dirs,
    ingress_results_json,
    result_xlsx,
    source_comparison_xlsx,
    source_validation_json,
    validation_json,
)
from .e2e import run_e2e

log = logging.getLogger(__name__)


def _skip_ingress_env() -> bool:
    return os.getenv("SKIP_INGRESS_INJECTION", "").strip().lower() in ("1", "true", "yes")


def _refresh_result_workbook(root: Path) -> None:
    from ..csv_report import write_e2e_workbook

    val_path = validation_json(root)
    cov_path = coverage_json(root)
    flows_path = e2e_flows_json(root)
    cron_path = e2e_cron_results_json(root)
    ingress_path = ingress_results_json(root)

    write_e2e_workbook(
        path=result_xlsx(root),
        validation_results=load_validation_results(val_path),
        coverage=load_coverage_json(cov_path),
        flows_results_path=flows_path if flows_path.is_file() else None,
        cron_results_path=cron_path if cron_path.is_file() else None,
        ingress_results_path=ingress_path if ingress_path.is_file() else None,
        project_root=root,
    )
    print(f"Excel workbook updated with ingress → {result_xlsx(root)}")


def run_full_validation(
    *,
    project_root: Path | None = None,
    purge_before: bool | None = None,
    purge_after: bool | None = None,
    skip_source_validation: bool = False,
    skip_ingress: bool | None = None,
    skip_passed: bool = False,
    sample_source: str = "fresh",
) -> int:
    """
    Single pipeline:
      1. RabbitMQ consumer + GQL simulation
      2. Cron/scheduler raw publish (same session, shared settle)
      3. Ingress API desktop/plugin events (PP test queues)
      4. Raw ↔ enriched validation + Excel reports
      5. Source comparison (UMS / CMS / Discovery probes) → source-comparison.xlsx
    """
    from ..source_validation.excel_report import write_source_validation_workbook
    from ..source_validation.field_specs import operations_for_iteration
    from ..source_validation.runner import run_source_validation, write_source_validation_report

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    root = project_root or load_config().project_root
    ensure_output_dirs(root)
    do_ingress = not (skip_ingress if skip_ingress is not None else _skip_ingress_env())

    print("\n" + "=" * 72)
    print("  FULL AUDIT VALIDATION PIPELINE")
    print("  GQL → cron → ingress → raw↔enriched → source probes")
    print("=" * 72 + "\n")

    e2e_code = run_e2e(
        report_path=str(validation_json(root)),
        coverage_path=str(coverage_json(root)),
        csv_path=str(root / "temp" / "results.csv"),
        xlsx_path=str(result_xlsx(root)),
        project_root=root,
        purge_before=purge_before,
        purge_after=purge_after,
        include_cron=True,
        skip_passed=skip_passed,
    )

    ingress_code = 0
    if do_ingress:
        print("\n" + "-" * 72)
        print("  INGRESS API (desktop / plugin — PP test queues via env)")
        print("-" * 72 + "\n")
        from ..ingress.config import IngressConfigError
        from ..ingress.runner import run_ingress_validation

        ingress_case_filter = None
        if skip_passed:
            from ..csv_report import ingress_case_filter_skip_passed

            ingress_case_filter = ingress_case_filter_skip_passed(root)
            if ingress_case_filter is not None:
                log.info(
                    "Skip-passed ingress — running %d case(s)",
                    len(ingress_case_filter),
                )

        try:
            ingress_run = run_ingress_validation(
                project_root=root,
                purge_before=True,
                report_path=ingress_results_json(root),
                case_filter=ingress_case_filter,
            )
            if ingress_run.validation_results:
                append_validation_results(validation_json(root), ingress_run.validation_results)
            ingress_code = 1 if ingress_run.fail_count else 0
            _refresh_result_workbook(root)
        except IngressConfigError as exc:
            log.error("%s", exc)
            ingress_code = 1

    if skip_source_validation:
        return max(e2e_code, ingress_code)

    print("\n" + "-" * 72)
    print("  SOURCE VALIDATION (enriched JSON vs UMS / CMS / Discovery)")
    print("-" * 72 + "\n")

    ops = list(operations_for_iteration(1, project_root=root))
    if skip_passed:
        from ..csv_report import passed_keys_from_result_workbook

        passed = passed_keys_from_result_workbook(root)
        if passed is not None:
            skip_ops = passed.gql_operations | passed.cron_operations
            before = len(ops)
            ops = [op for op in ops if op not in skip_ops]
            log.info(
                "Skip-passed source validation — %d op(s) skipped, %d remaining",
                before - len(ops),
                len(ops),
            )
    report = run_source_validation(
        project_root=root,
        operations=ops,
        iteration=1,
        sample_source=sample_source,
    )

    write_source_validation_report(report, source_validation_json(root))

    xlsx_path = source_comparison_xlsx(root)
    write_source_validation_workbook(
        path=xlsx_path,
        report=report,
        comparison_rows=report.comparison_rows,
        project_root=root,
        operations=ops,
    )

    print(
        f"\nSource validation: PASS={report.passed} FAIL={report.failed} SKIP={report.skipped} "
        f"→ {xlsx_path}"
    )

    print("\n" + "=" * 72)
    print("  OUTPUT")
    print(f"    {result_xlsx(root)}")
    print(f"    {xlsx_path}")
    print(f"    payload/raw/ + payload/enrich/ (GQL/cron)")
    print(f"    payload/ingress/raw + enrich/ (Ingress API)")
    print("=" * 72 + "\n")

    source_fail = report.failed > 0
    return 1 if e2e_code != 0 or ingress_code != 0 or source_fail else 0
