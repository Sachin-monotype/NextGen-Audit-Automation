"""Stable keys for cron/ingress cases — one correlation + one payload file per case."""

from __future__ import annotations

import re

_CRON_PREFIX = "cron:"
_INGRESS_PREFIX = "ingress:"
_CASE_STEM_SEP = "__"
_DISPLAY_RE = re.compile(r"^(.+)\(([a-zA-Z0-9_.-]+)\)$")


def cron_case_key(case_id: str) -> str:
    cid = (case_id or "").strip()
    if cid.startswith(_CRON_PREFIX):
        return cid
    return f"{_CRON_PREFIX}{cid}"


def ingress_case_key(case_id: str) -> str:
    cid = (case_id or "").strip()
    if cid.startswith(_INGRESS_PREFIX):
        return cid
    return f"{_INGRESS_PREFIX}{cid}"


def cron_display_operation(operation: str, case_id: str) -> str:
    """Compare / staging label: ``quarterlyReportNotification(lmsopen)``."""
    op = (operation or "").strip()
    cid = (case_id or "").strip()
    if not op:
        return cid
    if not cid or op == cid:
        return op
    return f"{op}({cid})"


def cron_staging_stem(operation: str, case_id: str) -> str:
    """Filesystem stem under payload/raw|enrich: ``op__case_id``."""
    op = (operation or "").strip()
    cid = (case_id or "").strip()
    if not cid or op == cid:
        return op or cid
    return f"{op}{_CASE_STEM_SEP}{cid}"


def parse_display_operation(label: str) -> tuple[str, str | None]:
    """``activateFamily(global)`` or ``quarterlyReportNotification(lmsopen)`` → base + suffix."""
    raw = (label or "").strip()
    if not raw:
        return "", None
    m = _DISPLAY_RE.match(raw)
    if m:
        return m.group(1).strip(), m.group(2).strip() or None
    if _CASE_STEM_SEP in raw:
        op, cid = raw.split(_CASE_STEM_SEP, 1)
        return op.strip(), cid.strip() or None
    return raw, None


def is_case_scoped_label(label: str) -> bool:
    base, suffix = parse_display_operation(label)
    return bool(suffix and base)
