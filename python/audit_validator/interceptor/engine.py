"""Network Interceptor Engine for Monotype Desktop and Web.

Ported from standalone NetworkInterceptor tool.
Captures GraphQL and HTTP network traffic from Electron Desktop Apps (Monotype NextGen)
and Web Browsers (Chrome) over Chrome DevTools Protocol (CDP port 9222).
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

log = logging.getLogger("AuditInterceptorEngine")


@dataclass
class HeaderValues:
    auth_token: str = ""
    bearer_token: str = ""
    correlation_id: str = ""
    user_agent: str = ""
    app_version: str = ""
    event_version: int = 1
    jwt_claims: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


BATCH_ORCHESTRATION_TRIGGER_MAP: dict[str, str] = {
    "bulkactivatestyles": "bulkActivateComplete",
    "bulkactivatelists": "bulkActivateComplete",
    "bulkactivateall": "bulkActivateComplete",
    "bulkdeactivatestyles": "bulkDeactivateComplete",
    "bulkdeactivatelists": "bulkDeactivateComplete",
    "bulkdeactivateall": "bulkDeactivateComplete",
    "exportfonts": "exportCompleted",
    "exportfontlist": "exportCompleted",
    "exportproject": "exportCompleted",
    "byoffontdelete": "byofFontDeleteComplete",
    "deleteimportedfonts": "byofFontDeleteComplete",
}


def detect_scenario(operation_name: str, variables: dict[str, Any] | None = None, query: str = "") -> str:
    """Infer touchpoint scenario (global, favourite, list, project, project_list, document, etc.)
    from GraphQL operation name and variables payload matching NextGen-Audit Automation rules.
    """
    if not isinstance(variables, dict):
        variables = {}

    inp = variables.get("input") if isinstance(variables.get("input"), dict) else variables
    op_lower = (operation_name or "").strip().lower()

    list_type = str(inp.get("listType") or "").upper()
    list_ids = inp.get("listIds") or inp.get("listId") or []
    if not isinstance(list_ids, list):
        list_ids = [list_ids] if list_ids else []

    project_id = str(inp.get("projectId") or inp.get("fontProjectId") or "").strip()
    font_list_id = str(inp.get("fontListId") or inp.get("listId") or "").strip()
    doc_id = str(inp.get("documentId") or "").strip()

    # 0. Batch Orchestration Events (bulkActivateComplete, bulkDeactivateComplete, exportCompleted, etc.)
    if "complete" in op_lower or "batch" in op_lower or "export" in op_lower:
        if project_id and list_type == "FONTLIST":
            return "project_list"
        if project_id or list_type == "FONTPROJECT" or any(str(l).startswith("project_") for l in list_ids):
            return "project"
        if list_type == "FAVORITE":
            return "favourite"
        if list_type == "FONTLIST" or list_ids or font_list_id:
            return "list"
        return "global"

    # 1. Family / Style / Variation Activation & Deactivation
    if any(k in op_lower for k in ("activatefamily", "deactivatefamilies", "activatestyle", "deactivatestyle", "activatevariation", "deactivatevariation")):
        if project_id and list_type == "FONTLIST":
            return "project_list"
        if project_id or list_type == "FONTPROJECT" or any(str(l).startswith("project_") for l in list_ids):
            return "project"
        if list_type == "FAVORITE":
            return "favourite"
        if list_type == "FONTLIST" or list_ids or font_list_id:
            return "list"
        return "global"

    # 2. List Activation & Deactivation (activateList, deActivateList, bulkActivateLists, bulkDeactivateLists)
    if "list" in op_lower and any(k in op_lower for k in ("activate", "deactivate")):
        if project_id:
            return "project_list"
        if list_type == "FAVORITE":
            return "favourite"
        return "list"

    # 3. Add to / Remove from List / Project / Favorites
    if "favorite" in op_lower or "favourite" in op_lower:
        return "favourite"

    if "fontlist" in op_lower or ("list" in op_lower and "project" not in op_lower):
        if project_id:
            return "project_list"
        return "list"

    # 4. Project Scoped Operations
    if "project" in op_lower:
        if "list" in op_lower or (font_list_id and font_list_id != project_id):
            return "project_list"
        return "project"

    # 5. Document Scoped Operations
    if "document" in op_lower or "uploadsession" in op_lower:
        if project_id:
            return "document_project"
        return "document"

    # 6. Fallback based on payload keys if operation name is generic
    if project_id and list_type == "FONTLIST":
        return "project_list"
    if project_id or list_type == "FONTPROJECT":
        return "project"
    if list_type == "FAVORITE":
        return "favourite"
    if list_type == "FONTLIST" or font_list_id:
        return "list"
    if doc_id:
        return "document"

    return "global"


@dataclass
class CapturedEvent:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    url: str = ""
    method: str = "POST"
    operation_name: str = ""
    operation_type: str = "unknown"
    query: str = ""
    variables: dict[str, Any] = field(default_factory=dict)
    scenario: str = "global"
    target: str = "web"  # "web" or "app"
    header_values: HeaderValues = field(default_factory=HeaderValues)
    raw_request_headers: dict[str, str] = field(default_factory=dict)
    status_code: int = 0
    status_text: str = ""
    raw_response_headers: dict[str, str] = field(default_factory=dict)
    response_body: Any = None
    error: str = ""
    target_title: str = ""
    call_count: int = 1
    # Comparison machine state
    compare_status: str = "pending"  # pending, queued, comparing, compared_pass, compared_fail, skipped
    compare_job_id: Optional[str] = None
    compare_error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["header_values"] = self.header_values.to_dict()
        return d


@dataclass
class PausedRequest:
    """A request held by CDP Fetch until a QA action releases it."""

    fetch_id: str
    url: str
    method: str
    headers: dict[str, str] = field(default_factory=dict)
    post_data: str = ""
    operation_name: str = ""
    operation_type: str = "unknown"
    query: str = ""
    variables: dict[str, Any] = field(default_factory=dict)
    target: str = "web"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def create_batch_companion_event(event: CapturedEvent | dict[str, Any]) -> Optional[CapturedEvent]:
    """Given a captured batch-trigger mutation (e.g. bulkActivateStyles, bulkActivateLists, bulkActivateAll),
    generate its corresponding batch-orchestrator completion event (e.g. bulkActivateComplete).
    """
    if isinstance(event, CapturedEvent):
        op_name = event.operation_name or ""
        scen = event.scenario or "global"
        hv = event.header_values
        raw_req_h = event.raw_request_headers
        raw_res_h = event.raw_response_headers
        status_code = event.status_code
        status_text = event.status_text
        response_body = event.response_body
        variables = event.variables
        query = event.query
        url = event.url
        method = event.method
        target = event.target
        target_title = event.target_title
        ts = event.timestamp
    else:
        op_name = str(event.get("operation_name") or "")
        scen = str(event.get("scenario") or "global")
        hv_raw = event.get("header_values") or {}
        hv = HeaderValues(**hv_raw) if isinstance(hv_raw, dict) else HeaderValues()
        raw_req_h = event.get("raw_request_headers") or {}
        raw_res_h = event.get("raw_response_headers") or {}
        status_code = int(event.get("status_code", 200))
        status_text = str(event.get("status_text") or "OK")
        response_body = event.get("response_body")
        variables = event.get("variables") if isinstance(event.get("variables"), dict) else {}
        query = str(event.get("query") or "")
        url = str(event.get("url") or "")
        method = str(event.get("method") or "POST")
        target = str(event.get("target") or "web")
        target_title = str(event.get("target_title") or "")
        ts = str(event.get("timestamp") or datetime.now(timezone.utc).isoformat())

    op_lower = op_name.strip().lower()
    batch_op = BATCH_ORCHESTRATION_TRIGGER_MAP.get(op_lower)
    if not batch_op:
        return None

    return CapturedEvent(
        id=str(uuid.uuid4()),
        timestamp=ts,
        url=url,
        method="POST",
        operation_name=batch_op,
        operation_type="batch_orchestration",
        query=query,
        variables=variables,
        scenario=scen,
        target=target,
        header_values=hv,
        raw_request_headers=raw_req_h,
        status_code=status_code if status_code > 0 else 200,
        status_text=status_text or "OK",
        raw_response_headers=raw_res_h,
        response_body=response_body,
        target_title=target_title,
        call_count=1,
    )


def decode_jwt_claims(auth_token: str) -> dict[str, Any]:
    """Decode unverified JWT claims from Authorization header token."""
    if not auth_token or not isinstance(auth_token, str):
        return {}
    token = auth_token.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    try:
        payload_b64 = parts[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        decoded_bytes = base64.urlsafe_b64decode(payload_b64)
        data = json.loads(decoded_bytes.decode("utf-8"))
        if isinstance(data, dict):
            return {
                "sub": data.get("sub", ""),
                "email": data.get("https://api.monotype.com/email") or data.get("email", ""),
                "org_id": data.get("org_id") or data.get("https://api.monotype.com/org_id", ""),
                "gcid": data.get("https://api.monotype.com/gcid") or data.get("gcid", ""),
                "appName": data.get("https://api.monotype.com/appName") or data.get("appName", ""),
                "iss": data.get("iss", ""),
                "exp": data.get("exp"),
                "iat": data.get("iat"),
            }
    except Exception:
        pass
    return {}


def extract_headers(headers: dict[str, str]) -> HeaderValues:
    normalized = {k.lower(): str(v) for k, v in headers.items()}

    raw_auth = ""
    for k in ("authorization", "x-auth-token", "auth-token", "x-authorization", "bearer"):
        if k in normalized:
            raw_auth = normalized[k].strip()
            break

    bearer_token = raw_auth
    if raw_auth.lower().startswith("bearer "):
        bearer_token = raw_auth[7:].strip()

    auth_token = bearer_token

    correlation_id = ""
    for k in ("x-correlation-id", "correlation-id", "x-correlationid", "correlationid", "x-trace-id", "x-request-id"):
        if k in normalized:
            correlation_id = normalized[k].strip()
            break

    user_agent = normalized.get("user-agent", "")
    app_version = ""
    for k in ("x-app-version", "x-client-version", "x-monotype-app-version", "app-version", "version", "x-unified-version"):
        if k in normalized:
            app_version = normalized[k].strip()
            break

    if not app_version and user_agent:
        match = re.search(r"(?:MonotypeNextGen|MonotypeConnect|Monotype[A-Za-z0-9_-]+)/([0-9]+\.[0-9]+\.[0-9]+[a-zA-Z0-9._-]*)", user_agent, re.IGNORECASE)
        if match:
            app_version = match.group(1)
        else:
            match_electron = re.search(r"Electron/([0-9]+\.[0-9]+\.[0-9]+)", user_agent, re.IGNORECASE)
            if match_electron:
                app_version = f"Electron/{match_electron.group(1)}"

    event_version = 1
    for k in ("x-event-version", "event-version", "x-eventversion", "eventversion"):
        if k in normalized:
            try:
                event_version = int(normalized[k])
                break
            except (ValueError, TypeError):
                pass

    jwt_claims = decode_jwt_claims(bearer_token)

    return HeaderValues(
        auth_token=auth_token,
        bearer_token=bearer_token,
        correlation_id=correlation_id,
        user_agent=user_agent,
        app_version=app_version,
        event_version=event_version,
        jwt_claims=jwt_claims,
    )


def parse_graphql(post_data: Union[str, bytes, None]) -> Tuple[str, str, str, dict[str, Any]]:
    if not post_data:
        return "", "unknown", "", {}

    if isinstance(post_data, bytes):
        try:
            post_data = post_data.decode("utf-8")
        except UnicodeDecodeError:
            return "", "unknown", "", {}

    post_data_str = post_data.strip()
    if not post_data_str:
        return "", "unknown", "", {}

    try:
        data = json.loads(post_data_str)
        if isinstance(data, dict):
            op_name = data.get("operationName") or ""
            query = data.get("query") or ""
            variables = data.get("variables") if isinstance(data.get("variables"), dict) else {}

            op_type = _detect_graphql_op_type(query)
            if not op_name and query:
                match = re.search(
                    r"(?:query|mutation|subscription)\s+([A-Za-z0-9_]+)",
                    query,
                    re.IGNORECASE,
                )
                if match:
                    op_name = match.group(1)

            return str(op_name or ""), op_type, query, variables
        elif isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict):
                op_name = first.get("operationName") or "batch"
                query = first.get("query") or ""
                return str(op_name or "batch"), _detect_graphql_op_type(query) or "batch", query, {}
    except Exception:
        pass

    op_name = ""
    op_type = "unknown"
    match_op = re.search(r"operationName=([A-Za-z0-9_]+)", post_data_str)
    if match_op:
        op_name = match_op.group(1)

    match_type = re.search(
        r"\b(query|mutation|subscription)\s+([A-Za-z0-9_]+)",
        post_data_str,
        re.IGNORECASE,
    )
    if match_type:
        op_type = match_type.group(1).lower()
        if not op_name:
            op_name = match_type.group(2)

    return op_name, op_type, post_data_str if len(post_data_str) < 500 else post_data_str[:500] + "...", {}


def _detect_graphql_op_type(query: str) -> str:
    """Detect query/mutation/subscription even when fragments or comments precede the op."""
    if not query:
        return "unknown"
    q = query.strip()
    # Fast path
    low = q.lower()
    if low.startswith("mutation"):
        return "mutation"
    if low.startswith("query"):
        return "query"
    if low.startswith("subscription"):
        return "subscription"
    # Fragment-first / comment-first documents
    match = re.search(
        r"(?:^|[\n\r])\s*(query|mutation|subscription)\b",
        q,
        re.IGNORECASE | re.MULTILINE,
    )
    if match:
        return match.group(1).lower()
    match = re.search(r"\b(query|mutation|subscription)\s+[A-Za-z_]", q, re.IGNORECASE)
    if match:
        return match.group(1).lower()
    return "unknown"


_READ_NAME_PREFIXES = (
    "get",
    "list",
    "fetch",
    "search",
    "find",
    "load",
    "query",
    "is",
    "has",
    "count",
)


def _is_get_style_read(op_name: str, op_type: str, query: str) -> bool:
    """True for Get* / clearly read-style GraphQL operations."""
    name = (op_name or "").strip().lower()
    if name.startswith("get"):
        return True
    if (op_type or "").strip().lower() == "query" and name.startswith(_READ_NAME_PREFIXES):
        return True
    if query and re.search(r"\bquery\s+get[A-Za-z0-9_]*\b", query, re.IGNORECASE):
        return True
    return False


def _is_graphql_mutation(op_name: str, op_type: str, query: str) -> bool:
    """True when the request is (or strongly looks like) a GraphQL mutation."""
    t = (op_type or "").strip().lower()
    if t == "mutation":
        return True
    if t in {"query", "subscription"}:
        return False
    if query and _detect_graphql_op_type(query) == "mutation":
        return True
    if query and _detect_graphql_op_type(query) in {"query", "subscription"}:
        return False

    # Persisted queries often send operationName only — infer from naming.
    name = (op_name or "").strip().lower()
    if not name:
        return False
    if name.startswith(_READ_NAME_PREFIXES) or name.startswith("get"):
        return False
    mutation_hints = (
        "add",
        "remove",
        "create",
        "update",
        "delete",
        "activate",
        "deactivate",
        "bulk",
        "set",
        "save",
        "insert",
        "upsert",
        "move",
        "copy",
        "share",
        "invite",
        "assign",
        "unassign",
        "pin",
        "unpin",
        "favorite",
        "favourite",
        "login",
        "logout",
        "import",
        "export",
        "upload",
        "sync",
        "rename",
        "replace",
        "detach",
        "attach",
        "enable",
        "disable",
        "grant",
        "revoke",
    )
    return any(name.startswith(h) or h in name for h in mutation_hints)


def should_skip_captured_operation(
    op_name: str,
    op_type: str,
    query: str,
    *,
    ignore_get_operations: bool,
    ignore_query_operations: bool,
) -> bool:
    """Apply Live Capture filter checkboxes."""
    if ignore_query_operations:
        # Mutations only — drop anything that is not a mutation.
        return not _is_graphql_mutation(op_name, op_type, query)
    if ignore_get_operations and _is_get_style_read(op_name, op_type, query):
        return True
    return False


def check_cdp_ready(port: int = 9222) -> bool:
    """Check if CDP remote debugging port is open and responding."""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/json/version", headers={"User-Agent": "AuditInterceptor"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False


def get_cdp_targets(port: int = 9222) -> List[dict[str, Any]]:
    """Fetch active CDP inspection targets (pages, webviews, apps)."""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/json/list", headers={"User-Agent": "AuditInterceptor"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                return [
                    {
                        "id": t.get("id"),
                        "title": t.get("title"),
                        "type": t.get("type"),
                        "url": t.get("url"),
                        "webSocketDebuggerUrl": t.get("webSocketDebuggerUrl"),
                        "devtoolsFrontendUrl": t.get("devtoolsFrontendUrl"),
                    }
                    for t in data
                    if t.get("type") in ("page", "webview", "iframe", "app", "other")
                ]
    except Exception:
        pass
    return []


class StandaloneNetworkInterceptor:
    def __init__(
        self,
        port: int = 9222,
        filter_operation: Optional[str] = None,
        filter_url_keyword: str = "graph",
        ignore_get_operations: bool = True,
        ignore_query_operations: bool = False,
        target: str = "web",
        on_event_callback: Optional[Callable[[CapturedEvent], None]] = None,
        interception_mode: str = "observe",
        on_paused_callback: Optional[Callable[[PausedRequest], None]] = None,
    ) -> None:
        self.port = port
        self.filter_operation = filter_operation.strip() if filter_operation else None
        self.filter_url_keyword = filter_url_keyword.strip().lower() if filter_url_keyword else ""
        self.ignore_get_operations = ignore_get_operations
        self.ignore_query_operations = ignore_query_operations
        self.target = target  # "web" or "app"
        self.on_event_callback = on_event_callback
        self.interception_mode = interception_mode if interception_mode in {"observe", "pause"} else "observe"
        self.on_paused_callback = on_paused_callback
        self.captured_events: List[CapturedEvent] = []
        self._is_capturing = False
        self._browser: Any = None
        self._control_loop: Any = None
        self._paused_futures: dict[str, Any] = {}
        self._paused_sockets: dict[str, Any] = {}
        self._paused_requests: dict[str, PausedRequest] = {}
        self._command_counter = 1000

    def stop_capture(self) -> None:
        """Signal listener to stop capturing."""
        self._is_capturing = False

    def paused_requests(self) -> list[PausedRequest]:
        return list(self._paused_requests.values())

    def release_all_paused(self) -> None:
        for fetch_id in list(self._paused_requests):
            self.release_paused(fetch_id, "continue")

    async def _release_paused_async(self, fetch_id: str, method: str, params: dict[str, Any]) -> bool:
        ws = self._paused_sockets.get(fetch_id)
        future = self._paused_futures.get(fetch_id)
        if ws is None or future is None:
            return False
        self._command_counter += 1
        await ws.send_json({"id": self._command_counter, "method": method, "params": {"requestId": fetch_id, **params}})
        self._paused_sockets.pop(fetch_id, None)
        self._paused_futures.pop(fetch_id, None)
        self._paused_requests.pop(fetch_id, None)
        if not future.done():
            future.set_result(True)
        return True

    def release_paused(self, fetch_id: str, action: str, *, url: str | None = None,
                       method: str | None = None, headers: dict[str, str] | None = None,
                       post_data: str | None = None, response_code: int = 200,
                       response_body: Any = None) -> bool:
        """Release a paused request from a thread-safe HTTP control endpoint."""
        if self._control_loop is None:
            return False
        params: dict[str, Any] = {}
        command = "Fetch.continueRequest"
        if action == "abort":
            command = "Fetch.failRequest"
            params = {"errorReason": "Aborted"}
        elif action == "mock":
            command = "Fetch.fulfillRequest"
            body = response_body if isinstance(response_body, str) else json.dumps(response_body)
            params = {
                "responseCode": max(100, min(599, int(response_code))),
                "responseHeaders": [{"name": "Content-Type", "value": "application/json"}],
                "body": base64.b64encode(body.encode("utf-8")).decode("ascii"),
            }
        else:
            if url:
                params["url"] = url
            if method:
                params["method"] = method
            if headers is not None:
                params["headers"] = [{"name": str(k), "value": str(v)} for k, v in headers.items()]
            if post_data is not None:
                params["postData"] = base64.b64encode(post_data.encode("utf-8")).decode("ascii")
        future = asyncio.run_coroutine_threadsafe(
            self._release_paused_async(fetch_id, command, params), self._control_loop
        )
        try:
            return bool(future.result(timeout=3))
        except Exception:
            return False

    def _record_captured_event(self, event: CapturedEvent) -> None:
        """Record event, handle deduplication for overwrite operations, emit callbacks and companion batch events."""
        OVERWRITE_LATEST_OPERATIONS = {
            "getimportedfonts",
            "getssomappings",
            "getallaccessrequests",
            "getassetattachments",
            "getstylesofallfontlists",
            "getstyledocuments",
            "getstylecomments",
            "getcustomersettings",
            "getcategorizedglyphs",
            "getpackageid",
        }

        op_name_lower = (event.operation_name or "").strip().lower()
        is_target_op = op_name_lower in OVERWRITE_LATEST_OPERATIONS

        if is_target_op:
            op_key = (event.operation_name or "").strip()
            scen_key = (event.scenario or "global").strip()
            found_idx = -1
            for idx, existing in enumerate(self.captured_events):
                if (existing.operation_name or "").strip() == op_key and \
                   (existing.scenario or "global").strip() == scen_key:
                    found_idx = idx
                    break

            if found_idx >= 0:
                event.call_count = self.captured_events[found_idx].call_count + 1
                self.captured_events[found_idx] = event
            else:
                self.captured_events.append(event)
        else:
            self.captured_events.append(event)

        log.info(
            "Captured Event [%s] op=%s status=%d auth=%s corr_id=%s (x%d)",
            event.method,
            event.operation_name or "unknown",
            event.status_code,
            "YES" if event.header_values.auth_token else "NO",
            event.header_values.correlation_id or "N/A",
            event.call_count,
        )

        if self.on_event_callback:
            try:
                self.on_event_callback(event)
            except Exception as cb_err:
                log.warning("Callback error: %s", cb_err)

        # Check and automatically emit companion batch-orchestration event
        if op_name_lower in BATCH_ORCHESTRATION_TRIGGER_MAP:
            companion = create_batch_companion_event(event)
            if companion:
                self.captured_events.append(companion)
                log.info(
                    "Generated Companion Batch Event [%s] op=%s status=%d auth=%s corr_id=%s",
                    companion.method,
                    companion.operation_name,
                    companion.status_code,
                    "YES" if companion.header_values.auth_token else "NO",
                    companion.header_values.correlation_id or "N/A",
                )
                if self.on_event_callback:
                    try:
                        self.on_event_callback(companion)
                    except Exception as cb_err:
                        log.warning("Companion callback error: %s", cb_err)

    async def _listen_cdp_native_async(self, duration_sec: float = 0.0) -> List[CapturedEvent]:
        """Native Chrome DevTools Protocol listener using WebSockets. Works on Electron & Chrome without browser context protocol issues."""
        import asyncio
        import aiohttp
        import base64
        import json

        cdp_base = f"http://127.0.0.1:{self.port}"
        log.info("Connecting native CDP WebSocket listener at %s...", cdp_base)

        hooked_target_ids: set[str] = set()
        active_target_tasks: dict[str, asyncio.Task] = {}
        pending_requests: dict[str, CapturedEvent] = {}
        pending_bodies: dict[int, str] = {}
        cmd_counter = 100

        async def _handle_target(target: dict[str, Any], session: aiohttp.ClientSession) -> None:
            nonlocal cmd_counter
            t_id = target.get("id") or ""
            ws_url = target.get("webSocketDebuggerUrl")
            title = target.get("title") or target.get("url") or "Target"
            if not ws_url or t_id in hooked_target_ids:
                return

            hooked_target_ids.add(t_id)
            log.info("Hooking CDP target [%s] (%s): %s", target.get("type"), title, ws_url)

            try:
                async with session.ws_connect(ws_url, max_msg_size=30 * 1024 * 1024) as ws:
                    self._control_loop = asyncio.get_running_loop()
                    await ws.send_json({
                        "id": 1,
                        "method": "Network.enable",
                        "params": {"maxTotalBufferSize": 20000000, "maxResourceBufferSize": 10000000}
                    })
                    if self.interception_mode == "pause":
                        await ws.send_json({
                            "id": 2,
                            "method": "Fetch.enable",
                            "params": {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]},
                        })

                    while self._is_capturing:
                        try:
                            msg = await asyncio.wait_for(ws.receive_json(), timeout=0.5)
                        except asyncio.TimeoutError:
                            continue
                        except Exception:
                            break

                        method = msg.get("method", "")
                        params = msg.get("params", {})

                        if method == "Fetch.requestPaused" and self.interception_mode == "pause":
                            fetch_id = str(params.get("requestId") or "")
                            req = params.get("request", {})
                            url = req.get("url", "")
                            post_data = req.get("postData") or ""
                            op_name, op_type, query, variables = parse_graphql(post_data)
                            matches_url = not self.filter_url_keyword or self.filter_url_keyword in url.lower() or (
                                self.filter_url_keyword == "graphql" and "/graph" in url.lower()
                            )
                            matches_operation = not self.filter_operation or (
                                self.filter_operation.lower() in op_name.lower() or self.filter_operation.lower() in query.lower()
                            )
                            matches_filters = not should_skip_captured_operation(
                                op_name, op_type, query,
                                ignore_get_operations=self.ignore_get_operations,
                                ignore_query_operations=self.ignore_query_operations,
                            )
                            if not matches_url or not matches_operation or not matches_filters:
                                await ws.send_json({"id": 3, "method": "Fetch.continueRequest", "params": {"requestId": fetch_id}})
                                continue

                            request_headers = {
                                str(item.get("name")): str(item.get("value", ""))
                                for item in req.get("headers", [])
                            } if isinstance(req.get("headers"), list) else dict(req.get("headers", {}))
                            paused = PausedRequest(
                                fetch_id=fetch_id,
                                url=url,
                                method=req.get("method", "POST"),
                                headers=request_headers,
                                post_data=post_data,
                                operation_name=op_name,
                                operation_type=op_type,
                                query=query,
                                variables=variables,
                                target=self.target,
                            )
                            self._paused_requests[fetch_id] = paused
                            self._paused_sockets[fetch_id] = ws
                            future = asyncio.get_running_loop().create_future()
                            self._paused_futures[fetch_id] = future
                            if self.on_paused_callback:
                                try:
                                    self.on_paused_callback(paused)
                                except Exception as callback_error:
                                    log.warning("Paused callback error: %s", callback_error)
                            try:
                                await asyncio.wait_for(future, timeout=120)
                            except asyncio.TimeoutError:
                                await self._release_paused_async(fetch_id, "Fetch.continueRequest", {})

                        elif method == "Network.requestWillBeSent":
                            req_id = params.get("requestId")
                            req = params.get("request", {})
                            url = req.get("url", "")

                            if self.filter_url_keyword:
                                kw = self.filter_url_keyword.lower()
                                if kw not in url.lower() and not (kw == "graphql" and "/graph" in url.lower()):
                                    continue

                            post_data = req.get("postData")
                            op_name, op_type, query, variables = parse_graphql(post_data)

                            if should_skip_captured_operation(
                                op_name,
                                op_type,
                                query,
                                ignore_get_operations=self.ignore_get_operations,
                                ignore_query_operations=self.ignore_query_operations,
                            ):
                                continue

                            if self.filter_operation:
                                target_op = self.filter_operation.lower()
                                if target_op not in op_name.lower() and target_op not in query.lower():
                                    continue

                            req_headers = dict(req.get("headers", {}))
                            header_vals = extract_headers(req_headers)
                            scen = detect_scenario(op_name, variables, query)

                            detected_target = self.target
                            ua = (header_vals.user_agent or "").lower()
                            if "electron" in ua or "monotypenextgen" in ua or "monotype connect" in ua or "connectservice" in ua:
                                detected_target = "app"

                            event = CapturedEvent(
                                url=url,
                                method=req.get("method", "POST"),
                                operation_name=op_name,
                                operation_type=op_type,
                                query=query,
                                variables=variables,
                                header_values=header_vals,
                                scenario=scen,
                                target=detected_target,
                                raw_request_headers=req_headers,
                            )
                            pending_requests[req_id] = event

                        elif method == "Network.responseReceived":
                            req_id = params.get("requestId")
                            resp = params.get("response", {})
                            if req_id in pending_requests:
                                event = pending_requests[req_id]
                                event.status_code = resp.get("status", 0)
                                resp_headers = dict(resp.get("headers", {}))
                                event.raw_response_headers = resp_headers
                                resp_headers_norm = {k.lower(): v for k, v in resp_headers.items()}
                                for hk in ("x-correlation-id", "xcorrelationid", "correlation-id", "correlationid"):
                                    if hk in resp_headers_norm and not event.header_values.correlation_id:
                                        event.header_values.correlation_id = resp_headers_norm[hk].strip()
                                        break

                        elif method == "Network.loadingFinished":
                            req_id = params.get("requestId")
                            if req_id in pending_requests:
                                cmd_counter += 1
                                current_cmd = cmd_counter
                                pending_bodies[current_cmd] = req_id
                                try:
                                    await ws.send_json({
                                        "id": current_cmd,
                                        "method": "Network.getResponseBody",
                                        "params": {"requestId": req_id}
                                    })
                                except Exception:
                                    pass

                        elif "id" in msg and msg["id"] in pending_bodies:
                            c_id = msg["id"]
                            req_id = pending_bodies.pop(c_id, None)
                            if req_id and req_id in pending_requests:
                                event = pending_requests.pop(req_id)
                                res = msg.get("result", {})
                                body_str = res.get("body", "")
                                if res.get("base64Encoded"):
                                    try:
                                        body_str = base64.b64decode(body_str).decode("utf-8", errors="replace")
                                    except Exception:
                                        pass
                                try:
                                    event.response_body = json.loads(body_str)
                                except Exception:
                                    event.response_body = body_str[:2000] if body_str else None

                                if not event.header_values.correlation_id and isinstance(event.response_body, dict):
                                    for rk in ("xCorrelationId", "correlationId", "correlation_id"):
                                        if event.response_body.get(rk):
                                            event.header_values.correlation_id = str(event.response_body[rk]).strip()
                                            break

                                self._record_captured_event(event)

                        elif method == "Network.loadingFailed":
                            req_id = params.get("requestId")
                            if req_id in pending_requests:
                                event = pending_requests.pop(req_id)
                                event.error = params.get("errorText", "Request failed")
                                self._record_captured_event(event)

            except Exception as err:
                log.debug("Target %s connection closed: %s", title, err)
            finally:
                hooked_target_ids.discard(t_id)

        async with aiohttp.ClientSession() as session:
            start_time = time.monotonic()
            log.info("Native CDP listener active on port %d...", self.port)
            while self._is_capturing:
                try:
                    targets = get_cdp_targets(self.port)
                    for t in targets:
                        t_id = t.get("id") or ""
                        t_type = t.get("type", "")
                        if t_type in ("page", "webview", "app", "other") and t_id not in hooked_target_ids:
                            task = asyncio.create_task(_handle_target(t, session))
                            active_target_tasks[t_id] = task
                except Exception as err:
                    log.debug("Polling targets error: %s", err)

                done_keys = [k for k, v in active_target_tasks.items() if v.done()]
                for k in done_keys:
                    active_target_tasks.pop(k, None)

                if duration_sec > 0 and (time.monotonic() - start_time) >= duration_sec:
                    break

                await asyncio.sleep(0.5)

            for task in active_target_tasks.values():
                task.cancel()

        return self.captured_events

    def _listen_playwright(self, duration_sec: float = 0.0) -> List[CapturedEvent]:
        """Fallback Playwright listener."""
        from playwright.sync_api import sync_playwright

        cdp_url = f"http://127.0.0.1:{self.port}"
        log.info("Connecting via Playwright to CDP at %s...", cdp_url)

        with sync_playwright() as p:
            self._browser = p.chromium.connect_over_cdp(cdp_url)
            self._is_capturing = True
            log.info("Playwright connected to CDP port %d.", self.port)

            pending_requests: Dict[Any, CapturedEvent] = {}
            hooked_pages: set = set()

            def handle_request(request: Any) -> None:
                if not self._is_capturing:
                    return
                url = request.url
                method = request.method
                if self.filter_url_keyword:
                    kw = self.filter_url_keyword.lower()
                    if kw not in url.lower() and not (kw == "graphql" and "/graph" in url.lower()):
                        return

                post_data = request.post_data
                op_name, op_type, query, variables = parse_graphql(post_data)

                if should_skip_captured_operation(
                    op_name,
                    op_type,
                    query,
                    ignore_get_operations=self.ignore_get_operations,
                    ignore_query_operations=self.ignore_query_operations,
                ):
                    return
                if self.filter_operation:
                    target_op = self.filter_operation.lower()
                    if target_op not in op_name.lower() and target_op not in query.lower():
                        return

                req_headers = dict(request.headers)
                header_vals = extract_headers(req_headers)
                scen = detect_scenario(op_name, variables, query)

                detected_target = self.target
                ua = (header_vals.user_agent or "").lower()
                if "electron" in ua or "monotypenextgen" in ua or "monotype connect" in ua or "connectservice" in ua:
                    detected_target = "app"

                event = CapturedEvent(
                    url=url,
                    method=method,
                    operation_name=op_name,
                    operation_type=op_type,
                    query=query,
                    variables=variables,
                    header_values=header_vals,
                    scenario=scen,
                    target=detected_target,
                    raw_request_headers=req_headers,
                )
                pending_requests[request] = event

            def handle_response(response: Any) -> None:
                if not self._is_capturing:
                    return
                request = response.request
                event = pending_requests.pop(request, None)
                if not event:
                    return

                event.status_code = response.status
                try:
                    resp_headers = dict(response.headers)
                    event.raw_response_headers = resp_headers
                    resp_headers_norm = {k.lower(): v for k, v in resp_headers.items()}
                    for hk in ("x-correlation-id", "xcorrelationid", "correlation-id", "correlationid"):
                        if hk in resp_headers_norm and not event.header_values.correlation_id:
                            event.header_values.correlation_id = resp_headers_norm[hk].strip()
                            break
                except Exception:
                    pass

                try:
                    event.response_body = response.json()
                except Exception:
                    try:
                        text = response.text()
                        event.response_body = text[:2000] if text else None
                    except Exception as err:
                        event.error = f"Could not read response body: {err}"

                if not event.header_values.correlation_id and isinstance(event.response_body, dict):
                    for rk in ("xCorrelationId", "correlationId", "correlation_id"):
                        if event.response_body.get(rk):
                            event.header_values.correlation_id = str(event.response_body[rk]).strip()
                            break

                self._record_captured_event(event)

            def hook_page(page: Any) -> None:
                if page in hooked_pages:
                    return
                hooked_pages.add(page)
                try:
                    page.on("request", handle_request)
                    page.on("response", handle_response)
                except Exception as err:
                    log.debug("Error hooking page: %s", err)

            def hook_context(ctx: Any) -> None:
                try:
                    ctx.on("page", hook_page)
                except Exception:
                    pass
                for page in ctx.pages:
                    hook_page(page)

            try:
                self._browser.on("context", hook_context)
            except Exception:
                pass

            for ctx in self._browser.contexts:
                hook_context(ctx)

            start_time = time.monotonic()
            try:
                while self._is_capturing:
                    for ctx in list(self._browser.contexts):
                        hook_context(ctx)
                        for page in list(ctx.pages):
                            hook_page(page)
                            try:
                                page.wait_for_timeout(100)
                            except Exception:
                                pass
                    if duration_sec > 0 and (time.monotonic() - start_time) >= duration_sec:
                        break
                    time.sleep(0.2)
            except KeyboardInterrupt:
                log.info("Interrupted.")
            finally:
                self._is_capturing = False

        return self.captured_events

    def start_listening(self, duration_sec: float = 0.0) -> List[CapturedEvent]:
        """Start listening on the CDP port. Uses native CDP WebSockets with Playwright fallback."""
        if not check_cdp_ready(self.port):
            raise RuntimeError(
                f"CDP port {self.port} is not reachable. Make sure App or Chrome is running with --remote-debugging-port={self.port}."
            )

        self._is_capturing = True
        try:
            import asyncio
            return asyncio.run(self._listen_cdp_native_async(duration_sec=duration_sec))
        except Exception as cdp_err:
            log.warning("Native CDP listener failed or interrupted (%s); falling back to Playwright if available", cdp_err)
            try:
                return self._listen_playwright(duration_sec=duration_sec)
            except Exception as pw_err:
                log.error("Both CDP and Playwright listeners failed: %s / %s", cdp_err, pw_err)
                raise
        finally:
            self._is_capturing = False

        return self.captured_events


def group_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate network events by operation_name and scenario, returning 1 row per unique pair."""
    grouped: Dict[str, Dict[str, Any]] = {}
    for evt in events:
        op_name = evt.get("operation_name") or ""
        scenario = evt.get("scenario") or "global"
        key = f"{op_name}:::{scenario}" if op_name else (evt.get("url") or "unknown_event")

        if key not in grouped:
            item = dict(evt)
            item["call_count"] = 1
            grouped[key] = item
        else:
            prev_count = grouped[key]["call_count"] + 1
            grouped[key] = dict(evt)
            grouped[key]["call_count"] = prev_count

    return list(grouped.values())


