"""Delete notification rows created during automation runs."""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)


def _db_config() -> dict[str, str | int] | None:
    host = os.getenv("NOTIFICATION_DB_HOST", "").strip()
    user = os.getenv("NOTIFICATION_DB_USER", "").strip()
    password = os.getenv("NOTIFICATION_DB_PASSWORD", "")
    database = os.getenv("NOTIFICATION_DB_NAME", "").strip()
    if not all([host, user, database]):
        log.info(
            "Notification DB not configured — set NOTIFICATION_DB_HOST, "
            "NOTIFICATION_DB_USER, NOTIFICATION_DB_PASSWORD, NOTIFICATION_DB_NAME"
        )
        return None
    port = int(os.getenv("NOTIFICATION_DB_PORT", "3306"))
    return {
        "host": host,
        "user": user,
        "password": password,
        "database": database,
        "port": port,
    }


def delete_notifications_for_user(user_id: str) -> int:
    """DELETE FROM notifications WHERE user_id = %s"""
    if not user_id:
        return 0

    cfg = _db_config()
    if not cfg:
        return 0

    try:
        import pymysql
    except ImportError as exc:
        raise RuntimeError("pymysql is required for notification DB cleanup") from exc

    conn = pymysql.connect(
        host=str(cfg["host"]),
        user=str(cfg["user"]),
        password=str(cfg["password"]),
        database=str(cfg["database"]),
        port=int(cfg["port"]),
        connect_timeout=15,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM notifications WHERE user_id = %s", (user_id,))
            deleted = cur.rowcount
        conn.commit()
        log.info(
            "Deleted %d notification row(s) for user_id=%s in %s",
            deleted,
            user_id,
            cfg["database"],
        )
        return deleted
    finally:
        conn.close()
