"""Disk cache for Discovery + identity prefetch (speeds full 170+ compares).

First full run still hits Typesense/MySQL; subsequent runs within ``TTL_SEC``
reuse pickled payloads under ``reports/source-cache/``.
"""

from __future__ import annotations

import hashlib
import logging
import pickle
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

TTL_SEC = 45 * 60  # 45 minutes


def _cache_dir(project_root: Path) -> Path:
    d = project_root / "reports" / "source-cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _key_hash(parts: list[str]) -> str:
    blob = "\n".join(parts).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:24]


def load_pickle(project_root: Path, name: str, key_parts: list[str]) -> Any | None:
    path = _cache_dir(project_root) / f"{name}_{_key_hash(key_parts)}.pkl"
    if not path.is_file():
        return None
    age = time.time() - path.stat().st_mtime
    if age > TTL_SEC:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    try:
        with path.open("rb") as fh:
            data = pickle.load(fh)  # noqa: S301 — local trusted cache only
        log.info("Source cache HIT %s (age %.0fs)", path.name, age)
        return data
    except Exception as exc:  # noqa: BLE001
        log.warning("Source cache read failed %s: %s", path, exc)
        return None


def save_pickle(project_root: Path, name: str, key_parts: list[str], payload: Any) -> None:
    path = _cache_dir(project_root) / f"{name}_{_key_hash(key_parts)}.pkl"
    try:
        tmp = path.with_suffix(".tmp")
        with tmp.open("wb") as fh:
            pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(path)
        log.info("Source cache SAVE %s", path.name)
    except Exception as exc:  # noqa: BLE001
        log.warning("Source cache write failed %s: %s", path, exc)
