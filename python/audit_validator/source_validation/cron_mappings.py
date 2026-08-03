"""Field mappings for cron / scheduler audit operations (from mt-audit-log-resolver-service).

Resolver enrichers call UMS/CMS/Discovery/BYOF/Batch HTTP APIs. Source validation
prefers ``SOURCE_TRUTH=db`` (MySQL) for UMS/CMS/AMS when configured — same field
paths, different client in ``source_validation.db.factory``.
"""

from __future__ import annotations

from .mapping_registry import MappingField

# Shared actor snapshot fields (UMS profile + CMS customer) — most cron enrichers.
_ACTOR_UMS_CMS: tuple[MappingField, ...] = (
    MappingField(
        field="Actor email",
        node="actor",
        sub_node="enrichedSnapshot.email",
        attribute="email",
        data_mapping="UMS profile email",
        notes="actor-enriched-snapshot",
        validate="Y",
        enriched_path="actor.enrichedSnapshot.email",
        source_system="UMS",
        source_api="profiles by globalUserId",
        layer="actor",
    ),
    MappingField(
        field="Actor customer",
        node="actor",
        sub_node="enrichedSnapshot.customer.displayName",
        attribute="displayName",
        data_mapping="CMS customer displayName",
        notes="actor-enriched-snapshot",
        validate="Y",
        enriched_path="actor.enrichedSnapshot.customer.displayName",
        source_system="CMS",
        source_api="customers by globalCustomerId",
        layer="actor",
    ),
)

# Scheduler passthrough — MT Connect CMS customer on actor (no subject enricher).
_SCHEDULER_ACTOR: tuple[MappingField, ...] = _ACTOR_UMS_CMS + (
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
    # Scheduler passthrough (no dedicated enricher)
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
    rows = CRON_OPERATION_MAPPINGS.get(base)
    return list(rows) if rows else None
