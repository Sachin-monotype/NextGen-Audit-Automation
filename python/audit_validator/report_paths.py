"""Canonical output paths: result/, payload/, temp/."""

from __future__ import annotations

import shutil
from pathlib import Path


def result_dir(root: Path) -> Path:
    return root / "result"


def result_xlsx(root: Path) -> Path:
    return result_dir(root) / "result.xlsx"


def source_comparison_xlsx(root: Path) -> Path:
    return result_dir(root) / "source-comparison.xlsx"


def payload_dir(root: Path) -> Path:
    return root / "payload"


def payload_raw_dir(root: Path) -> Path:
    return payload_dir(root) / "raw"


def payload_enrich_dir(root: Path) -> Path:
    return payload_dir(root) / "enrich"


def payload_ingress_raw_dir(root: Path) -> Path:
    return payload_dir(root) / "ingress" / "raw"


def payload_ingress_enrich_dir(root: Path) -> Path:
    return payload_dir(root) / "ingress" / "enrich"


def temp_dir(root: Path) -> Path:
    return root / "temp"


def temp_path(root: Path, name: str) -> Path:
    return temp_dir(root) / name


def ensure_output_dirs(root: Path) -> dict[str, Path]:
    """Create result/, payload/raw, payload/enrich, temp/."""
    dirs = {
        "result": result_dir(root),
        "payload_raw": payload_raw_dir(root),
        "payload_enrich": payload_enrich_dir(root),
        "payload_ingress_raw": payload_ingress_raw_dir(root),
        "payload_ingress_enrich": payload_ingress_enrich_dir(root),
        "temp": temp_dir(root),
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def reset_payload_dirs(root: Path) -> None:
    """Clear payload/raw and payload/enrich before a fresh capture run."""
    for sub in ("raw", "enrich"):
        path = payload_dir(root) / sub
        if path.is_dir():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)


# ── Internal JSON / logs (temp only — not manager-facing) ─────────────────────

def validation_json(root: Path) -> Path:
    return temp_path(root, "validation.json")


def coverage_json(root: Path) -> Path:
    return temp_path(root, "coverage-matrix.json")


def results_csv(root: Path) -> Path:
    return temp_path(root, "results.csv")


def cron_results_json(root: Path) -> Path:
    return temp_path(root, "cron-results.json")


def ingress_results_json(root: Path) -> Path:
    return temp_path(root, "ingress-results.json")


def flows_results_json(root: Path) -> Path:
    return temp_path(root, "flows-results.json")


def run_log(root: Path) -> Path:
    return temp_path(root, "latest-run.log")


def source_validation_json(root: Path) -> Path:
    return temp_path(root, "source-validation.json")


def backlog_validation_json(root: Path) -> Path:
    return temp_path(root, "backlog-validation.json")


def backlog_coverage_json(root: Path) -> Path:
    return temp_path(root, "backlog-coverage-matrix.json")


def backlog_results_csv(root: Path) -> Path:
    return temp_path(root, "backlog-results.csv")


def backlog_compare_json(root: Path) -> Path:
    return temp_path(root, "backlog-vs-fresh.json")


def offline_validation_json(root: Path) -> Path:
    return temp_path(root, "offline-validation.json")


def gql_flows_json(root: Path) -> Path:
    return temp_path(root, "gql-flows-results.json")


# Backward-compatible aliases (pipeline code migration)
def ensure_report_dirs(root: Path) -> dict[str, Path]:
    return ensure_output_dirs(root)


def e2e_validation_json(root: Path) -> Path:
    return validation_json(root)


def e2e_coverage_json(root: Path) -> Path:
    return coverage_json(root)


def e2e_results_csv(root: Path) -> Path:
    return results_csv(root)


def e2e_results_xlsx(root: Path) -> Path:
    return result_xlsx(root)


def e2e_cron_results_json(root: Path) -> Path:
    return cron_results_json(root)


def e2e_ingress_results_json(root: Path) -> Path:
    return ingress_results_json(root)


def e2e_flows_json(root: Path) -> Path:
    return flows_results_json(root)


def e2e_run_log(root: Path) -> Path:
    return run_log(root)


def e2e_compare_json(root: Path) -> Path:
    return backlog_compare_json(root)


def backlog_report_dir(root: Path) -> Path:
    path = temp_path(root, "backlog")
    path.mkdir(parents=True, exist_ok=True)
    return path


def backlog_validation_json_legacy(root: Path) -> Path:
    return backlog_validation_json(root)


def report_dirs(root: Path) -> dict[str, Path]:
    """Legacy — maps old report bucket names to temp/."""
    t = temp_dir(root)
    return {"e2e": t, "offline": t, "gql": t, "temp": t}
