"""Flow context and operation runner (no RabbitMQ wait — Python E2E owns queues)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .client import GraphQLClient

log = logging.getLogger(__name__)


@dataclass
class OperationResult:
    operation: str
    status: str  # PASS | FAIL | SKIP | XFAIL
    duration_ms: int
    error: str | None = None
    response: Any = None
    correlation_id: str | None = None


@dataclass
class FlowContext:
    client: GraphQLClient
    admin_client: GraphQLClient
    nextgen_client: GraphQLClient
    profile_id: str
    customer_id: str
    results: list[OperationResult] = field(default_factory=list)
    project_root: Path | None = None


def _capture_correlation(ctx: FlowContext) -> str | None:
    """Prefer the NextGen BFF correlation (mutations emit audit), else primary client."""
    for client in (ctx.nextgen_client, ctx.client, ctx.admin_client):
        cid = getattr(client, "last_correlation_id", None)
        if cid:
            return str(cid)
    return None


def _remember(
    ctx: FlowContext,
    operation: str,
    correlation_id: str | None,
    *,
    status: str | None = None,
    error: str | None = None,
) -> None:
    if not correlation_id and not status:
        return
    try:
        from ..generation_tracker import record_generation

        meta: dict[str, Any] = {
            "customer_id": ctx.customer_id,
            "profile_id": ctx.profile_id,
        }
        if status:
            meta["trigger_status"] = status
        if error:
            meta["trigger_error"] = str(error)[:500]
        # Still record even without cid so Generate verify can mark trigger_failed /
        # no_correlation — mint a placeholder key only when we have a real cid.
        if correlation_id:
            record_generation(
                operation,
                correlation_id,
                project_root=ctx.project_root,
                kind="graphql",
                meta=meta,
            )
        elif status:
            # No cid: keep a marker under empty history via a synthetic key so status
            # is visible in list_owned for this op's latest entry.
            record_generation(
                operation,
                f"missing-cid:{operation}:{int(time.time() * 1000)}",
                project_root=ctx.project_root,
                kind="graphql",
                meta={**meta, "cid_missing": True},
            )
    except Exception as exc:  # noqa: BLE001
        log.debug("generation_tracker record failed: %s", exc)


def _has_payload_errors(data: Any, operation: str) -> str | None:
    if not isinstance(data, dict):
        return None
    payload = data.get(operation)
    if payload is None:
        return None
    if isinstance(payload, dict):
        errors = payload.get("errors")
        if errors:
            return str(errors)
    return None


def run_operation(
    ctx: FlowContext,
    operation: str,
    fn: Callable[[], Any],
    *,
    skip: bool = False,
    expected_to_fail: bool = False,
) -> OperationResult:
    if skip:
        log.info("SKIP %s", operation)
        return OperationResult(operation=operation, status="SKIP", duration_ms=0)

    start = time.monotonic()
    try:
        response = fn()
    except Exception as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        cid = _capture_correlation(ctx)
        err = str(exc)
        status = "XFAIL" if expected_to_fail else "FAIL"
        _remember(ctx, operation, cid, status=status, error=err)
        if expected_to_fail:
            log.warning("XFAIL %s (%dms): %s", operation, duration_ms, err)
            return OperationResult(operation, "XFAIL", duration_ms, error=err, correlation_id=cid)
        log.error("FAIL %s (%dms): %s", operation, duration_ms, err)
        return OperationResult(operation, "FAIL", duration_ms, error=err, correlation_id=cid)

    duration_ms = int((time.monotonic() - start) * 1000)
    cid = _capture_correlation(ctx)

    if response is None:
        err = "GraphQL returned empty response"
        _remember(ctx, operation, cid, status="FAIL", error=err)
        log.error("FAIL %s (%dms): %s", operation, duration_ms, err)
        return OperationResult(operation, "FAIL", duration_ms, error=err, correlation_id=cid)
    payload_err = _has_payload_errors(response, operation)
    if payload_err:
        if expected_to_fail:
            _remember(ctx, operation, cid, status="XFAIL", error=payload_err)
            log.warning("XFAIL %s (%dms): %s", operation, duration_ms, payload_err)
            return OperationResult(
                operation, "XFAIL", duration_ms, error=payload_err, response=response, correlation_id=cid
            )
        _remember(ctx, operation, cid, status="FAIL", error=payload_err)
        log.error("FAIL %s (%dms): %s", operation, duration_ms, payload_err)
        return OperationResult(
            operation, "FAIL", duration_ms, error=payload_err, response=response, correlation_id=cid
        )

    _remember(ctx, operation, cid, status="PASS")
    log.info("PASS %s (%dms) correlation=%s", operation, duration_ms, (cid or "")[:8])
    return OperationResult(operation, "PASS", duration_ms, response=response, correlation_id=cid)
