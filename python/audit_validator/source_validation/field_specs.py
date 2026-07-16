"""Field-level enrichment source expectations (from audit-events.xlsx + resolver enrichers)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .audit_events_registry import operations_with_fresh_captures, operations_with_samples
from ..cron.payloads import load_cron_cases
from ..ingress.payloads import load_ingress_cases


def _ingress_captured_operations(project_root: Path) -> set[str]:
    ingress_enrich = project_root / "payload" / "ingress" / "enrich"
    if not ingress_enrich.is_dir():
        return set()
    return {p.stem for p in ingress_enrich.glob("*.json") if not p.stem.startswith("unknown-")}


@dataclass(frozen=True)
class FieldSpec:
    path: str
    source: str
    notes: str = ""


def default_operations(project_root: Path | None = None) -> tuple[str, ...]:
    """Operations available for validation — prefer fresh E2E captures over static queue-pairs."""
    if project_root is None:
        from ..project_root import find_project_root
        project_root = find_project_root()
    enriched_dir = project_root / "payload" / "enrich"
    fresh: set[str] = set(operations_with_fresh_captures(enriched_dir))
    for case in load_cron_cases():
        if (enriched_dir / f"{case.operation}.json").is_file():
            fresh.add(case.operation)
    fresh.update(_ingress_captured_operations(project_root))
    for case in load_ingress_cases():
        if case.operation not in fresh and (project_root / "payload" / "ingress" / "enrich" / f"{case.operation}.json").is_file():
            fresh.add(case.operation)
    if fresh:
        return tuple(sorted(fresh))
    qp = project_root / "reports" / "queue-pairs" / "enriched"
    return tuple(operations_with_samples(qp))


ITERATION_2_OPERATIONS: tuple[str, ...] = (
    "activateFamily", "activateStyle", "deactivateStyle", "activateVariation",
    "bulkActivateStyles", "bulkDeactivateStyles", "createRole", "createTeam",
    "updateRole", "deleteRoles", "createProject", "publishProject", "createAsset",
    "updateAsset", "createPrivateTags", "addFavoriteStyles", "addFavoriteFamilies",
    "activateList", "createWebProject", "updateProfile",
)
DEFAULT_ITERATION_OPERATIONS = ITERATION_2_OPERATIONS
MAX_SOURCE_VALIDATION_ITERATIONS = 1

try:
    ALL_SAMPLE_OPERATIONS: tuple[str, ...] = default_operations()
except Exception:
    ALL_SAMPLE_OPERATIONS = ITERATION_2_OPERATIONS


def operations_for_iteration(iteration: int = 1, project_root: Path | None = None) -> tuple[str, ...]:
    if project_root:
        return default_operations(project_root)
    return ALL_SAMPLE_OPERATIONS


OPERATION_SPECS: dict[str, tuple[FieldSpec, ...]] = {}
