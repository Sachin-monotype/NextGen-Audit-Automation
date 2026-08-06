"""Field mappings for cron / scheduler audit operations (from mt-audit-log-resolver-service).

Resolver enrichers call UMS/CMS/Discovery/BYOF/Batch HTTP APIs. Source validation
prefers ``SOURCE_TRUTH=db`` (MySQL) for UMS/CMS/AMS when configured — same field
paths, different client in ``source_validation.db.factory``.
"""

from __future__ import annotations

from .mapping_registry import MappingField

# Shared actor snapshot fields (UMS profile + CMS customer) — most cron enrichers.
# Shared actor snapshot fields (UMS profile + CMS customer) — most cron enrichers.
_ACTOR_UMS_CMS: tuple[MappingField, ...] = (
    MappingField(
        field="Actor profile id",
        node="actor",
        sub_node="enrichedSnapshot.user.profile.id",
        attribute="id",
        data_mapping="UMS profile id",
        notes="actor-enriched-snapshot",
        validate="Y",
        enriched_path="actor.enrichedSnapshot.user.profile.id",
        source_system="UMS",
        source_api="profiles by globalUserId",
        layer="actor",
    ),
    MappingField(
        field="Actor email",
        node="actor",
        sub_node="enrichedSnapshot.user.profile.email",
        attribute="email",
        data_mapping="UMS profile email",
        notes="actor-enriched-snapshot",
        validate="Y",
        enriched_path="actor.enrichedSnapshot.user.profile.email",
        source_system="UMS",
        source_api="profiles by globalUserId",
        layer="actor",
    ),
    MappingField(
        field="Actor firstName",
        node="actor",
        sub_node="enrichedSnapshot.user.profile.firstName",
        attribute="firstName",
        data_mapping="UMS profile firstName",
        notes="actor-enriched-snapshot",
        validate="Y",
        enriched_path="actor.enrichedSnapshot.user.profile.firstName",
        source_system="UMS",
        source_api="profiles by globalUserId",
        layer="actor",
    ),
    MappingField(
        field="Actor lastName",
        node="actor",
        sub_node="enrichedSnapshot.user.profile.lastName",
        attribute="lastName",
        data_mapping="UMS profile lastName",
        notes="actor-enriched-snapshot",
        validate="Y",
        enriched_path="actor.enrichedSnapshot.user.profile.lastName",
        source_system="UMS",
        source_api="profiles by globalUserId",
        layer="actor",
    ),
    MappingField(
        field="Actor customerId",
        node="actor",
        sub_node="enrichedSnapshot.user.profile.customerId",
        attribute="customerId",
        data_mapping="UMS profile customerId",
        notes="actor-enriched-snapshot",
        validate="Y",
        enriched_path="actor.enrichedSnapshot.user.profile.customerId",
        source_system="UMS",
        source_api="profiles by globalUserId",
        layer="actor",
    ),
    MappingField(
        field="Actor customer id",
        node="actor",
        sub_node="enrichedSnapshot.customer.id",
        attribute="id",
        data_mapping="CMS customer id",
        notes="actor-enriched-snapshot",
        validate="Y",
        enriched_path="actor.enrichedSnapshot.customer.id",
        source_system="CMS",
        source_api="customers by globalCustomerId",
        layer="actor",
    ),
    MappingField(
        field="Actor customer name",
        node="actor",
        sub_node="enrichedSnapshot.customer.name",
        attribute="name",
        data_mapping="CMS customer name",
        notes="actor-enriched-snapshot",
        validate="Y",
        enriched_path="actor.enrichedSnapshot.customer.name",
        source_system="CMS",
        source_api="customers by globalCustomerId",
        layer="actor",
    ),
)