def build_event_composite_payload(event: dict[str, Any] | CapturedEvent) -> dict[str, Any]:
    """Construct complete enriched payload container including eventVersion, useragent, query, variables, and jwtClaims."""
    if isinstance(event, CapturedEvent):
        evt = event.to_dict()
    else:
        evt = dict(event)

    hv = evt.get("header_values") or {}
    variables = evt.get("variables") or {}
    raw_headers = evt.get("raw_request_headers") or {}

    event_version = hv.get("event_version", 1)
    if event_version == 1 and isinstance(variables, dict) and "eventVersion" in variables:
        try:
            event_version = int(variables["eventVersion"])
        except (ValueError, TypeError):
            pass

    raw_token = hv.get("bearer_token") or hv.get("auth_token") or ""
    if raw_token.lower().startswith("bearer "):
        raw_token = raw_token[7:].strip()

    jwt_claims = hv.get("jwt_claims") or decode_jwt_claims(raw_token)
    user_agent = hv.get("user_agent") or raw_headers.get("user-agent") or raw_headers.get("User-Agent") or ""
    app_version = hv.get("app_version") or ""

    return {
        "operationName": evt.get("operation_name") or "",
        "operationType": evt.get("operation_type") or "unknown",
        "scenario": evt.get("scenario") or "global",
        "eventVersion": event_version,
        "userAgent": user_agent,
        "appVersion": app_version,
        "correlationId": hv.get("correlation_id") or "",
        "authToken": raw_token,
        "timestamp": evt.get("timestamp") or "",
        "url": evt.get("url") or "",
        "query": evt.get("query") or "",
        "variables": variables,
        "jwtClaims": jwt_claims,
    }


