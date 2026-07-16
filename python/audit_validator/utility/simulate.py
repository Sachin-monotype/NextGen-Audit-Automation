"""Legacy simulate helper — runs Python flows then points at captured event dirs."""

from __future__ import annotations

from pathlib import Path

from ..project_root import find_project_root
from ..simulation.runner import run_flows_simulation


def run_full_pipeline(
    project_root: Path | None = None,
    *,
    skip_simulation: bool = False,
    consumers_only: bool = False,
) -> tuple[int, Path, Path]:
    root = project_root or find_project_root()
    raw_dir = root / "payload" / "raw"
    enriched_dir = root / "payload" / "enrich"

    if not skip_simulation:
        exit_code = run_flows_simulation(root)
        if exit_code != 0:
            print(f"Warning: GraphQL simulation exited with code {exit_code}")

    return 0, raw_dir, enriched_dir
