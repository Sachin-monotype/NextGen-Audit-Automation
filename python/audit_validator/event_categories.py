"""Event categorisation for the NextGen UI filters.

Primary source: ``docs/UI Navigation of Event (2).xlsx`` (first sheet), loaded into
``data/ui_navigation.json`` as ``section`` per operation. Falls back to resolver
routing-key heuristics only when the sheet has no section for that op.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .rabbitmq.resolver_routing_map import OPERATION_TO_ROUTING_KEY

# Ordered category labels (matches the in-app notification preference groups /
# UI Navigation sheet section headers).
CATEGORIES: list[str] = [
    "Font Sync & activation",
    "Imported font Access Requests",
    "Imported font Compliance",
    "Project Compliance",
    "Project lifecycle & membership",
    "Library & font changes",
    "User & Access",
    "Account & workspace",
    "Exports & maintenance",
    "Other",
]

_PKG_DATA = Path(__file__).resolve().parent / "data" / "ui_navigation.json"

# Cron / scheduler operations that are published by their origin services with
# routing keys not present in the resolver's outbound map.
_CRON_ROUTING_KEYS: dict[str, str] = {
    "weekly_account_expiry": "user.account.expiring",
    "weekly_account_expiry_digest": "user.accounts.digest",
    "fontLeavingCatalogue": "font.leaving.catalogue",
    "font_leaving_catalogue": "font.leaving.catalogue",
    "tokenExpiring": "server.token.expiring",
    "tokenExpiringSuspended": "server.token.expiring.suspended",
    "projectArchivalWarningAdmin": "project.archival.warning.admin",
    "projectArchivalWarningMember": "project.archival.warning.member",
    "quarterlyReportNotification": "reporting.window.open",
    "subscriptionExpiryNotification": "subscription.contract.expiry",
    "fontBridgeAuthFailed": "fontbridge.auth.failed",
    "fontSyncFailure": "font.sync.failure",
    "byofLicenceExpired": "byof.licence.expired",
    "byofFontNoLicense": "byof.font.nolicense",
    "subscription.fonts.deactivated": "subscription.fonts.deactivated",
    "auto_deactivated_user": "user.account.deactivated",
    "userAccountAccepted": "user.invitation.accepted",
    "user_invitation_expired": "user.invitation.expired",
}

# Sheet section labels → canonical CATEGORIES entry (casing / aliases).
_SECTION_ALIASES: dict[str, str] = {
    "font sync & activation": "Font Sync & activation",
    "font activations": "Font Sync & activation",
    "imported font access requests": "Imported font Access Requests",
    "imported font compliance": "Imported font Compliance",
    "project compliance": "Project Compliance",
    "project lifecycle & membership": "Project lifecycle & membership",
    "library & font changes": "Library & font changes",
    "user & access": "User & Access",
    "account & workspace": "Account & workspace",
    "exports & maintenance": "Exports & maintenance",
    # Extra sheet sections not shown as top-level UI filters → nearest bucket
    "desktop app preferences": "Exports & maintenance",
    "plugin events": "Font Sync & activation",
    "login & identity": "User & Access",
}


def routing_key_for(operation: str) -> str:
    return OPERATION_TO_ROUTING_KEY.get(operation) or _CRON_ROUTING_KEYS.get(operation, "")


def _normalize_section(section: str) -> str | None:
    raw = (section or "").strip()
    if not raw:
        return None
    if raw in CATEGORIES:
        return raw
    return _SECTION_ALIASES.get(raw.lower())


@lru_cache(maxsize=1)
def _ui_navigation_sections() -> dict[str, str]:
    """operation → canonical category from UI Navigation Excel export."""
    if not _PKG_DATA.is_file():
        return {}
    try:
        raw = json.loads(_PKG_DATA.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for op, meta in raw.items():
        if not isinstance(meta, dict):
            continue
        cat = _normalize_section(str(meta.get("section") or ""))
        if cat:
            out[str(op)] = cat
    return out


def _category_for_routing_key(rk: str) -> str | None:
    if not rk:
        return None

    # Reads/exports first so *_retrieved / *_exported don't fall into Library.
    if (
        rk.endswith("_retrieved")
        or rk.endswith("_exported")
        or rk.startswith(("font.template", "font.download", "font.upload_session"))
        or rk.startswith(("document.scan", "document.metadata", "font.document"))
        or rk.startswith(("app.logs", "reporting.window"))
    ):
        return "Exports & maintenance"

    if rk.startswith("byof.access"):
        return "Imported font Access Requests"
    if rk.startswith(("byof.", "importedfont")):
        return "Imported font Compliance"

    if rk.startswith("project.archival"):
        return "Project Compliance"
    if rk.startswith(("project.", "library.project")):
        return "Project lifecycle & membership"

    if rk.startswith(("font.activation", "font.deactivation", "font.sync")):
        return "Font Sync & activation"

    if rk.startswith(
        (
            "user.role",
            "user.team",
            "user.invitation",
            "user.profile",
            "user.password",
            "user.account",
            "user.accounts",
            "user.tag",
            "user.locale",
            "user.sso",
            "account.sso",
            "sso.mapping",
            "server.token",
        )
    ):
        return "User & Access"

    if rk.startswith("fontbridge"):
        return "User & Access"

    if rk.startswith(("account.", "server.account", "subscription.", "user.notification")):
        return "Account & workspace"

    if rk.startswith(
        (
            "library.",
            "font.favorite",
            "fontpair.",
            "font.favorite_pair",
            "font.private_tag",
            "font.production",
            "font.glyph",
            "font.style",
            "font.licence",
            "font.addon",
            "font.pairs",
            "font.similar_font",
            "font.batch",
            "font.import",
            "font.access",
        )
    ):
        return "Library & font changes"

    return None


def resolve_category(operation: str) -> str:
    """Best-effort category for an operation (Excel section first)."""
    # Touchpoint display names: activateFamily(global) → activateFamily
    base = operation.split("(", 1)[0].strip() if "(" in operation else operation

    sections = _ui_navigation_sections()
    if base in sections:
        return sections[base]
    if operation in sections:
        return sections[operation]

    cat = _category_for_routing_key(routing_key_for(base)) or _category_for_routing_key(
        routing_key_for(operation)
    )
    if cat:
        return cat

    op = base.lower()
    if "byof" in op or "importedfont" in op or "contract" in op or "licence" in op or "license" in op:
        return "Imported font Compliance"
    if "project" in op:
        return "Project lifecycle & membership"
    if any(t in op for t in ("activate", "deactivate", "sync")):
        return "Font Sync & activation"
    if any(t in op for t in ("role", "team", "profile", "invitation", "sso", "password", "user")):
        return "User & Access"
    if any(
        t in op
        for t in (
            "customer",
            "serviceaccount",
            "companylogo",
            "onboarding",
            "preference",
            "notification",
            "subscription",
            "token",
        )
    ):
        return "Account & workspace"
    if op.startswith("get") or "export" in op or "download" in op:
        return "Exports & maintenance"
    if any(
        t in op
        for t in (
            "asset",
            "favorite",
            "tag",
            "font",
            "style",
            "list",
            "webproject",
            "glyph",
            "production",
        )
    ):
        return "Library & font changes"
    return "Other"


def category_by_operation(operations: list[str]) -> dict[str, str]:
    return {op: resolve_category(op) for op in operations}


def category_report(operations: list[str]) -> dict[str, object]:
    by_op = category_by_operation(operations)
    counts: dict[str, int] = {c: 0 for c in CATEGORIES}
    for cat in by_op.values():
        counts[cat] = counts.get(cat, 0) + 1
    return {
        "categories": CATEGORIES,
        "by_operation": by_op,
        "counts": counts,
    }
