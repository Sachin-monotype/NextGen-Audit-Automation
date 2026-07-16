"""Load GraphQL documents bundled with the Python package."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "graphql_documents.json"


@lru_cache(maxsize=1)
def load_graphql_documents(_project_root: str | None = None) -> dict[str, str]:
    """Return export-name → minified GraphQL document string."""
    if not _DATA_FILE.is_file():
        raise FileNotFoundError(
            f"GraphQL document bundle not found: {_DATA_FILE}. "
            "Run scripts/export_graphql_documents.py to regenerate."
        )
    raw = json.loads(_DATA_FILE.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in raw.items()}


def get_document(project_root: Path, export_name: str) -> str | None:
    return load_graphql_documents(str(project_root)).get(export_name)