# Scheduler passthrough — MT Connect CMS customer on actor (no subject enricher).
_SCHEDULER_ACTOR: tuple[MappingField, ...] = _ACTOR_UMS_CMS + (
    MappingField("xCorrelationId", "event", "xCorrelationId", "", "raw envelope", "", "Y", "xCorrelationId", "Payload", "payload", "event"),
    MappingField("correlationId", "event", "correlationId", "", "raw envelope", "", "Y", "correlationId", "Payload", "payload", "event"),
    MappingField("eventId", "event", "eventId", "", "raw envelope", "", "N", "eventId", "Payload", "payload", "event"),
    MappingField("eventVersion", "event", "eventVersion", "", "raw envelope", "", "N", "eventVersion", "Payload", "payload", "event"),
    MappingField("occurredAt", "event", "occurredAt", "", "raw envelope", "", "N", "occurredAt", "Payload", "payload", "event"),
    MappingField("source.service", "source", "service", "", "raw envelope", "", "Y", "source.service", "Payload", "payload", "event"),
    MappingField("source.operation", "source", "operation", "", "raw envelope", "", "Y", "source.operation", "Payload", "payload", "event"),
    MappingField("source.platform", "source", "platform", "", "raw envelope", "", "Y", "source.platform", "Payload", "payload", "event"),
    MappingField("source.platformEnvironment", "source", "platformEnvironment", "", "raw envelope", "", "Y", "source.platformEnvironment", "Payload", "payload", "event"),
    MappingField("source.platformVersion", "source", "platformVersion", "", "raw envelope", "", "N", "source.platformVersion", "Payload", "payload", "event"),
    MappingField("source.actorUserAgent", "source", "actorUserAgent", "", "raw envelope", "", "Y", "source.actorUserAgent", "Payload", "payload", "event"),
    MappingField("actor.authenticationState", "actor", "authenticationState", "", "raw envelope", "", "Y", "actor.authenticationState", "Payload", "payload", "actor"),
    MappingField("actor.globalUserId", "actor", "globalUserId", "", "raw envelope", "", "Y", "actor.globalUserId", "Payload", "payload", "actor"),
    MappingField("actor.globalCustomerId", "actor", "globalCustomerId", "", "raw envelope", "", "Y", "actor.globalCustomerId", "Payload", "payload", "actor"),
    MappingField("actor.orgId", "actor", "orgId", "", "raw envelope", "", "Y", "actor.orgId", "Payload", "payload", "actor"),
    MappingField("subject.type", "subject", "type", "", "raw envelope", "", "Y", "subject.type", "Payload", "payload", "subject"),
    MappingField("subject.id[0]", "subject", "id[0]", "", "raw envelope", "", "Y", "subject.id[0]", "Payload", "payload", "subject"),
    MappingField("subject.metadata.triggerCode[0]", "subject", "metadata.triggerCode[0]", "", "raw envelope", "", "Y", "subject.metadata.triggerCode[0]", "Payload", "payload", "subject"),
    MappingField("subject.metadata.triggerCodes[0]", "subject", "metadata.triggerCodes[0]", "", "raw envelope", "", "Y", "subject.metadata.triggerCodes[0]", "Payload", "payload", "subject"),
    MappingField("subject.metadata.event", "subject", "metadata.event", "", "raw envelope", "", "Y", "subject.metadata.event", "Payload", "payload", "subject"),
    MappingField("subject.metadata.scheduledAt", "subject", "metadata.scheduledAt", "", "raw envelope", "", "N", "subject.metadata.scheduledAt", "Payload", "payload", "subject"),
    MappingField("subject.metadata.routingKey[0]", "subject", "metadata.routingKey[0]", "", "raw envelope", "", "Y", "subject.metadata.routingKey[0]", "Payload", "payload", "subject"),
    MappingField("subject.metadata.correlationId", "subject", "metadata.correlationId", "", "raw envelope", "", "Y", "subject.metadata.correlationId", "Payload", "payload", "subject"),
    MappingField("subject.metadata.triggeredAt", "subject", "metadata.triggeredAt", "", "raw envelope", "", "N", "subject.metadata.triggeredAt", "Payload", "payload", "subject"),
    MappingField("subject.metadata.signal", "subject", "metadata.signal", "", "raw envelope", "", "N", "subject.metadata.signal", "Payload", "payload", "subject"),
    MappingField("subject.metadata.inDays", "subject", "metadata.inDays", "", "raw envelope", "", "N", "subject.metadata.inDays", "Payload", "payload", "subject"),
    MappingField(
        field="eventSource",
        node="event",
        sub_node="eventSource",
        attribute="eventSource",
        data_mapping="resolver scheduler passthrough",
        notes="scheduler.constant VALID_SCHEDULAR_ROUTING_KEYS",
        validate="N",
        enriched_path="eventSource",
        source_system="Resolver",
        source_api="passthrough",
        layer="event",
    ),
)

