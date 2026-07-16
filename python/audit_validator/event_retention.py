"""One JSON file per operation under payload/raw and payload/enrich."""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from .report_paths import payload_enrich_dir, payload_raw_dir, reset_payload_dirs

log = logging.getLogger(__name__)

_LEGACY_SUFFIX = "-mtconnect-api"
_CORR_SUFFIX = re.compile(r"^(.+)-[0-9a-f]{8}$")


def _operation_from_stem(stem: str) -> str | None:
    """Resolve filename stem → operation name."""
    if stem.endswith(_LEGACY_SUFFIX):
        return stem[: -len(_LEGACY_SUFFIX)]
    match = _CORR_SUFFIX.match(stem)
    if match:
        base = match.group(1)
        if base.endswith(_LEGACY_SUFFIX):
            return base[: -len(_LEGACY_SUFFIX)]
        # operation-service pattern — take part before last hyphen segment if service-like
        if "-service" in base or "-mgmt-" in base:
            return base.rsplit("-", 2)[0] if base.count("-") >= 2 else base.split("-")[0]
        return base
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", stem):
        return stem
    return stem if stem else None


def _canonical_path(directory: Path, operation: str) -> Path:
    return directory / f"{operation}.json"


def prune_payload_dir(directory: Path) -> int:
    """Keep one `{operation}.json` per operation (newest wins)."""
    if not directory.is_dir():
        return 0

    by_op: dict[str, list[Path]] = {}
    for path in directory.glob("*.json"):
        op = _operation_from_stem(path.stem)
        if not op:
            continue
        by_op.setdefault(op, []).append(path)

    removed = 0
    for op, paths in by_op.items():
        keep = max(paths, key=lambda p: p.stat().st_mtime)
        target = _canonical_path(directory, op)
        if keep != target:
            if target.exists():
                target.unlink(missing_ok=True)
                removed += 1
            shutil.move(str(keep), str(target))
        for path in paths:
            if path != target and path.exists():
                path.unlink(missing_ok=True)
                removed += 1
    if removed:
        log.info("Canonicalized %d file(s) in %s", removed, directory)
    return removed


def prune_captured_events(project_root: Path) -> tuple[int, int]:
    raw = prune_payload_dir(payload_raw_dir(project_root))
    enriched = prune_payload_dir(payload_enrich_dir(project_root))
    return raw, enriched


def prepare_payload_capture(project_root: Path) -> None:
    """Wipe payload dirs at run start so each run is a fresh capture set."""
    reset_payload_dirs(project_root)
