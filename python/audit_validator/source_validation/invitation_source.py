"""Fetch user invitation rows for Compare (MySQL user_management.user_invitation)."""

from __future__ import annotations

from typing import Any

from ..models import JsonDict
from .config import SourceValidationConfig


def invitation_email_from_enriched(enriched: JsonDict) -> str | None:
    """Prefer enriched snapshot email (updateUserInvitations) over mutation input."""
    subject = enriched.get("subject") or {}
    snap = subject.get("enrichedSnapshot") or {}
    invs = snap.get("invitations") or []
    if isinstance(invs, list):
        for inv in invs:
            if isinstance(inv, dict) and inv.get("email"):
                return str(inv["email"]).strip()
    meta = subject.get("metadata") or {}
    inp = meta.get("input") if isinstance(meta, dict) else {}
    if isinstance(inp, dict):
        data = inp.get("data") or []
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                emails = item.get("emails")
                if isinstance(emails, list) and emails:
                    return str(emails[0]).strip()
                if item.get("email"):
                    return str(item["email"]).strip()
    if subject.get("email"):
        return str(subject["email"]).strip()
    return None


def invitation_id_from_enriched(enriched: JsonDict) -> str | None:
    subject = enriched.get("subject") or {}
    snap = subject.get("enrichedSnapshot") or {}
    invs = snap.get("invitations") or []
    if isinstance(invs, list):
        for inv in invs:
            if isinstance(inv, dict):
                for key in ("id", "invitationId"):
                    val = inv.get(key)
                    if val not in (None, "", []):
                        return str(val).strip()
    meta = subject.get("metadata") or {}
    inp = meta.get("input") if isinstance(meta, dict) else {}
    if isinstance(inp, dict):
        data = inp.get("data") or []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    iid = item.get("invitationId") or item.get("id")
                    if iid not in (None, "", []):
                        return str(iid).strip()
    sid = subject.get("id")
    if isinstance(sid, list) and sid:
        return str(sid[0]).strip()
    if sid not in (None, "", []):
        return str(sid).strip()
    return None


def fetch_invitation_for_enriched(
    enriched: JsonDict,
    *,
    customer_id: str = "",
    cfg: SourceValidationConfig | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Return (invitation row, error note). Uses MySQL when MYSQL_* is configured."""
    from .db.clients import UmsDbClient
    from .db.connection import load_mysql_config, mysql_ready

    if cfg is None:
        from .config import load_source_validation_config

        cfg = load_source_validation_config()

    if not cfg.mysql_source_ready:
        return None, "MYSQL_HOST/USER/PASSWORD not set — invitation compare needs MySQL"

    mysql = load_mysql_config()
    if not mysql_ready(mysql):
        return None, "MySQL not reachable — check VPN and MYSQL_* in .env"

    email = invitation_email_from_enriched(enriched)
    inv_id = invitation_id_from_enriched(enriched)
    gcid = customer_id or str((enriched.get("actor") or {}).get("globalCustomerId") or cfg.gcid or "")

    try:
        client = UmsDbClient(mysql)
        if email:
            row = client.get_invitation_by_email(email, gcid, correlation_id="invitation-compare")
            if row:
                return row, ""
        if inv_id:
            row = client.get_invitation_by_id(inv_id, correlation_id="invitation-compare")
            if row:
                return row, ""
    except Exception as exc:  # noqa: BLE001
        return None, f"MySQL user_invitation lookup failed: {exc}"

    return None, f"No row in user_management.user_invitation for email={email!r} id={inv_id!r}"
