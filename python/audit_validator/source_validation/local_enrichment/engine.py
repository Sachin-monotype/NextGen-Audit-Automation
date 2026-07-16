"""Dispatch local enrichment by operation (subset mirroring resolver enrichers)."""

from __future__ import annotations

from ...models import JsonDict
from .actor import build_actor_enriched_snapshot
from .font_activation import build_activate_family_snapshot, family_ids_from_envelope
from .types import EnrichmentClients, EnrichmentResult

_SUPPORTED = frozenset(
    {
        "activateFamily",
        "activateStyle",
        "activateVariation",
        "bulkActivateStyles",
        "createRole",
        "createTeam",
    }
)


def supported_operations() -> tuple[str, ...]:
    return tuple(sorted(_SUPPORTED))


def enrich_event(
    operation: str,
    envelope: JsonDict,
    *,
    clients: EnrichmentClients,
    correlation_id: str | None = None,
) -> EnrichmentResult:
    cid = correlation_id or str(envelope.get("xCorrelationId") or "local-enrichment")
    actor = envelope.get("actor") or {}
    global_user_id = str(actor.get("globalUserId") or "")
    global_customer_id = str(actor.get("globalCustomerId") or "")
    result = EnrichmentResult(operation=operation)

    try:
        if operation == "activateFamily":
            if not clients.discovery:
                result.errors.append("Discovery client required for activateFamily")
                return result
            family_ids = family_ids_from_envelope(envelope)
            result.subject_snapshot = build_activate_family_snapshot(
                family_ids=family_ids,
                correlation_id=cid,
                discovery=clients.discovery,
            )
        elif operation in {"activateStyle", "activateVariation", "bulkActivateStyles"}:
            # Font ops share Discovery subject enrichment; full parity TBD per op input shape
            if clients.discovery:
                family_ids = family_ids_from_envelope(envelope)
                if family_ids:
                    result.subject_snapshot = build_activate_family_snapshot(
                        family_ids=family_ids,
                        correlation_id=cid,
                        discovery=clients.discovery,
                    )
        elif operation in {"createRole", "createTeam"}:
            pass  # subject from UMS in resolver — structural validation only here

        if clients.ums and clients.cms and global_user_id and global_customer_id:
            result.actor_snapshot = build_actor_enriched_snapshot(
                global_user_id=global_user_id,
                global_customer_id=global_customer_id,
                correlation_id=cid,
                ums=clients.ums,
                cms=clients.cms,
            )
        elif operation in {"activateFamily", "createRole", "createTeam"}:
            result.errors.append("UMS/CMS clients and actor IDs required for actor snapshot")
    except Exception as exc:
        result.errors.append(str(exc))

    return result