def validate_network_event(evt: Dict[str, Any]) -> Dict[str, str]:
    issues: List[str] = []
    severity = "OK"

    status_code = evt.get("status_code", 0)
    hv = evt.get("header_values") or {}
    auth = hv.get("bearer_token") or hv.get("auth_token") or ""
    correlation_id = hv.get("correlation_id") or ""
    op_state = (evt.get("operation_state") or "").lower()
    resp_body = evt.get("response_body")

    if status_code >= 500:
        issues.append(f"HTTP {status_code} server error")
        severity = "Critical"
    elif 400 <= status_code < 500:
        issues.append(f"HTTP {status_code} client error")
        if severity not in ("Critical",):
            severity = "High"

    if not auth.strip():
        issues.append("Missing auth token")
        if severity not in ("Critical", "High"):
            severity = "High"

    if op_state and op_state not in ("success", "ok", ""):
        issues.append(f"operationState={op_state}")
        if severity not in ("Critical", "High"):
            severity = "Medium"

    if not correlation_id.strip():
        issues.append("Missing correlation_id")
        if severity == "OK":
            severity = "Medium"

    if resp_body is None or resp_body == "" or resp_body == {}:
        issues.append("Empty response body")
        if severity == "OK":
            severity = "Low"

    validation = "PASS" if not issues else ("WARN" if severity in ("Low", "Medium") else "FAIL")
    return {
        "severity": severity if issues else "OK",
        "validation": validation,
        "notes": "; ".join(issues) if issues else "",
    }


