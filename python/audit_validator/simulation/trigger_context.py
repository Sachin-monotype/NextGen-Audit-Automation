"""Capture GraphQL/curl trigger context for source validation (not Raw Mongo)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# Default Chrome UA used for BE GraphQL curls when NEXTGEN_USER_AGENT is unset.
DEFAULT_WEB_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)


def platform_environment_from_user_agent(ua: str | None) -> str | None:
    """Map client fingerprint → platformEnvironment.

    App / Electron desktop clients include ``Electron`` (and usually
    ``MonotypeNextGen``) in the UA. Plain browser UAs map to ``web``.
    Returns None when the UA is missing or unrecognized.
    """
    u = str(ua or "").strip().lower()
    if not u:
        return None
    if "electron" in u or "monotypenextgen" in u:
        return "app"
    if any(
        m in u
        for m in ("mozilla", "chrome", "safari", "firefox", "edg/", "applewebkit")
    ):
        return "web"
    return None


def build_trigger_context(
    *,
    operation: str,
    correlation_id: str | None,
    graphql_response: dict[str, Any] | None = None,
    graphql_input: dict[str, Any] | None = None,
    user_agent: str | None = None,
    platform_environment: str | None = None,
    jwt_identity: dict[str, Any] | None = None,
    success: bool | None = None,
    invent_client_defaults: bool = True,
    ingress_source: dict[str, Any] | None = None,
    ingress_actor: dict[str, Any] | None = None,
    ingress_subject: dict[str, Any] | None = None,
    ingress_headers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the trigger payload we compare enriched envelope fields against.

    ``invent_client_defaults=False`` (Excel / CasePilot UI) skips fabricating a
    Chrome UA and hardcoded ``web`` platform — those are not on the GraphQL
    response. Compare then derives platformEnvironment from the enriched UA
    (Electron → app) and SKIPs actorUserAgent when it was never captured.

    App / desktop UI captures: prefer real ``ingress_source`` / request headers.
    GQL frontend events (including those fired from the desktop app) default to
    ``mtconnect-api`` + ``platformVersion`` from ``X-Unified-Version`` / body
    (typically ``1.0.0``). App-shell ops (preferences / local fonts) and
    fontSimilarViewed / fontPairsViewed use ``mtconnect-ui``.
    """
    if invent_client_defaults:
        ua = (
            user_agent
            or os.getenv("NEXTGEN_USER_AGENT")
            or DEFAULT_WEB_USER_AGENT
        )
    else:
        ua = (user_agent or "").strip()

    platform = os.getenv("AUDIT_SOURCE_PLATFORM", "nextGen").strip() or "nextGen"
    if platform_environment and str(platform_environment).strip():
        env = str(platform_environment).strip().lower()
    elif invent_client_defaults:
        env = (
            os.getenv("AUDIT_SOURCE_PLATFORM_ENVIRONMENT")
            or os.getenv("SOURCE_PLATFORM_ENVIRONMENT")
            or "web"
        ).strip() or "web"
    else:
        # Prefer UA-derived env when we have a real fingerprint; else leave blank.
        env = platform_environment_from_user_agent(ua) or ""

    # Ingress Excel body may already carry the real source block.
    ing = ingress_source if isinstance(ingress_source, dict) else {}
    hdrs: dict[str, str] = {}
    if isinstance(ingress_headers, dict):
        for hk, hv in ingress_headers.items():
            if hv not in (None, "", [], {}):
                hdrs[str(hk).strip().lower()] = str(hv).strip()
    if not env and isinstance(ing.get("platformEnvironment"), str):
        env = str(ing.get("platformEnvironment") or "").strip().lower()
    if not ua and isinstance(ing.get("actorUserAgent"), str):
        ua = str(ing.get("actorUserAgent") or "").strip()
        if not env:
            env = platform_environment_from_user_agent(ua) or ""
    if not ua and hdrs.get("user-agent"):
        ua = hdrs["user-agent"]
        if not env:
            env = platform_environment_from_user_agent(ua) or ""

    is_app_ui = (not invent_client_defaults) and (
        env == "app"
        or bool(ua and ("electron" in ua.lower() or "monotypenextgen" in ua.lower()))
        or str(ing.get("service") or "").strip().lower() == "mtconnect-ui"
    )

    # Ops that publish as mtconnect-ui (desktop shell + discover font similar/pairs).
    _ui_service_ops = {
        "appSettingsAutoPerformanceEnabled",
        "appSettingsAutoPerformanceDisabled",
        "appSettingsPerformanceModeChanged",
        "appLanguageChanged",
        "appSettingsPluginInstallAllEnabled",
        "appSettingsPluginInstallAllDisabled",
        "appSettingsPluginAppEnabled",
        "appSettingsActivationModeChanged",
        "appFeedbackSubmitted",
        "appLogsExported",
        "appNetworkRefreshed",
        "appHealthStatusRefreshed",
        "appCacheCleared",
        "fontTempActivated",
        "fontActivationTypeSwitched",
        "fontLocalfontActivated",
        "fontLocalfontDeactivated",
        "fontSyncSuccess",
        "userSwitchWorkspaceApp",
        "fontSimilarViewed",
        "fontPairsViewed",
        "fontSimilarFontViewed",
    }
    base_op = (operation or "").split("(", 1)[0].strip()
    # Only named shell / fontSimilar ops use mtconnect-ui — do not trust a
    # mis-tagged ingress ``service`` for GQL events (those are mtconnect-api).
    wants_ui_service = base_op in _ui_service_ops
    connect_svc = str(ing.get("service") or "").strip() == "MonotypeNextGenConnectService"

    if connect_svc:
        service = "MonotypeNextGenConnectService"
        version = (
            hdrs.get("x-unified-version")
            or str(ing.get("platformVersion") or "").strip()
            or "1.0.0"
        )
    elif wants_ui_service:
        service = "mtconnect-ui"
        version = (
            hdrs.get("x-unified-version")
            or str(ing.get("platformVersion") or "").strip()
            or "1.0.0"
        )
    elif is_app_ui:
        # App-hosted GraphQL still publishes as mtconnect-api.
        service = "mtconnect-api"
        version = (
            hdrs.get("x-unified-version")
            or str(ing.get("platformVersion") or "").strip()
            or "1.0.0"
        )
    else:
        service = os.getenv("AUDIT_SOURCE_SERVICE", "mtconnect-api").strip() or "mtconnect-api"
        version = (
            hdrs.get("x-unified-version")
            or os.getenv("AUDIT_SOURCE_PLATFORM_VERSION", "1.0.0").strip()
            or "1.0.0"
        )
        if str(ing.get("service") or "").strip() and not wants_ui_service:
            # Preserve non-GQL overrides (e.g. Connect) but never overwrite
            # GQL default with a stale mtconnect-ui tag.
            ing_svc = str(ing.get("service")).strip()
            if ing_svc != "mtconnect-ui":
                service = ing_svc
        if str(ing.get("platformVersion") or "").strip():
            version = str(ing.get("platformVersion")).strip()

    op_state = "success"
    if success is False:
        op_state = "failure"
    elif isinstance(graphql_response, dict):
        # Heuristic: GraphQL errors / success:false
        for node in graphql_response.values():
            if isinstance(node, dict) and node.get("success") is False:
                op_state = "failure"
                break
    if isinstance(ing.get("operationState"), str) and ing.get("operationState"):
        op_state = str(ing.get("operationState"))

    request: dict[str, Any] = {
        "service": service,
        "platform": platform,
        "platformVersion": version,
        "operation": operation,
        "operationState": op_state,
        "operationIndex": 0,
    }
    source: dict[str, Any] = {
        "operation": operation,
        "service": service,
        "platform": platform,
        "platformVersion": version,
        "operationState": op_state,
        "operationIndex": int(ing.get("operationIndex") or 0),
        "type": ing.get("type") if isinstance(ing.get("type"), list) else ["user"],
    }
    if isinstance(ing.get("platform"), str) and ing.get("platform"):
        source["platform"] = str(ing.get("platform"))
        request["platform"] = source["platform"]
    if env:
        request["platformEnvironment"] = env
        source["platformEnvironment"] = env
    if ua:
        request["userAgent"] = ua
        request["user-agent"] = ua
        source["actorUserAgent"] = ua

    # Overlay remaining ingress source leaves (osName / osVersion / cpuArch / …).
    for key, val in ing.items():
        if key in source and source.get(key) not in (None, "", [], {}):
            continue
        if val in (None, "", [], {}):
            continue
        source[key] = val
        if key in {"service", "platform", "platformVersion", "operation", "operationState", "operationIndex"}:
            request[key] = val

    headers = ingress_headers if isinstance(ingress_headers, dict) else {}
    actor = ingress_actor if isinstance(ingress_actor, dict) else {}
    subject = ingress_subject if isinstance(ingress_subject, dict) else {}

    ctx: dict[str, Any] = {
        "operation": operation,
        "xCorrelationId": (
            (headers.get("xCorrelationId") if isinstance(headers.get("xCorrelationId"), str) else None)
            or correlation_id
            or ""
        ),
        "correlation_id": correlation_id or "",
        "eventVersion": (
            headers.get("eventVersion")
            if headers.get("eventVersion") not in (None, "")
            else (os.getenv("AUDIT_EVENT_VERSION", "1").strip() or "1")
        ),
        "graphql_response": graphql_response or {},
        "graphql_input": graphql_input or {},
        "jwt_identity": jwt_identity or {},
        # BE curls: UA is what we sent. Excel/UI: true when ingress/UA was passed.
        "ua_captured": bool(ua) and (
            invent_client_defaults
            or bool(user_agent)
            or bool(ing.get("actorUserAgent"))
        ),
        "request": request,
        "source": source,
    }
    if headers.get("eventId") not in (None, ""):
        ctx["eventId"] = headers.get("eventId")
    if headers.get("occurredAt") not in (None, ""):
        ctx["occurredAt"] = headers.get("occurredAt")
    if actor:
        ctx["ingress_actor"] = actor
        gcid = str(actor.get("globalCustomerId") or "").strip()
        guid = str(actor.get("globalUserId") or "").strip()
        if gcid and guid:
            actor = {**actor, "authenticationState": "authenticated"}
            ctx["ingress_actor"] = actor
    if subject:
        ctx["ingress_subject"] = subject
    if ing:
        ctx["ingress_source"] = {**ing, "service": service, "platformVersion": version}
        if ua:
            ctx["ingress_source"]["actorUserAgent"] = ua
    if headers:
        ctx["ingress_headers"] = headers
        ctx["request_headers"] = headers
    return ctx


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