# exportCompleted / exportFailed — Batch orchestration + actor (export.enricher.ts).
_EXPORT_COMPLETE: tuple[MappingField, ...] = _ACTOR_UMS_CMS + (
    MappingField(
        field="Batch id",
        node="subject",
        sub_node="batchDetails.batchId",
        attribute="batchId",
        data_mapping="raw subject.batchId",
        notes="batch-orchestration getBatch",
        validate="N",
        enriched_path="subject.batchDetails.batchId",
        source_system="Trigger",
        source_api="raw envelope",
        layer="subject",
    ),
    MappingField(
        field="Export row count",
        node="subject",
        sub_node="rowCount",
        attribute="rowCount",
        data_mapping="raw subject.rowCount",
        notes="export enricher preserves subject",
        validate="N",
        enriched_path="subject.rowCount",
        source_system="Trigger",
        source_api="raw envelope",
        layer="subject",
    ),
)

# fontSyncFailure — Discovery variations + service-account actor (FontBridge enricher).
_FONT_SYNC_FAILURE: tuple[MappingField, ...] = _ACTOR_UMS_CMS + (
    MappingField(
        field="Font style name",
        node="subject",
        sub_node="enrichedSnapshot.fontDetails[0].styles[0].name",
        attribute="name",
        data_mapping="Discovery variation by md5",
        notes="fontSyncFailure enricher",
        validate="Y",
        enriched_path="subject.enrichedSnapshot.fontDetails[0].styles[0].name",
        source_system="Typesense",
        source_api="GET /v1/variations",
        layer="subject",
    ),
)

# BYOF notifyByofLicenceExpiry — BYOF contract + Discovery styles.
_BYOF_LICENCE_EXPIRY: tuple[MappingField, ...] = _ACTOR_UMS_CMS + (
    MappingField(
        field="Contract id",
        node="subject",
        sub_node="contract.contractId",
        attribute="contractId",
        data_mapping="BYOF licence contract",
        notes="notifyByofLicenceExpiry enricher",
        validate="Y",
        enriched_path="subject.contract.contractId",
        source_system="BYOF",
        source_api="GET contract by id",
        layer="subject",
    ),
)

# Bulk font sync complete — Discovery + batch orchestration.
_BULK_FONT_SYNC: tuple[MappingField, ...] = _ACTOR_UMS_CMS + (
    MappingField(
        field="Batch id",
        node="subject",
        sub_node="batchId",
        attribute="batchId",
        data_mapping="raw subject.batchId",
        notes="bulkActivate/DeactivateComplete enricher",
        validate="N",
        enriched_path="subject.batchId",
        source_system="Trigger",
        source_api="raw envelope",
        layer="subject",
    ),
)

# licenseLinked — Discovery font-import + actor.
_LICENSE_LINKED: tuple[MappingField, ...] = _ACTOR_UMS_CMS

# Actor-only service ops (bulk BYOF, process upload, etc.)
_ACTOR_ONLY: tuple[MappingField, ...] = _ACTOR_UMS_CMS

