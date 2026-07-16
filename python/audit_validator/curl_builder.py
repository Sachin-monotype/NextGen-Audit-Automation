"""Build copy-pasteable curl commands for an operation (GraphQL or Ingress).

Given an operation and (optionally) a captured raw event, produce a curl that a
QA can paste into a terminal / Postman to re-trigger the same call:

- GraphQL ops (mtconnect-api / NextGen /graph): POST the operation's GraphQL
  document with variables taken from the captured raw event's
  ``subject.metadata.input`` (the exact input that produced the event).
- Ingress ops (desktop / plugin / UI, service != mtconnect-api): POST the raw
  audit envelope to the resolver Ingress API.

The bearer token is embedded directly (from ``BEARER_TOKEN`` env) so the copied
curl is immediately runnable. If no token is configured it falls back to the
``$BEARER_TOKEN`` placeholder.
"""

from __future__ import annotations

import json
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .utility.operation_graphql import (
    get_document_for_operation,
    is_nextgen_ui_operation,
)

_UI_NAV_FILE = Path(__file__).resolve().parent / "data" / "ui_navigation.json"


def _resolve_bearer() -> tuple[str, bool]:
    """Return (authorization_value, is_real_token).

    Reads the real token from env so the copied curl is directly runnable.
    Falls back to the ``$BEARER_TOKEN`` shell placeholder when nothing is set.
    """
    for key in ("INGRESS_BEARER_TOKEN", "BEARER_TOKEN_PP", "BEARER_TOKEN"):
        token = (os.getenv(key) or "").strip()
        if token:
            token = token[len("Bearer ") :].strip() if token.lower().startswith("bearer ") else token
            return f"Bearer {token}", True
    return "Bearer $BEARER_TOKEN", False

# Services that reach the resolver through the Ingress REST API (not GraphQL).
_INGRESS_SERVICES = {
    "mtconnect-ui",
    "mtconnect-desktop",
    "monotype-connect-electron",
    "MonotypeNextGenConnectService",
    "MonotypeFontBridge",
    "Plugin",
    "ElectronPanel",
    "mt-login-service",
}

_DEFAULT_INGRESS_URL = (
    "https://mt-audit-log-resolver-service-preprod.monotype-pp.com/v1/audit-events"
)


@dataclass
class CurlResult:
    operation: str
    kind: str  # graphql | ingress | unknown
    endpoint: str
    curl: str
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "kind": self.kind,
            "endpoint": self.endpoint,
            "curl": self.curl,
            "note": self.note,
        }


def _graphql_endpoint(operation: str) -> str:
    if is_nextgen_ui_operation(operation):
        return os.getenv(
            "NEXTGEN_GRAPHQL_ENDPOINT", "https://nextgen.monotype-pp.com/graph"
        )
    return os.getenv("GRAPHQL_ENDPOINT", "https://nextgen.monotype-pp.com/graphql")


def _ingress_endpoint() -> str:
    return (os.getenv("INGRESS_API_URL") or _DEFAULT_INGRESS_URL).rstrip("/")


def _input_from_raw(raw: dict[str, Any] | None) -> dict[str, Any]:
    if not raw:
        return {}
    subject = raw.get("subject") or {}
    meta = subject.get("metadata") or {}
    inp = meta.get("input")
    return inp if isinstance(inp, dict) else {}


def _service_of(raw: dict[str, Any] | None) -> str:
    if not raw:
        return ""
    return str((raw.get("source") or {}).get("service") or "")


def _is_ingress(operation: str, raw: dict[str, Any] | None) -> bool:
    if get_document_for_operation(operation):
        return False
    svc = _service_of(raw)
    if svc and svc in _INGRESS_SERVICES:
        return True
    # No GraphQL document and desktop/plugin-ish → ingress
    return svc not in ("mtconnect-api", "")


def _multiline_curl(method: str, url: str, headers: dict[str, str], body: str) -> str:
    parts = [f"curl -X {method} {shlex.quote(url)}"]
    for key, val in headers.items():
        parts.append(f"  -H {shlex.quote(f'{key}: {val}')}")
    parts.append(f"  -d {shlex.quote(body)}")
    return " \\\n".join(parts)


def build_graphql_curl(operation: str, raw: dict[str, Any] | None) -> CurlResult | None:
    document = get_document_for_operation(operation)
    if not document:
        return None
    endpoint = _graphql_endpoint(operation)
    variables = _input_from_raw(raw)
    # Most NextGen mutations take a single `input` argument.
    payload: dict[str, Any] = {"query": document}
    if variables:
        payload["variables"] = {"input": variables}
    body = json.dumps(payload, ensure_ascii=False, default=str)
    authorization, real_token = _resolve_bearer()
    headers = {
        "Content-Type": "application/json",
        "Authorization": authorization,
        "Accept": "application/json",
    }
    token_note = "" if real_token else " Set BEARER_TOKEN in .env for a ready-to-run token."
    note = ("Variables taken from the captured raw event's subject.metadata.input." + token_note).strip()
    if not variables:
        note = ("No captured input found — fill `variables` before sending." + token_note).strip()
    return CurlResult(
        operation=operation,
        kind="graphql",
        endpoint=endpoint,
        curl=_multiline_curl("POST", endpoint, headers, body),
        note=note,
    )


def _clean_envelope(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Drop Mongo-only fields so the envelope is a valid Ingress payload."""
    if not raw:
        return {}
    cleaned = {k: v for k, v in raw.items() if k not in ("_id", "receivedAt", "insertedAt")}
    return cleaned


def build_ingress_curl(operation: str, raw: dict[str, Any] | None) -> CurlResult:
    endpoint = _ingress_endpoint()
    envelope = _clean_envelope(raw) or {"source": {"operation": operation}}
    # Ingress API accepts a one-element array of audit envelopes.
    # default=str safely stringifies any residual Mongo types (ObjectId, datetime).
    body = json.dumps([envelope], ensure_ascii=False, default=str)
    cid = str((raw or {}).get("xCorrelationId") or "")
    authorization, real_token = _resolve_bearer()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": authorization,
        "x-request-source": os.getenv("INGRESS_REQUEST_SOURCE", "MT_CONNECT_BS"),
        "x-os-platform": os.getenv("INGRESS_OS_PLATFORM", "MAC"),
    }
    if cid:
        headers["x-correlation-id"] = cid
    token_note = "" if real_token else " Set BEARER_TOKEN in .env for a ready-to-run token."
    note = ("Replays the captured raw audit envelope through the resolver Ingress API." + token_note).strip()
    if not raw:
        note = "No captured raw event — this is a skeleton; fill the envelope before sending."
    return CurlResult(
        operation=operation,
        kind="ingress",
        endpoint=endpoint,
        curl=_multiline_curl("POST", endpoint, headers, body),
        note=note,
    )


def build_curl(operation: str, raw: dict[str, Any] | None = None) -> CurlResult:
    """Build the most appropriate curl for an operation given an optional raw event."""
    if _is_ingress(operation, raw):
        return build_ingress_curl(operation, raw)
    gql = build_graphql_curl(operation, raw)
    if gql:
        return gql
    # Fallback: ingress skeleton
    return build_ingress_curl(operation, raw)


def load_ui_navigation() -> dict[str, Any]:
    if _UI_NAV_FILE.is_file():
        return json.loads(_UI_NAV_FILE.read_text(encoding="utf-8"))
    return {}


def ui_navigation_for(operation: str) -> dict[str, Any]:
    return load_ui_navigation().get(operation, {})
