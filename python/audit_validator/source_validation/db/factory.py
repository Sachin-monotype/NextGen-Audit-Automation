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
    """
    mode = source_truth_mode()
    if mode == "db":
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
            # Mark cfg-ready paths: db clients don't use ums_base_url, but runner
            # still gates on cfg.ums_ready / cms_ready / ams_ready. Those stay True
            # when HTTP URLs are configured (normal .env). If URLs missing, force
            # ready by still returning clients — runner checks cfg.*_ready.
            return ums, cms, ams, "db"

    ums = UmsClient(cfg) if cfg.ums_ready else None
    cms = CmsClient(cfg) if cfg.cms_ready else None
    ams = AmsClient(cfg) if cfg.ams_ready else None
    return ums, cms, ams, "api"