# Per resolver operation (base name — case suffix stripped at lookup time).
CRON_OPERATION_MAPPINGS: dict[str, tuple[MappingField, ...]] = {
    # Scheduler passthrough / cron operations
    "quarterlyReportNotification": _SCHEDULER_ACTOR,
    "subscriptionExpiryNotification": _SCHEDULER_ACTOR,
    "weekly_account_expiry": _SCHEDULER_ACTOR,
    "weekly_account_expiry_digest": _SCHEDULER_ACTOR,
    "tokenExpiring": _SCHEDULER_ACTOR,
    "tokenExpiringSuspended": _SCHEDULER_ACTOR,
    "projectArchivalWarningAdmin": _SCHEDULER_ACTOR,
    "projectArchivalWarningMember": _SCHEDULER_ACTOR,
    "fontLeavingCatalogue": _SCHEDULER_ACTOR,
    "font_leaving_catalogue": _SCHEDULER_ACTOR,
    "fontBridgeAuthFailed": _SCHEDULER_ACTOR,
    "byofLicenceExpired": _SCHEDULER_ACTOR,
    "byofFontNoLicense": _SCHEDULER_ACTOR,
    "subscription.fonts.deactivated": _SCHEDULER_ACTOR,
    "auto_deactivated_user": _SCHEDULER_ACTOR,
    "userAccountAccepted": _SCHEDULER_ACTOR,
    "user_invitation_expired": _SCHEDULER_ACTOR,
    # New cron operations from trigger automation / lasttrigeerRun.xlsx
    "ums-user-invitation-expired": _SCHEDULER_ACTOR,
    "ums-user-auto-deactivated": _SCHEDULER_ACTOR,
    "ums-user-account-expiring": _SCHEDULER_ACTOR,
    "ums-weekly-account-expiry-digest": _SCHEDULER_ACTOR,
    "lfus-font-leaving-catalogue": _SCHEDULER_ACTOR,
    "lfus-pending-retention-reminder": _SCHEDULER_ACTOR,
    "lfus-monthly-digest": _SCHEDULER_ACTOR,
    "ams-project-archival-warning": _SCHEDULER_ACTOR,
    "mt-login-server-token-expiry": _SCHEDULER_ACTOR,
    "mt-login-server-token-expiry-s2": _SCHEDULER_ACTOR,
    "mt-login-server-token-expiry-s5": _SCHEDULER_ACTOR,
    "byof-missing-licence-import": _SCHEDULER_ACTOR,
    "license-subscription-expiry": _SCHEDULER_ACTOR,
    "license-subscription-expiry-7": _SCHEDULER_ACTOR,
    "license-quarterly-reporting": _SCHEDULER_ACTOR,
    "license-quarterly-reporting-open": _SCHEDULER_ACTOR,
    "license-quarterly-reporting-intimation": _SCHEDULER_ACTOR,
    "license-quarterly-reporting-closing7days": _SCHEDULER_ACTOR,
    "license-quarterly-reporting-closing2days": _SCHEDULER_ACTOR,
    "license-seat-limit": _SCHEDULER_ACTOR,
    "license-seat-limit-warning": _SCHEDULER_ACTOR,
    "license-seat-limit-exceeded": _SCHEDULER_ACTOR,
    "customer-company-expiry-warning": _SCHEDULER_ACTOR,
    # Resolver enrichers
    "exportCompleted": _EXPORT_COMPLETE,
    "exportFailed": _EXPORT_COMPLETE,
    "fontSyncFailure": _FONT_SYNC_FAILURE,
    "notifyByofLicenceExpiry": _BYOF_LICENCE_EXPIRY,
    "bulkActivateComplete": _BULK_FONT_SYNC,
    "bulkDeactivateComplete": _BULK_FONT_SYNC,
    "bulkMarkAsProductionFontsRequest": _ACTOR_ONLY,
    "byofFontDeleteComplete": _ACTOR_ONLY,
    "licenseLinked": _LICENSE_LINKED,
    "processUploadSessionFonts": _ACTOR_ONLY,
}


def cron_mapping_for_operation(operation: str) -> list[MappingField] | None:
    from ..case_keys import parse_display_operation

    base, _case = parse_display_operation(operation)
    rows = CRON_OPERATION_MAPPINGS.get(operation) or CRON_OPERATION_MAPPINGS.get(base)
    if not rows:
        norm_base = base.lower().replace("_", "-")
        norm_op = operation.lower().replace("_", "-")
        for k, v in CRON_OPERATION_MAPPINGS.items():
            k_norm = k.lower().replace("_", "-")
            if k_norm == norm_op or k_norm == norm_base:
                rows = v
                break
    return list(rows) if rows else None

