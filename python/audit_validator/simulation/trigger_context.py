"""Capture GraphQL/curl trigger context for source validation (not Raw Mongo)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def build_trigger_context(
    *,
    operation: str,
    correlation_id: str | None,
    graphql_response: dict[str, Any] | None = None,
    graphql_input: dict[str, Any] | None = None,
    user_agent: str | None = None,
    jwt_identity: dict[str, Any] | None = None,
    success: bool | None = None,
) -> dict[str, Any]:
    """Build the trigger payload we compare enriched envelope fields against."""
    ua = (
        user_agent
        or os.getenv("NEXTGEN_USER_AGENT")
        or (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
        )
    )
    service = os.getenv("AUDIT_SOURCE_SERVICE", "mtconnect-api").strip() or "mtconnect-api"
    platform = os.getenv("AUDIT_SOURCE_PLATFORM", "nextGen").strip() or "nextGen"
    env = (
        os.getenv("AUDIT_SOURCE_PLATFORM_ENVIRONMENT")
        or os.getenv("SOURCE_PLATFORM_ENVIRONMENT")
        or "web"
    ).strip() or "web"
    version = os.getenv("AUDIT_SOURCE_PLATFORM_VERSION", "1.0.0").strip() or "1.0.0"

    op_state = "success"
    if success is False:
        op_state = "failure"
    elif isinstance(graphql_response, dict):
        # Heuristic: GraphQL errors / success:false
        for node in graphql_response.values():
            if isinstance(node, dict) and node.get("success") is False:
                op_state = "failure"
                break

    return {
        "operation": operation,
        "xCorrelationId": correlation_id or "",
        "correlation_id": correlation_id or "",
        "eventVersion": os.getenv("AUDIT_EVENT_VERSION", "1").strip() or "1",
        "graphql_response": graphql_response or {},
        "graphql_input": graphql_input or {},
        "jwt_identity": jwt_identity or {},
        "request": {
            "userAgent": ua,
            "user-agent": ua,
            "service": service,
            "platform": platform,
            "platformEnvironment": env,
            "platformVersion": version,
            "operation": operation,
            "operationState": op_state,
            "operationIndex": 0,
        },
        "source": {
            "operation": operation,
            "service": service,
            "platform": platform,
            "platformEnvironment": env,
            "platformVersion": version,
            "actorUserAgent": ua,
            "operationState": op_state,
            "operationIndex": 0,
            "type": ["user"],
        },
    }


def save_trigger_context(
    project_root: Path,
    display_name: str,
    context: dict[str, Any],
) -> Path:
    trigger_dir = project_root / "payload" / "trigger"
    trigger_dir.mkdir(parents=True, exist_ok=True)
    path = trigger_dir / f"{display_name}.json"
    path.write_text(json.dumps(context, indent=2, default=str), encoding="utf-8")
    return path


def load_trigger_context(project_root: Path, operation: str) -> dict[str, Any] | None:
    path = project_root / "payload" / "trigger" / f"{operation}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _replay_graphql_live(
    base_op: str,
    inp: dict[str, Any],
    *,
    project_root: Path,
) -> dict[str, Any] | None:
    """Re-fire the mutation with captured input; return GraphQL ``data`` object."""
    from audit_validator.simulation.client import DualEndpointGraphQLClient, GraphQLClient
    from audit_validator.simulation.config import load_simulation_config
    from audit_validator.utility.operation_graphql import (
        get_document_for_operation,
        is_nextgen_ui_operation,
    )

    document = get_document_for_operation(base_op)
    if not document or not inp:
        return None
    cfg = load_simulation_config(project_root)
    try:
        if is_nextgen_ui_operation(base_op):
            client = DualEndpointGraphQLClient(cfg)
        else:
            client = GraphQLClient(cfg)
        data = client.request(document, {"input": inp})
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def build_trigger_from_captured_event(
    operation: str,
    raw_event: dict[str, Any],
    enriched: dict[str, Any] | None = None,
    *,
    project_root: Path | None = None,
) -> dict[str, Any] | None:
    """Build trigger context from a paired raw event — never use raw envelope as row source.

    Prefer ``subject.metadata.result`` (mutation response at publish). When absent,
    re-fire GraphQL with ``subject.metadata.input`` so Compare can source join keys
    and response fields from the API — not the published raw envelope.
    """
    from audit_validator.touchpoint.assertions import (
        extract_raw_metadata_input,
        extract_raw_metadata_result,
    )

    msg = raw_event.get("message") if isinstance(raw_event.get("message"), dict) else raw_event
    if not isinstance(msg, dict):
        return None

    base_op = operation.split("(", 1)[0].strip() if "(" in operation else operation
    op_name = str((msg.get("source") or {}).get("operation") or base_op).strip() or base_op

    inp = extract_raw_metadata_input(raw_event)
    if not inp and enriched:
        meta = ((enriched.get("subject") or {}).get("metadata") or {}).get("input")
        if isinstance(meta, dict):
            inp = meta

    result = extract_raw_metadata_result(raw_event)
    gql_response: dict[str, Any] = {}
    replay_mode = "input_only"
    # Prefer the mutation response captured at publish time (metadata.result).
    # Live replay creates new batchIds/timestamps and can return success:false when
    # the resource is already favourited/tagged — that falsely FAILs Compare.
    if result:
        gql_response = {op_name: result}
        replay_mode = "metadata.result"
    elif project_root is not None and inp:
        live_data = _replay_graphql_live(base_op, inp, project_root=project_root)
        if live_data:
            gql_response = live_data
            replay_mode = "live_replay"

    cid = str(msg.get("xCorrelationId") or (enriched or {}).get("xCorrelationId") or "")

    published_source: dict[str, Any] = {}
    for root in (enriched, msg):
        if not isinstance(root, dict):
            continue
        src = root.get("source")
        if isinstance(src, dict) and src:
            published_source = src
            break

    pub_state = published_source.get("operationState")
    pub_success: bool | None = None
    if pub_state == "success":
        pub_success = True
    elif pub_state == "failure":
        pub_success = False

    ctx = build_trigger_context(
        operation=op_name,
        correlation_id=cid,
        graphql_response=gql_response,
        graphql_input=inp,
        user_agent=published_source.get("actorUserAgent"),
        success=pub_success,
    )
    if cid:
        ctx["xCorrelationId"] = cid
        ctx["correlation_id"] = cid
    ctx["replay_mode"] = replay_mode
    _overlay_published_envelope(ctx, msg, enriched)
    return ctx


def _overlay_published_envelope(
    ctx: dict[str, Any],
    raw_msg: dict[str, Any],
    enriched: dict[str, Any] | None,
) -> None:
    """Use the published event envelope for trigger fields that must match enriched.

    Live GraphQL replay and env defaults (Chrome UA, replay success:false after delete)
    disagree with the UI/CasePilot trigger that actually published the audit event.
    """
    published_source: dict[str, Any] = {}
    for root in (enriched, raw_msg):
        if not isinstance(root, dict):
            continue
        src = root.get("source")
        if isinstance(src, dict) and src:
            published_source = src
            break
    if not published_source:
        return

    ctx_source = ctx.setdefault("source", {})
    req = ctx.setdefault("request", {})
    for key in (
        "operation",
        "service",
        "platform",
        "platformEnvironment",
        "platformVersion",
        "actorUserAgent",
        "operationState",
        "operationIndex",
        "type",
    ):
        val = published_source.get(key)
        if val in (None, "", [], {}):
            continue
        ctx_source[key] = val
        if key == "actorUserAgent":
            req["userAgent"] = val
            req["user-agent"] = val
        elif key in req or key in {
            "operationState",
            "operation",
            "service",
            "platform",
            "platformEnvironment",
            "platformVersion",
            "operationIndex",
        }:
            req[key] = val

    for root in (enriched, raw_msg):
        if not isinstance(root, dict):
            continue
        for key in ("eventId", "eventVersion", "occurredAt", "routingKey"):
            val = root.get(key)
            if val not in (None, "", [], {}):
                ctx[key] = val
