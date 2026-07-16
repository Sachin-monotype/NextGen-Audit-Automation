"""Export GraphQL simulation flows as a Postman collection + environment."""

from __future__ import annotations

import copy
import json
import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from .operation_graphql import get_export_for_operation, is_nextgen_ui_operation
from .operation_meta import (
    CurlContext,
    OPERATION_VARIABLES_TEMPLATE,
    _PLACEHOLDER,
    build_simulation_curl,
    load_curl_context,
    resolve_simulation_variables,
    ui_navigation,
)
from ..simulation.client import apollo_operation_name
from ..simulation.flow_catalog import audit_operation, flow_operations
from ..simulation.graphql_loader import get_document

log = logging.getLogger(__name__)

POSTMAN_SCHEMA = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"


@dataclass(frozen=True)
class PostmanExportResult:
    collection_path: Path
    environment_path: Path
    request_count: int
    validation_path: Path | None = None


def _postman_placeholder_map(ctx: CurlContext, project_root: Path) -> dict[str, str]:
    from .operation_meta import _placeholder_map

    return _placeholder_map(ctx, project_root)


def resolve_simulation_variables_for_postman(
    label: str,
    *,
    project_root: Path,
    ctx: CurlContext | None = None,
) -> dict[str, Any]:
    curl_ctx = ctx or load_curl_context(project_root)
    return resolve_simulation_variables(label, curl_ctx, project_root)


