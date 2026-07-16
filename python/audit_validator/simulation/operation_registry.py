"""Operations exercised by simulation flows — parsed from flows.py."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from ..utility.operation_graphql import load_operation_index

_FLOWS_FILE = Path(__file__).resolve().parent / "flows.py"
_RUN_OPERATION = re.compile(r'run_operation\(\s*ctx,\s*"([^"]+)"')
_LABEL_PARENS = re.compile(r"\s*\([^)]+\)")


def _normalize_operation_label(label: str) -> str:
    """createProject (FontProject) → createProject"""
    return _LABEL_PARENS.sub("", label).strip()


@lru_cache(maxsize=1)
def simulated_operations() -> frozenset[str]:
    """Audit operation names invoked via run_operation() in simulation flows."""
    if not _FLOWS_FILE.is_file():
        return frozenset()
    known = load_operation_index()
    ops: set[str] = set()
    for label in _RUN_OPERATION.findall(_FLOWS_FILE.read_text(encoding="utf-8")):
        op = _normalize_operation_label(label)
        if op in known:
            ops.add(op)
    return frozenset(ops)
