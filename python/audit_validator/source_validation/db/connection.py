"""SELECT-only MySQL connection for CMS / UMS / AMS schemas.

PRECAUTION: this module only issues SELECT / SHOW / DESCRIBE. Never INSERT/UPDATE/DELETE.
"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

log = logging.getLogger(__name__)

_FORBIDDEN = (
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "truncate",
    "create",
    "replace",
    "grant",
    "revoke",
    "call",
    "load",
)

# When set, ``get_connection`` reuses one open connection (Compare identity prefetch).
_tls = threading.local()


@dataclass(frozen=True)
class MysqlConfig:
    host: str
    port: int
    user: str
    password: str
    use_ssl: bool = True
    connect_timeout: int = 15
    read_timeout: int = 30

    @property
    def ready(self) -> bool:
        return bool(self.host and self.user and self.password)


def load_mysql_config() -> MysqlConfig:
    return MysqlConfig(
        host=(os.getenv("MYSQL_HOST") or "").strip(),
        port=int(os.getenv("MYSQL_PORT") or "3306"),
        user=(os.getenv("MYSQL_USER") or "").strip(),
        password=(os.getenv("MYSQL_PASSWORD") or "").strip(),
        use_ssl=(os.getenv("MYSQL_SSL", "true").strip().lower() not in ("0", "false", "no")),
        connect_timeout=int(os.getenv("MYSQL_CONNECT_TIMEOUT_SEC") or "15"),
        read_timeout=int(os.getenv("MYSQL_READ_TIMEOUT_SEC") or "30"),
    )


def mysql_ready(cfg: MysqlConfig | None = None) -> bool:
    return (cfg or load_mysql_config()).ready


def connect(cfg: MysqlConfig | None = None):
    """Open a pymysql connection (DictCursor). Raises on auth/network failure."""
    import pymysql
    from pymysql.cursors import DictCursor

    c = cfg or load_mysql_config()
    if not c.ready:
        raise RuntimeError(
            "MySQL not configured — set MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD "
            "(and optionally MYSQL_PORT / MYSQL_SSL)."
        )
    kwargs: dict[str, Any] = {
        "host": c.host,
        "port": c.port,
        "user": c.user,
        "password": c.password,
        "connect_timeout": c.connect_timeout,
        "read_timeout": c.read_timeout,
        "charset": "utf8mb4",
        "cursorclass": DictCursor,
        "autocommit": True,
    }
    # Match MTConnectAutomation utilities/db_helper.py: ssl={"ssl": {}}.
    # A full SSLContext with CERT_NONE still auth'd, but this form is what PP/Asteria uses.
    if c.use_ssl:
        kwargs["ssl"] = {"ssl": {}}
    return pymysql.connect(**kwargs)


def assert_select_only(sql: str) -> None:
    """Hard guard — refuses anything that isn't a read."""
    head = (sql or "").lstrip().lower()
    # Allow leading comments
    while head.startswith("--") or head.startswith("/*"):
        if head.startswith("--"):
            nl = head.find("\n")
            head = head[nl + 1 :].lstrip() if nl >= 0 else ""
        else:
            end = head.find("*/")
            head = head[end + 2 :].lstrip() if end >= 0 else ""
    first = head.split(None, 1)[0] if head else ""
    if first not in {"select", "show", "describe", "desc", "explain", "with"}:
        raise PermissionError(f"Refusing non-SELECT SQL (got leading token {first!r})")
    # Also scan for dangerous keywords as whole statements (naive but safe for our use).
    lowered = " " + head.replace("\n", " ") + " "
    for bad in _FORBIDDEN:
        if f" {bad} " in lowered:
            # WITH … SELECT is ok; INSERT INTO is not.
            if bad == "with":
                continue
            # "created_by" etc. contain no bare keyword thanks to spaces padding —
            # but "update" as column shouldn't appear as " update ".
            raise PermissionError(f"Refusing SQL containing '{bad}'")


@contextmanager
def shared_connection(cfg: MysqlConfig | None = None) -> Iterator[Any]:
    """Hold one MySQL connection for a batch of SELECT calls (avoids SSL handshake per query)."""
    conn = connect(cfg)
    prev = getattr(_tls, "conn", None)
    _tls.conn = conn
    try:
        yield conn
    finally:
        _tls.conn = prev
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


@contextmanager
def get_connection(cfg: MysqlConfig | None = None) -> Iterator[Any]:
    reused = getattr(_tls, "conn", None)
    if reused is not None:
        yield reused
        return
    conn = connect(cfg)
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def select_all(sql: str, params: tuple | list | None = None, *, cfg: MysqlConfig | None = None) -> list[dict]:
    assert_select_only(sql)
    with get_connection(cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            rows = cur.fetchall()
            return [dict(r) for r in rows] if rows else []


def select_one(sql: str, params: tuple | list | None = None, *, cfg: MysqlConfig | None = None) -> dict | None:
    rows = select_all(sql, params, cfg=cfg)
    return rows[0] if rows else None
