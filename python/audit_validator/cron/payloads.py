"""Cron/scheduler audit envelopes: loading, normalization, live bootstrap, and
validation.

Consolidated module for the cron pipeline. Previously split across
``payloads.py`` (loading/normalization), ``bootstrap.py`` (live BYOF contract
lookup), and ``validation.py`` (passthrough/enricher classification). Kept
together because they form one cohesive cron concern and share the same data.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..compare.raw_enriched import compare_raw_enriched
from ..models import JsonDict, ValidationResult, ValidationStatus
from ..template_registry import get_template

log = logging.getLogger(__name__)

_CRON_DIR = Path(__file__).resolve().parent.parent / "data" / "cron_payloads"

# Scheduler passthrough: source.trigger present, no source.operation in raw payload.
CRON_PASSTHROUGH_ROUTING_KEYS: frozenset[str] = frozenset({
    "user.account.expiring",
    "user.accounts.digest",
    "font.leaving.catalogue",
    "server.token.expiring",
    "server.token.expiring.suspended",
    "project.archival.warning.admin",
    "project.archival.warning.member",
})

# routingKey → resolver operation label when source.operation is absent
_ROUTING_OPERATION: dict[str, str] = {
    "user.account.expiring": "weekly_account_expiry",
    "user.accounts.digest": "weekly_account_expiry_digest",
    "font.leaving.catalogue": "font_leaving_catalogue",
    "subscription.contract.expiry": "subscriptionExpiryNotification",
    "reporting.window.open": "quarterlyReportNotification",
    "reporting.window.closing": "quarterlyReportNotification",
    "reporting.window.final": "quarterlyReportNotification",
    "reporting.window.intimation": "quarterlyReportNotification",
    "server.token.expiring": "tokenExpiring",
    "server.token.expiring.suspended": "tokenExpiringSuspended",
    "project.archival.warning.admin": "projectArchivalWarningAdmin",
    "project.archival.warning.member": "projectArchivalWarningMember",
    "byof.licence.expiring": "notifyByofLicenceExpiry",
    "byof.licence.expired": "byofLicenceExpired",
    "byof.licence.expiry": "notifyByofLicenceExpiry",
    "byof.font.nolicense": "byofFontNoLicense",
    "fontbridge.auth.failed": "fontBridgeAuthFailed",
    "font.sync.failure": "fontSyncFailure",
    "subscription.fonts.deactivated": "subscription.fonts.deactivated",
    "user.account.deactivated": "auto_deactivated_user",
    "user.invitation.accepted": "userAccountAccepted",
    "user.invitation.expired": "user_invitation_expired",
}

# Infer payload routingKey when sample JSON omits it (see license-management-service PR #525).
_EVENT_NAME_ROUTING: dict[str, str] = {
    "QUARTERLY_REPORT_WINDOW_OPEN": "reporting.window.open",
    "QUARTERLY_REPORT_CLOSING_7_DAYS_REMINDER": "reporting.window.closing",
    "QUARTERLY_REPORT_CLOSING_2_DAYS_REMINDER": "reporting.window.final",
    "QUARTERLY_REPORT_WINDOW_INTIMATION": "reporting.window.intimation",
    "SUBSCRIPTION_EXPIRY_30_DAYS_WARNING": "subscription.contract.expiry",
    "SUBSCRIPTION_EXPIRY_7_DAYS_WARNING": "subscription.contract.expiry",
    "SUBSCRIPTION_EXPIRED": "subscription.contract.expiry",
}

_CASE_ROUTING_OVERRIDES: dict[str, str] = {
    "lms": "",  # QUARTERLY_REPORT_CONTRACT_EXPIRED — no notification routing key in LMS publisher
}

_ENRICHED_ONLY_FIELDS = frozenset({"enrichedEventId", "enrichedAt", "eventSource"})

# LMS / login / LFUS publish to mt.platform.raw_events with AMQP key raw.events (PR #525).
_RAW_EVENTS_AMQP_SERVICES = frozenset(
    {
        "license-management-service",
        "mt-login-service",
        "leaving-font-usage-service",
        "mosaic-asset-mgmt-service",
        "scheduler",
    }
)


@dataclass(frozen=True)
class CronCase:
    case_id: str
    path: Path
    routing_key: str
    service: str
    operation: str
    jira_refs: tuple[str, ...] = ()


def _slug(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_")


def _operation_name(payload: JsonDict, routing_key: str, case_id: str) -> str:
    source = payload.get("source") or {}
    op = (source.get("operation") or "").strip()
    if op:
        return op
    if routing_key in _ROUTING_OPERATION:
        return _ROUTING_OPERATION[routing_key]
    return _slug(case_id)


def _jira_for_service(service: str) -> tuple[str, ...]:
    if service == "license-management-service":
        return ("FDC-14226", "FDC-14203", "LMS-PR-525")
    if service == "user-mgmt-service":
        return ("FDC-14211", "UMS-PR-865")
    if service == "byof-license-service":
        return ("FDC-14204", "BYOF-PR-56")
    return ()


def _infer_routing_key(payload: JsonDict, case_id: str) -> str:
    rk = str(payload.get("routingKey") or "").strip()
    if rk:
        return rk
    if case_id in _CASE_ROUTING_OVERRIDES:
        return _CASE_ROUTING_OVERRIDES[case_id]
    subject = payload.get("subject") or {}
    if isinstance(subject, dict):
        event_name = str(subject.get("eventName") or "").strip()
        if event_name in _EVENT_NAME_ROUTING:
            return _EVENT_NAME_ROUTING[event_name]
    return ""


def amqp_routing_key_for_payload(payload: JsonDict) -> str:
    """AMQP routing key on mt.platform.raw_events (varies by publishing service)."""
    import os

    default = os.getenv("RAW_EVENTS_AMQP_ROUTING_KEY", "raw.events")
    source = payload.get("source") or {}
    service = str(source.get("service") or "").strip()
    if service in _RAW_EVENTS_AMQP_SERVICES:
        return default
    payload_rk = str(payload.get("routingKey") or "").strip()
    return payload_rk or default


def _infer_gcid_from_payload(payload: JsonDict) -> str | None:
    actor = payload.get("actor") or {}
    if isinstance(actor, dict):
        gcid = str(actor.get("globalCustomerId") or "").strip()
        if gcid:
            return gcid

    subject = payload.get("subject") or {}
    if not isinstance(subject, dict):
        return None
    for key in ("customerId", "workspaceId", "id"):
        val = subject.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, list) and val and isinstance(val[0], str) and val[0].strip():
            return val[0].strip()

    variations = subject.get("variations")
    if isinstance(variations, list):
        for variation in variations:
            if not isinstance(variation, dict):
                continue
            projects = variation.get("projects")
            if not isinstance(projects, list):
                continue
            for project in projects:
                if isinstance(project, dict):
                    gcid = str(project.get("globalCustomerId") or "").strip()
                    if gcid:
                        return gcid
    return None


def _patch_byof_contract(payload: JsonDict, *, contract_id: str | None = None) -> None:
    source = payload.get("source") or {}
    if not isinstance(source, dict) or source.get("operation") != "notifyByofLicenceExpiry":
        return

    actor = payload.get("actor") or {}
    gcid = str(actor.get("globalCustomerId") or "").strip() if isinstance(actor, dict) else ""
    user_id = str(actor.get("globalUserId") or "").strip() if isinstance(actor, dict) else ""
    if user_id in {"", "null"}:
        user_id = (
            (os.getenv("CRON_BYOF_USER_ID") or "").strip()
            or (os.getenv("NOTIFICATION_CLEANUP_USER_ID") or "").strip()
            or "bc195ef6-6884-11f1-a522-0e0a04e472ab"
        )
    if isinstance(actor, dict) and actor.get("globalUserId") in {None, "", "null"}:
        actor["globalUserId"] = user_id

    subject = payload.setdefault("subject", {})
    if not isinstance(subject, dict):
        return

    cid = (contract_id or "").strip()
    if not cid:
        ids = subject.get("id")
        if isinstance(ids, list) and ids:
            cid = str(ids[0]).strip()
        elif isinstance(ids, str):
            cid = ids.strip()
    if not cid:
        return

    subject["id"] = [cid]
    subject["contractId"] = [cid]
    subject["type"] = "contract"
    contract = subject.get("contract")
    if not isinstance(contract, dict):
        contract = {}
        subject["contract"] = contract
    contract["contractId"] = cid
    if gcid:
        contract.setdefault("companyId", gcid)
    contract.setdefault("updatedBy", user_id)
    contract.setdefault("createdBy", user_id)
    if "styles" not in subject:
        subject["styles"] = []


_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# Identity fields in cron samples that all point at the *target customer* — safe to
# re-point at the run-time gcid so a stale sample id doesn't break enrichment.
_DYNAMIC_GCID_KEYS = frozenset({"globalCustomerId", "customerId", "workspaceId"})
_DYNAMIC_GCID_LIST_KEYS = frozenset({"globalCustomerIds"})
# Scheduler emits these at run time — refresh so they are never stale.
_RUNTIME_TS_KEYS = frozenset({"scheduledAt", "triggeredAt"})


def _apply_runtime_overrides(
    node: Any,
    *,
    gcid: str | None,
    user_id: str | None,
    profile_id: str | None,
    now_iso: str,
) -> None:
    """Recursively replace stale sample identity/time values with run-time ones.

    Only rewrites values that clearly refer to the target customer/user (UUID-shaped
    gcid/customerId/workspaceId, userId, profileId) and the scheduler timestamps. Other
    fields (names, roles, formatted expiry dates) are left to the caller/sample.
    """
    if isinstance(node, dict):
        for key, val in list(node.items()):
            if gcid and key in _DYNAMIC_GCID_KEYS and isinstance(val, str) and _UUID_RE.match(val):
                node[key] = gcid
            elif gcid and key in _DYNAMIC_GCID_LIST_KEYS and isinstance(val, list):
                node[key] = [gcid] if not val else [gcid for _ in val]
            elif key in _RUNTIME_TS_KEYS and isinstance(val, str) and val.strip():
                node[key] = now_iso
            elif user_id and key == "userId" and isinstance(val, str) and val.strip():
                node[key] = user_id
            elif profile_id and key == "profileId" and isinstance(val, str) and val.strip():
                node[key] = profile_id
            else:
                _apply_runtime_overrides(
                    val, gcid=gcid, user_id=user_id, profile_id=profile_id, now_iso=now_iso
                )
    elif isinstance(node, list):
        for item in node:
            _apply_runtime_overrides(
                item, gcid=gcid, user_id=user_id, profile_id=profile_id, now_iso=now_iso
            )


def normalize_cron_payload(
    payload: JsonDict,
    *,
    case_id: str,
    gcid: str | None = None,
    byof_contract_id: str | None = None,
    user_id: str | None = None,
    profile_id: str | None = None,
) -> JsonDict:
    """Fresh correlation ids + fix known sample defects before publish.

    Run-time variables (``gcid``/``user_id``/``profile_id``) override the stale ids
    baked into the sample so payloads stay dynamic per environment. Timestamps
    (``occurredAt``/``scheduledAt``/``triggeredAt``) are always refreshed to now.
    """
    out = copy.deepcopy(payload)

    for field in _ENRICHED_ONLY_FIELDS:
        out.pop(field, None)

    # tokenexpire.json sample used empty-string key instead of actor
    if "" in out and isinstance(out[""], dict):
        out["actor"] = out.pop("")

    resolved_gcid = (gcid or _infer_gcid_from_payload(out) or "").strip() or None
    if resolved_gcid:
        actor = out.setdefault("actor", {})
        if isinstance(actor, dict) and not actor.get("globalCustomerId"):
            actor["globalCustomerId"] = resolved_gcid

    rk = _infer_routing_key(out, case_id)
    source = out.setdefault("source", {})
    if isinstance(source, dict):
        # Scheduler passthrough: no source.operation (resolver uses trigger or routingKey only).
        is_scheduler = (
            bool(source.get("trigger")) and not source.get("operation")
        ) or (rk in CRON_PASSTHROUGH_ROUTING_KEYS and not source.get("operation"))
        if is_scheduler and not source.get("trigger"):
            # Resolver scheduler handler requires source.trigger (see scheduler-inbound-envelope.schema.ts).
            source["trigger"] = rk or case_id
        if not is_scheduler and not source.get("operation"):
            source["operation"] = _operation_name(out, rk, case_id)
        service = str(source.get("service") or "").strip()
        if not service or service == "scheduler":
            pass  # keep explicit service from sample when present

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    out["xCorrelationId"] = str(uuid.uuid4())
    out["eventId"] = str(uuid.uuid4())
    out["occurredAt"] = now_iso
    if rk:
        out["routingKey"] = rk
    elif "routingKey" in out and not out["routingKey"]:
        out.pop("routingKey", None)

    # Re-point stale sample ids at the run-time customer/user and refresh scheduler
    # timestamps so the same payload works across environments and never goes stale.
    if resolved_gcid or user_id or profile_id:
        _apply_runtime_overrides(
            out,
            gcid=resolved_gcid,
            user_id=(user_id or "").strip() or None,
            profile_id=(profile_id or "").strip() or None,
            now_iso=now_iso,
        )

    _patch_byof_contract(out, contract_id=byof_contract_id)
    return out


def load_cron_cases(cron_dir: Path | None = None) -> list[CronCase]:
    base = cron_dir or _CRON_DIR
    if not base.is_dir():
        return []

    cases: list[CronCase] = []
    for path in sorted(base.glob("*.json")):
        if not path.stat().st_size:
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            continue
        case_id = path.stem
        rk = _infer_routing_key(raw, case_id)
        source = raw.get("source") or {}
        service = str(source.get("service") or "scheduler")
        operation = _operation_name(raw, rk, case_id)
        cases.append(
            CronCase(
                case_id=case_id,
                path=path,
                routing_key=rk,
                service=service,
                operation=operation,
                jira_refs=_jira_for_service(service),
            )
        )
    return cases


# ---------------------------------------------------------------------------
# Live bootstrap (formerly cron/bootstrap.py)
# ---------------------------------------------------------------------------

_GET_AVAILABLE_CONTRACTS = (
    "query GetAvailableContracts($input: GetAvailableContractsInput) { "
    "getAvailableContracts(input: $input) { nodes { contractId licenceName status } totalCount } }"
)


def resolve_byof_contract_id(*, project_root: Path | None = None) -> str | None:
    """
    Return a real BYOF contract id for notifyByofLicenceExpiry cron validation.

    Uses CRON_BYOF_CONTRACT_ID when set; otherwise reuses the first available contract
    from NextGen GraphQL.
    """
    explicit = (os.getenv("CRON_BYOF_CONTRACT_ID") or "").strip()
    if explicit:
        return explicit

    try:
        from ..auth import resolve_nextgen_bearer_token
        from ..config import load_config
        from ..simulation.client import GraphQLClient
        from ..simulation.config import load_simulation_config

        app_cfg = load_config(project_root)
        cfg = load_simulation_config(app_cfg.project_root)
        token = resolve_nextgen_bearer_token()
        if not token:
            log.warning("BYOF cron bootstrap skipped — no NextGen bearer token")
            return None

        client = GraphQLClient(cfg, bearer_token=token, endpoint=cfg.nextgen_endpoint)
        resp = client.request_apollo("GetAvailableContracts", _GET_AVAILABLE_CONTRACTS, None, browser=True)
        nodes = (resp.get("getAvailableContracts") or {}).get("nodes") or []
        contract_id = str((nodes[0] or {}).get("contractId") or "").strip() if nodes else ""
        if contract_id:
            log.info("Using existing BYOF contract for cron validation: %s", contract_id[:8])
        else:
            log.warning("BYOF cron bootstrap skipped — no contracts returned from GraphQL")
        return contract_id or None
    except Exception as exc:
        log.warning("BYOF cron bootstrap failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Validation (formerly cron/validation.py)
# ---------------------------------------------------------------------------

# Resolver has no dedicated enricher for these scheduler/notification ops today
# (verified against mt-audit-log-resolver-service enrichers). They are published by
# their originating services (LMS, UMS, BYOF, login, font-bridge, LFUS, scheduler)
# as notification/passthrough events, so a missing enriched copy is WARN, not FAIL.
CRON_NO_ENRICHER_OPERATIONS: frozenset[str] = frozenset({
    "quarterlyReportNotification",
    "subscriptionExpiryNotification",
    "weekly_account_expiry",
    "weekly_account_expiry_digest",
    "tokenExpiring",
    "tokenExpiringSuspended",
    "projectArchivalWarningAdmin",
    "projectArchivalWarningMember",
    "fontLeavingCatalogue",
    "font_leaving_catalogue",
    "fontBridgeAuthFailed",
    "fontSyncFailure",
    "byofLicenceExpired",
    "byofFontNoLicense",
    "subscription.fonts.deactivated",
    "auto_deactivated_user",
    "userAccountAccepted",
    "user_invitation_expired",
})

# Enricher exists in resolver code but is not reliably deployed in Everest preprod yet.
CRON_DEFERRED_ENRICHER_OPERATIONS: frozenset[str] = frozenset({
    "notifyByofLicenceExpiry",
})


def is_scheduler_passthrough(raw: JsonDict | None, enriched: JsonDict | None = None) -> bool:
    payload = raw or enriched or {}
    source = payload.get("source") or {}
    if isinstance(source, dict) and source.get("trigger") and not source.get("operation"):
        return True
    if enriched and str(enriched.get("eventSource") or "") == "scheduler":
        return True
    rk = str(payload.get("routingKey") or "")
    return rk in CRON_PASSTHROUGH_ROUTING_KEYS and not (source.get("operation") if isinstance(source, dict) else None)


def expects_cron_enrichment(operation: str, raw: JsonDict | None = None) -> bool:
    if operation in CRON_NO_ENRICHER_OPERATIONS:
        return False
    if raw and is_scheduler_passthrough(raw):
        return True  # passthrough still produces an enriched copy on the queue
    if operation == "notifyByofLicenceExpiry":
        return True
    return operation not in CRON_NO_ENRICHER_OPERATIONS and not is_scheduler_passthrough(raw)


def validate_scheduler_passthrough(
    operation: str,
    service: str,
    enriched: JsonDict,
    raw: JsonDict | None,
    result: ValidationResult,
) -> None:
    for field in ("xCorrelationId", "eventId", "occurredAt", "enrichedEventId", "enrichedAt"):
        val = enriched.get(field)
        if not isinstance(val, str) or not str(val).strip():
            result.add(
                "cron-scheduler",
                "required_string",
                ValidationStatus.FAIL,
                f"Scheduler enriched event missing `{field}`",
                field,
            )

    if str(enriched.get("eventSource") or "") != "scheduler":
        result.add(
            "cron-scheduler",
            "eventSource",
            ValidationStatus.WARN,
            "Expected eventSource=scheduler on passthrough enriched payload",
            "eventSource",
        )

    rk = str(enriched.get("routingKey") or "")
    if raw:
        raw_rk = str(raw.get("routingKey") or "")
        if raw_rk and rk and raw_rk != rk:
            result.add(
                "cron-scheduler",
                "routingKey",
                ValidationStatus.FAIL,
                f"routingKey mismatch raw={raw_rk} enriched={rk}",
                "routingKey",
            )

    if raw is not None:
        compare_raw_enriched(raw, enriched, result, skip_enrichment_fields=True)


def validate_cron_event_pair(
    operation: str,
    service: str,
    enriched: JsonDict,
    raw: JsonDict | None = None,
) -> ValidationResult:
    from ..validator import validate_event_pair

    if raw and is_scheduler_passthrough(raw, enriched):
        result = ValidationResult(
            operation=operation,
            service=service,
            template_id="cron-scheduler",
            status=ValidationStatus.PASS,
        )
        validate_scheduler_passthrough(operation, service, enriched, raw, result)
        return result

    if operation in CRON_NO_ENRICHER_OPERATIONS:
        result = ValidationResult(
            operation=operation,
            service=service,
            template_id="cron-no-enricher",
            status=ValidationStatus.WARN,
        )
        result.add(
            "cron",
            "no_enricher",
            ValidationStatus.WARN,
            f"No resolver enricher registered for `{operation}` — enrichment not expected",
        )
        return validate_event_pair(operation, service, enriched, raw, structure_only=True)

    template = get_template(operation)
    if template:
        return validate_event_pair(operation, service, enriched, raw, structure_only=False)

    return validate_event_pair(operation, service, enriched, raw, structure_only=False)
