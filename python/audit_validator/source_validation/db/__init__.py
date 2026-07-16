"""MySQL source-of-truth clients for CMS / UMS / AMS (SELECT-only).

Why
---
Calling the same HTTP APIs the enricher uses mostly proves API↔enriched parity.
Reading MySQL (the tables those APIs sit on) is an independent ground truth and
supports bulk ``IN (...)`` lookups across many events.

Toggle with ``SOURCE_TRUTH=db`` (default remains ``api``).
"""

from __future__ import annotations

from .clients import AmsDbClient, CmsDbClient, UmsDbClient
from .connection import MysqlConfig, get_connection, load_mysql_config, mysql_ready
from .factory import build_ums_cms_ams_clients

__all__ = [
    "AmsDbClient",
    "CmsDbClient",
    "MysqlConfig",
    "UmsDbClient",
    "build_ums_cms_ams_clients",
    "get_connection",
    "load_mysql_config",
    "mysql_ready",
]
