"""
Routing key registries for enriched event validation.

Primary (audit resolver): RESOLVER_ROUTING_KEYS from outbound-routing-map.json
  Branch: mt-audit-log-resolver-service / m2m_node_cache_temp

Secondary (notification queue): NOTIFICATION_ROUTING_KEYS from preprod RabbitMQ UI
  Queue: mt.platform.events.notification_automation.queue (legacy; same exchange keys)
  Includes subscription/reporting/BYOF warning keys from other publishers.

Platform tap queue for E2E: mt.platform.resolver.enrichpayload (paired with rawpayload on mt-connect).
"""

from __future__ import annotations

from .resolver_routing_map import (
    RESOLVER_ROUTING_KEYS,
    RESOLVER_ROUTING_KEYS_LIST,
)

# Default expected keys for audit resolver E2E validation
ENRICHED_ROUTING_KEYS: frozenset[str] = RESOLVER_ROUTING_KEYS
ENRICHED_ROUTING_KEYS_LIST: list[str] = RESOLVER_ROUTING_KEYS_LIST

# Preprod notification_automation.queue bindings (non-resolver publishers)
NOTIFICATION_ROUTING_KEYS: frozenset[str] = frozenset(
    {
        "byof.access.requested",
        "byof.access.resolved",
        "byof.font.nolicense",
        "byof.font.unlicensed",
        "byof.licence.expired",
        "byof.licence.expiring",
        "byof.licence.expiry",
        "byof.licence.overused",
        "company.subscription.expiring",
        "font.agent.disconnected",
        "font.catalogue.removed",
        "font.conflict.resolved",
        "font.leaving.catalogue",
        "font.sync.failed",
        "fontbridge.accounts.limitreached",
        "fontbridge.auth.failed",
        "fontbridge.sync.failed",
        "library.list.shared",
        "library.list.updated",
        "platform.maintenance.scheduled",
        "project.archival.warning.admin",
        "project.archival.warning.member",
        "project.byof.expired",
        "project.byof.overused",
        "project.byof.unlicensed",
        "project.byof.violated",
        "project.byof.warning",
        "project.fonts.activated",
        "project.member.added",
        "project.member.removed",
        "project.production.marked",
        "project.production.requested",
        "project.state.archived",
        "reporting.quarterly.submitted",
        "reporting.window.closing",
        "reporting.window.final",
        "reporting.window.open",
        "server.token.expiring",
        "server.token.expiring.suspended",
        "subscription.contract.expiry",
        "subscription.fonts.deactivated",
        "subscription.pageviews.warning",
        "subscription.production.limitreached",
        "subscription.seats.limitreached",
        "subscription.seats.warning",
        "user.account.deactivated",
        "user.account.expiring",
        "user.accounts.digest",
        "user.import.completed",
        "user.invitation.accepted",
        "user.invitation.expired",
    }
)

NOTIFICATION_ROUTING_KEYS_LIST: list[str] = sorted(NOTIFICATION_ROUTING_KEYS)