def _postman_variables(project_root: Path, ctx: CurlContext) -> list[dict[str, str]]:
    raw = {k: v for k, v in dotenv_values(project_root / ".env").items() if v is not None}
    secondary = raw.get("BEARER_TOKEN_SECONDARY", "")
    if secondary.lower().startswith("bearer "):
        secondary = secondary[7:].strip()
    use_ctx = raw.get("GRAPHQL_USE_CUSTOMER_CONTEXT", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    placeholders = _postman_placeholder_map(ctx, project_root)
    entries = [
        ("graphql_endpoint", ctx.endpoint),
        ("nextgen_graphql_endpoint", ctx.nextgen_endpoint),
        ("nextgen_origin", ctx.nextgen_origin),
        ("nextgen_referer", ctx.nextgen_referer),
        ("bearer_token", ctx.bearer_token),
        ("secondary_bearer_token", secondary),
        ("accept_language", raw.get("ACCEPT_LANGUAGE", "en")),
        ("use_customer_context", "true" if use_ctx else "false"),
        ("customer_context_id", raw.get("GRAPHQL_CONTEXT_CUSTOMER_ID", ctx.customer_id)),
    ]
    for key, value in placeholders.items():
        entries.append((key.lower(), value))
    return [{"key": k, "value": v, "type": "string"} for k, v in entries if v is not None]


def _graphql_body(query: str, variables: dict[str, Any], *, graphql_operation: str = "") -> str:
    if is_nextgen_ui_operation(graphql_operation):
        export = get_export_for_operation(graphql_operation) or graphql_operation
        return json.dumps(
            {
                "operationName": apollo_operation_name(export),
                "variables": variables,
                "extensions": {"clientLibrary": {"name": "@apollo/client", "version": "4.0.9"}},
                "query": query,
            },
            indent=2,
        )
    return json.dumps({"query": query, "variables": variables}, indent=2)


def _request_headers(*, bearer_var: str, graphql_operation: str = "") -> list[dict[str, str]]:
    headers = [
        {"key": "Authorization", "value": f"Bearer {{{{{bearer_var}}}}}", "type": "text"},
        {"key": "Content-Type", "value": "application/json", "type": "text"},
        {"key": "accept-language", "value": "{{accept_language}}", "type": "text"},
        {
            "key": "x-context-customerid",
            "value": "{{customer_context_id}}",
            "type": "text",
            "disabled": True,
        },
    ]
    if is_nextgen_ui_operation(graphql_operation):
        headers.extend(
            [
                {
                    "key": "accept",
                    "value": "application/graphql-response+json,application/json;q=0.9",
                    "type": "text",
                },
                {"key": "origin", "value": "{{nextgen_origin}}", "type": "text"},
                {"key": "referer", "value": "{{nextgen_referer}}", "type": "text"},
            ]
        )
    return headers


def _test_script() -> list[str]:
    return [
        "pm.test('HTTP 200', function () {",
        "    pm.response.to.have.status(200);",
        "});",
        "pm.test('No GraphQL errors', function () {",
        "    const body = pm.response.json();",
        "    pm.expect(body.errors, JSON.stringify(body.errors || [])).to.be.undefined;",
        "});",
    ]


def _validate_request(
    *,
    op_label: str,
    graphql_operation: str,
    uses_secondary: bool,
    skipped: bool,
    query: str,
    variables: dict[str, Any],
    project_root: Path,
    ctx: CurlContext,
) -> tuple[str, str]:
    if skipped:
        return "SKIP", "skipped by default in automation"

    raw = {k: v for k, v in dotenv_values(project_root / ".env").items() if v is not None}
    token = raw.get("BEARER_TOKEN_SECONDARY" if uses_secondary else "BEARER_TOKEN", "")
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        return "SKIP", "missing bearer token in .env"

    unresolved = re.findall(r"\$[A-Z0-9_]+", json.dumps(variables))
    if unresolved:
        return "SKIP", f"unresolved: {', '.join(sorted(set(unresolved)))}"

    try:
        import requests

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "accept-language": raw.get("ACCEPT_LANGUAGE", "en"),
        }
        use_ctx = raw.get("GRAPHQL_USE_CUSTOMER_CONTEXT", "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        context_id = raw.get("GRAPHQL_CONTEXT_CUSTOMER_ID", ctx.customer_id)
        if use_ctx and context_id:
            headers["x-context-customerid"] = context_id

        if is_nextgen_ui_operation(graphql_operation):
            export = get_export_for_operation(graphql_operation) or graphql_operation
            headers.update(
                {
                    "accept": "application/graphql-response+json,application/json;q=0.9",
                    "origin": ctx.nextgen_origin,
                    "referer": ctx.nextgen_referer,
                }
            )
            endpoint = ctx.nextgen_endpoint
            payload = {
                "operationName": apollo_operation_name(export),
                "variables": variables,
                "extensions": {"clientLibrary": {"name": "@apollo/client", "version": "4.0.9"}},
                "query": query,
            }
        else:
            endpoint = ctx.endpoint
            payload = {"query": query, "variables": variables}

        resp = requests.post(
            endpoint,
            headers=headers,
            json=payload,
            timeout=120,
        )
        body = resp.json()
        if body.get("errors"):
            err = body["errors"][0].get("message", str(body["errors"]))[:200]
            return "FAIL", err

        data = body.get("data") or {}
        field = data.get(graphql_operation)
        if field is None and data:
            field = next(iter(data.values()), None)
        if isinstance(field, dict) and field.get("errors"):
            return "FAIL", str(field["errors"])[:200]
        summary = json.dumps(field, separators=(",", ":"))[:160] if field is not None else "OK"
        return "PASS", summary
    except Exception as exc:
        return "FAIL", str(exc)[:200]


def build_postman_collection(
    project_root: Path,
    *,
    validate: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    ctx = load_curl_context(project_root)
    folders: dict[str, list[dict[str, Any]]] = {}
    validation_rows: list[dict[str, Any]] = []

    for op in flow_operations():
        export = get_export_for_operation(op.graphql_operation)
        query = get_document(project_root, export) if export else ""
        if not query:
            log.warning("Skipping Postman item — missing GraphQL doc: %s", op.label)
            continue

        variables = resolve_simulation_variables(op.label, ctx, project_root)
        bearer_var = "secondary_bearer_token" if op.uses_secondary_token else "bearer_token"

        description_parts = [
            f"**Flow:** `{op.flow}`",
            f"**GraphQL operation:** `{op.graphql_operation}`",
            f"**UI navigation:** {ui_navigation(op.graphql_operation) or '(n/a)'}",
        ]
        if op.skipped_by_default:
            description_parts.append("**Note:** Skipped by default in automation (`skip=True`).")
        if op.uses_secondary_token:
            description_parts.append("**Auth:** Uses `secondary_bearer_token`.")

        unresolved = re.findall(r"\$[A-Z0-9_]+", json.dumps(variables))
        if unresolved:
            description_parts.append(
                "**Unresolved placeholders:** "
                + ", ".join(sorted(set(unresolved)))
                + " — set in environment before running."
            )

        name = op.label
        if validate:
            status, detail = _validate_request(
                op_label=op.label,
                graphql_operation=op.graphql_operation,
                uses_secondary=op.uses_secondary_token,
                skipped=op.skipped_by_default,
                query=query,
                variables=variables,
                project_root=project_root,
                ctx=ctx,
            )
            description_parts.append(f"**Validation ({status}):** {detail}")
            name = f"{op.label} [{status}]"
            validation_rows.append(
                {
                    "flow": op.flow,
                    "label": op.label,
                    "graphql_operation": op.graphql_operation,
                    "status": status,
                    "detail": detail,
                }
            )

        item = {
            "name": name,
            "request": {
                "method": "POST",
                "header": _request_headers(
                    bearer_var=bearer_var,
                    graphql_operation=op.graphql_operation,
                ),
                "body": {
                    "mode": "raw",
                    "raw": _graphql_body(query, variables, graphql_operation=op.graphql_operation),
                    "options": {"raw": {"language": "json"}},
                },
                "url": (
                    "{{nextgen_graphql_endpoint}}"
                    if is_nextgen_ui_operation(op.graphql_operation)
                    else "{{graphql_endpoint}}"
                ),
                "description": "\n\n".join(description_parts),
            },
            "event": [{"listen": "test", "script": {"type": "text/javascript", "exec": _test_script()}}],
        }
        folders.setdefault(op.flow, []).append(item)

    collection = {
        "info": {
            "_postman_id": str(uuid.uuid4()),
            "name": "MT Audit Log — GraphQL Simulation",
            "description": (
                "Auto-generated from python/audit_validator/simulation/flows.py.\n\n"
                "Import with MT-Audit-Simulation.postman_environment.json.\n"
                "Set runtime IDs (project_id, asset_id, tag_id, …) after create steps.\n"
                "Regenerate: `python -m audit_validator export-postman --validate`"
            ),
            "schema": POSTMAN_SCHEMA,
        },
        "variable": [
            v for v in _postman_variables(project_root, ctx) if v["key"] not in {"bearer_token", "secondary_bearer_token"}
        ],
        "item": [{"name": flow, "item": items} for flow, items in folders.items()],
    }
    return collection, validation_rows, sum(len(v) for v in folders.values())


def build_postman_environment(project_root: Path) -> dict[str, Any]:
    ctx = load_curl_context(project_root)
    return {
        "id": str(uuid.uuid4()),
        "name": "MT Audit Simulation — Everest",
        "values": _postman_variables(project_root, ctx),
        "_postman_variable_scope": "environment",
        "_postman_exported_at": "",
        "_postman_exported_using": "audit_validator export-postman",
    }


def write_postman_export(
    project_root: Path,
    *,
    collection_path: Path,
    environment_path: Path | None = None,
    validate: bool = False,
    validation_path: Path | None = None,
) -> PostmanExportResult:
    collection, validation_rows, count = build_postman_collection(project_root, validate=validate)
    collection_path.parent.mkdir(parents=True, exist_ok=True)
    collection_path.write_text(json.dumps(collection, indent=2), encoding="utf-8")
    log.info("Wrote Postman collection (%d requests) → %s", count, collection_path)

    env_path = environment_path or collection_path.with_name(
        "MT-Audit-Simulation.postman_environment.json"
    )
    env_path.write_text(json.dumps(build_postman_environment(project_root), indent=2), encoding="utf-8")
    log.info("Wrote Postman environment → %s", env_path)

    val_out: Path | None = None
    if validate and validation_path:
        val_out = validation_path
        val_out.parent.mkdir(parents=True, exist_ok=True)
        val_out.write_text(json.dumps({"requests": validation_rows}, indent=2), encoding="utf-8")

    return PostmanExportResult(
        collection_path=collection_path,
        environment_path=env_path,
        request_count=count,
        validation_path=val_out,
    )
