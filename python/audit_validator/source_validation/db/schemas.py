"""MySQL schema names for CMS / UMS / AMS — PP vs QA.

QA stores mirror tables under ``*_nextgenqa`` schemas, e.g.::

    user_management.user_invitation          # PP
    user_management_nextgenqa.user_invitation  # QA
"""

from __future__ import annotations

import os

_BASE_SCHEMAS = {
    "ums": "user_management",
    "cms": "customer_management",
    "ams": "asset_management",
}


def mysql_schema_suffix() -> str:
    """Suffix appended to schema names for the active audit target."""
    target = (os.getenv("AUDIT_TARGET") or "pp").strip().lower()
    if target == "qa":
        return (os.getenv("MYSQL_QA_SCHEMA_SUFFIX") or "_nextgenqa").strip()
    return (os.getenv("MYSQL_SCHEMA_SUFFIX") or "").strip()


def mysql_schema(kind: str) -> str:
    """Resolve schema for ``ums`` / ``cms`` / ``ams`` (or a raw base name)."""
    key = (kind or "").strip().lower()
    base = _BASE_SCHEMAS.get(key, kind.strip())
    return f"{base}{mysql_schema_suffix()}"


def ums_schema() -> str:
    return mysql_schema("ums")


def cms_schema() -> str:
    return mysql_schema("cms")


def ams_schema() -> str:
    return mysql_schema("ams")