def _trigger_is_ui_or_excel_capture(data: dict[str, Any]) -> bool:
    """True when the trigger came from Excel / Playwright / CasePilot UI (not BE seed)."""
    mode = str(data.get("replay_mode") or "").strip()
    src = str(data.get("capture_source") or "").strip()
    note = str(data.get("jwt_identity_note") or "")
    return (
        mode in {"casepilot_ui", "playwright_script"}
        or src in {"playwright_script", "casepilot_ui", "casepilot_minimal"}
        or bool(data.get("jwt_from_excel"))
        or "Excel auth_token" in note
        or note.startswith("JWT claims from Excel")
    )


def _read_trigger_file(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def load_trigger_context(project_root: Path, operation: str) -> dict[str, Any] | None:
    """Load trigger JSON for an operation.

    Prefer Excel/UI captures (``…(UI).json`` / playwright / Excel JWT) over stale
    BE seed files that share the same scenario name (e.g. ``activateFamily(global)``).
    """
    trigger_dir = project_root / "payload" / "trigger"
    op = (operation or "").strip()
    if not op:
        return None

    candidates: list[Path] = []
    exact = trigger_dir / f"{op}.json"
    if exact.is_file():
        candidates.append(exact)

    if op.endswith("(UI)"):
        bare = op[: -len("(UI)")]
        bare_path = trigger_dir / f"{bare}.json"
        if bare_path.is_file() and bare_path not in candidates:
            candidates.append(bare_path)
    else:
        ui_path = trigger_dir / f"{op}(UI).json"
        if ui_path.is_file() and ui_path not in candidates:
            candidates.append(ui_path)

    ui_hits: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    for path in candidates:
        data = _read_trigger_file(path)
        if not data:
            continue
        if _trigger_is_ui_or_excel_capture(data):
            ui_hits.append(data)
        else:
            other.append(data)

    if ui_hits:
        return ui_hits[0]
    if other:
        return other[0]
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
            ctx["ua_captured"] = True
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
        pub_cid = str(root.get("xCorrelationId") or "").strip()
        if pub_cid:
            ctx["xCorrelationId"] = pub_cid
            ctx["correlation_id"] = pub_cid
            break

    for root in (enriched, raw_msg):
        if not isinstance(root, dict):
            continue
        for key in ("eventId", "eventVersion", "occurredAt", "routingKey"):
            val = root.get(key)
            if val not in (None, "", [], {}):
                ctx[key] = val
