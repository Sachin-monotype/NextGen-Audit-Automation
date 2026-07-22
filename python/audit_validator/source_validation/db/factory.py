"""Choose HTTP vs MySQL clients for UMS / CMS / AMS."""

from __future__ import annotations

import logging
import os
from typing import Any

from ..clients import AmsClient, CmsClient, UmsClient
from ..config import SourceValidationConfig
from .clients import AmsDbClient, CmsDbClient, UmsDbClient
from .connection import load_mysql_config, mysql_ready

log = logging.getLogger(__name__)


def source_truth_mode() -> str:
    """``api`` (default) | ``db`` — also accepts SOURCE_VALIDATION_TRUTH."""
    raw = (
        os.getenv("SOURCE_TRUTH")
        or os.getenv("SOURCE_VALIDATION_TRUTH")
        or "api"
    ).strip().lower()
    return "db" if raw in {"db", "mysql", "sql"} else "api"


def build_ums_cms_ams_clients(
    cfg: SourceValidationConfig,
) -> tuple[Any | None, Any | None, Any | None, str]:
    """Return ``(ums, cms, ams, truth_mode)``.

    When ``SOURCE_TRUTH=db`` and MySQL env is set, swap UMS/CMS/AMS to DB clients.
    Discovery/Typesense stays on HTTP either way.
    Falls back to HTTP API clients when pymysql is missing or MySQL is unreachable.
    """
    mode = source_truth_mode()
    if mode == "db":
        try:
            import pymysql  # noqa: F401
        except ImportError:
            log.warning(
                "SOURCE_TRUTH=db but pymysql is not installed — falling back to API clients. "
                "Install with: backend/.venv/bin/pip install pymysql"
            )
            mode = "api"
        else:
            mysql = load_mysql_config()
            if not mysql_ready(mysql):
                log.warning(
                    "SOURCE_TRUTH=db but MYSQL_* not configured — falling back to API clients"
                )
                mode = "api"
            else:
                log.info(
                    "Source truth: MySQL (%s) for UMS/CMS/AMS — Typesense still HTTP",
                    mysql.host,
                )
                ums = UmsDbClient(mysql)
                cms = CmsDbClient(mysql)
                ams = AmsDbClient(mysql)
                return ums, cms, ams, "db"

    ums = UmsClient(cfg) if cfg.ums_ready else None
    cms = CmsClient(cfg) if cfg.cms_ready else None
    ams = AmsClient(cfg) if cfg.ams_ready else None
    return ums, cms, ams, "api"