def export_events_to_excel(
    events: List[Dict[str, Any]],
    output_path: Optional[Union[str, Path]] = None,
    event_ids: Optional[List[str]] = None,
) -> Path:
    """Export captured network events to an Excel spreadsheet matching the exact required format."""
    import pandas as pd

    if not output_path:
        output_path = Path.cwd() / "output" / "audit_results.xlsx"

    target_events = events
    if event_ids:
        ids_set = set(event_ids)
        target_events = [e for e in events if str(e.get("id")) in ids_set]

    rows = []
    for evt in target_events:
        op_name = evt.get("operation_name") or "unknown_event"
        status_code = evt.get("status_code", 0)
        status_str = "OK" if 200 <= status_code < 400 else ("FAIL" if status_code > 0 else "UNKNOWN")

        hv = evt.get("header_values") or {}
        raw_auth = hv.get("bearer_token") or hv.get("auth_token") or ""
        if raw_auth.lower().startswith("bearer "):
            raw_auth = raw_auth[7:].strip()

        correlation_id = hv.get("correlation_id") or ""
        url = evt.get("url") or ""
        ua = (hv.get("user_agent") or "").lower()
        target = evt.get("target") or ("app" if "app" in url.lower() or "electron" in ua or "monotypenextgen" in ua else "web")
        scenario = evt.get("scenario") or detect_scenario(op_name, evt.get("variables"), evt.get("query") or "")

        composite_payload = build_event_composite_payload(evt)

        resp_body = evt.get("response_body")
        if isinstance(resp_body, (dict, list)):
            resp_str = json.dumps(resp_body)
        else:
            resp_str = str(resp_body) if resp_body is not None else ""

        vld = validate_network_event(evt)
        rows.append({
            "event_name": op_name,
            "scenario": scenario,
            "target": target,
            "correlation_id": correlation_id,
            "auth_token": raw_auth,
            "http_status": status_code,
            "status": status_str,
            "severity": vld["severity"],
            "validation": vld["validation"],
            "notes": vld["notes"],
            "response": resp_str,
            "payload": json.dumps(composite_payload),
        })

    df = pd.DataFrame(
        rows,
        columns=[
            "event_name",
            "scenario",
            "target",
            "correlation_id",
            "auth_token",
            "http_status",
            "status",
            "severity",
            "validation",
            "notes",
            "response",
            "payload",
        ],
    )
    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(out_p, index=False)
    log.info("Exported %d network events to Excel at %s", len(rows), out_p)
    return out_p
