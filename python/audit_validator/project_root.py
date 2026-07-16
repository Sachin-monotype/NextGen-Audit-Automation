"""Locate the mt-audit-log-automation project root from the Python package."""

from __future__ import annotations

from pathlib import Path


def find_project_root(start: Path | None = None) -> Path:
    """
    Walk up from `start` (default: this package) until we find a directory that
    contains `python/audit_validator/` and either `.env` or `reports/`.
    """
    here = start or Path(__file__).resolve().parent
    for parent in [here, *here.parents]:
        if not (parent / "python" / "audit_validator" / "__init__.py").is_file():
            continue
        if (parent / ".env").is_file() or (parent / "reports").is_dir():
            return parent
    raise FileNotFoundError(
        "Could not locate project root (expected python/audit_validator/ plus .env or reports/)"
    )
