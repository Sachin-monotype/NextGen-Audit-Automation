"""Fetch + send an editable payload for any generatable event.

Powers the Generate page's "Edit payload & send" panel so a QA can, e.g., take
``activateFamily`` and activate a *different* family straight from the UI, or tweak
any ingress/cron event and fire it — without touching code.

Three transports mirror the generation routing (see ``operation_sources``):

- ``graphql`` — POST ``{query, variables}`` to the NextGen ``/graph`` endpoint.
- ``ingress`` — POST a one-element array of audit envelopes to the resolver Ingress API.
- ``cron``    — publish the scheduler envelope onto the raw-events exchange.

``default_payload`` returns the editable JSON body pre-filled from the latest captured
event (GraphQL variables), or the bundled sample (ingress/cron). ``send_payload`` sends
the (possibly edited) body and returns the origin's response so the UI can show success.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

import requests

from .curl_builder import _graphql_endpoint, _ingress_endpoint, _resolve_bearer
from .operation_sources import CRON_PREFIX, INGRESS_PREFIX
from .utility.operation_graphql import get_document_for_operation

_TIMEOUT = float(os.getenv("CUSTOM_SEND_TIMEOUT_SEC", "30"))


def _kind_of(item_id: str) -> tuple[str, str, str | None]:
    """Return (kind, key, touchpoint) for a catalog selection id."""
    if item_id.startswith(INGRESS_PREFIX):
        return "ingress", item_id[len(INGRESS_PREFIX):], None
    if item_id.startswith(CRON_PREFIX):
        return "cron", item_id[len(CRON_PREFIX):], None
    from .touchpoint.scenarios import parse_selection_id

    operation, touchpoint = parse_selection_id(item_id)
    return "graphql", operation, touchpoint


def _ingress_case(case_id: str):
    from .ingress.payloads import load_ingress_cases

    for case in load_ingress_cases():
        if case.case_id == case_id:
            return case
    return None


def _cron_case(case_id: str):
    from .cron.payloads import load_cron_cases

    for case in load_cron_cases():
        if case.case_id == case_id:
            return case
    return None


def _project_root(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit
    # Repo root: …/NextGen-Audit Automation (python/audit_validator/custom_send.py → parents[2])
    return Path(__file__).resolve().parents[2]


def _graphql_variables(
    operation: str,
    document: str,
    raw: dict[str, Any] | None,
    *,
    project_root: Path | None = None,
    touchpoint: str | None = None,
) -> dict[str, Any]:
    """Build GraphQL ``variables`` with *working* tenant inventory IDs.

    Seeds from Mongo / templates are treated as candidates only — ``live_seeds``
    verifies via getFamilies / favorites so Copy curl does not ship dead IDs.
    """
    import copy

    from .live_seeds import ensure_working_graphql_variables
    from .utility.operation_meta import (
        load_curl_context,
        merged_operation_variables_template,
        resolve_variables,
    )

    root = _project_root(project_root)
    if touchpoint:
        try:
            from .simulation.config import load_simulation_config
            from .simulation.touchpoint_runner import _make_seed
            from .touchpoint.payloads import variables_for

            cfg = load_simulation_config(root)
            variables = variables_for(operation, _make_seed(cfg), touch=touchpoint)
            if variables:
                return variables
        except Exception:
            # Fall through to operation templates/Mongo when a scenario seed
            # cannot be resolved (the editor must still show a useful payload).
            pass
    template = copy.deepcopy(merged_operation_variables_template().get(operation) or {})
    variables: dict[str, Any] = {}

    # Prefer template/.env resolution, then overlay Mongo input as candidate values.
    if template:
        try:
            ctx = load_curl_context(root)
            resolved = resolve_variables(operation, ctx, root)
            if resolved:
                variables = resolved
            else:
                variables = template
        except Exception:
            variables = template

    if raw:
        meta = (raw.get("subject") or {}).get("metadata") or {}
        inp = meta.get("input")
        if isinstance(inp, dict) and inp:
            if not variables:
                variables = {"input": inp}
            elif isinstance(variables.get("input"), dict):
                # Keep resolved keys; let mongo familyIds sit as candidates for live_seeds
                merged_input = {**variables["input"], **inp}
                # Prefer live-validated familyIds: restore template candidates first in list
                if "familyIds" in variables["input"] and "familyIds" in inp:
                    a = variables["input"].get("familyIds") or []
                    b = inp.get("familyIds") or []
                    merged_input["familyIds"] = list(dict.fromkeys([*a, *b]))
                variables = {**variables, "input": merged_input}
            elif "input" in template or "$input" in document:
                variables = {"input": inp}

    if not variables:
        if "$input" in document or "Input)" in document or "Input!" in document:
            variables = {"input": {}}
        else:
            variables = {}

    try:
        return ensure_working_graphql_variables(
            operation, variables, project_root=root
        )
    except Exception as exc:
        log = __import__("logging").getLogger(__name__)
        log.debug("live_seeds skipped for %s: %s", operation, exc)
        return variables


def _extract_correlation_id(payload: Any, explicit: str | None = None) -> tuple[Any, str]:
    """Pull optional cid from request or payload wrapper; strip wrapper keys from GQL body."""
    if explicit and str(explicit).strip():
        return payload, str(explicit).strip()
    if not isinstance(payload, dict):
        return payload, ""
    cid = str(
        payload.get("x-correlation-id")
        or payload.get("xCorrelationId")
        or payload.get("correlation_id")
        or ""
    ).strip()
    if not cid:
        return payload, ""
    cleaned = {
        k: v
        for k, v in payload.items()
        if k not in ("x-correlation-id", "xCorrelationId", "correlation_id")
    }
    return cleaned, cid


def default_payload(
    item_id: str,
    *,
    raw: dict[str, Any] | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Build the editable default body for an event.

    ``raw`` (a captured raw event from Mongo) seeds GraphQL variables when available.
    Falls back to ``operation_meta`` templates so Edit&Send is never an empty ``{}``.
    """
    kind, key, touchpoint = _kind_of(item_id)

    if kind == "graphql":
        document = get_document_for_operation(key)
        if not document:
            return {
                "id": item_id,
                "kind": "graphql",
                "operation": key,
                "editable": False,
                "note": "No GraphQL document is registered for this operation.",
            }
        variables = _graphql_variables(
            key,
            document,
            raw,
            project_root=project_root,
            touchpoint=touchpoint,
        )
        cid = str(uuid.uuid4())
        # x-correlation-id sits in the JSON so Copy curl / Send share one filter key.
        # It is stripped before the GraphQL POST and sent as the HTTP header.
        body = {
            "x-correlation-id": cid,
            "query": document,
            "variables": variables,
        }
        hint = (
            "Edit variables (e.g. input.familyIds) and Send. Query is fixed. "
            f"Filter Mongo/UI with x-correlation-id already in this payload ({cid[:8]}…)."
        )
        if isinstance(variables.get("input"), dict) and not variables["input"]:
            hint = (
                "variables.input is empty — fill required fields (IDs) from .env seeds "
                "(PROJECT_ID, SEED_FAMILY_ID, …) before Send."
            )
        return {
            "id": item_id,
            "kind": "graphql",
            "operation": key,
            "touchpoint": touchpoint,
            "endpoint": _graphql_endpoint(key),
            "editable": True,
            "payload": body,
            "correlation_id": cid,
            "hint": hint,
        }

    if kind == "ingress":
        case = _ingress_case(key)
        if not case:
            return {"id": item_id, "kind": "ingress", "editable": False, "note": f"Unknown ingress case {key}"}
        envelope = json.loads(Path(case.path).read_text(encoding="utf-8"))
        if isinstance(envelope, dict) and not str(envelope.get("xCorrelationId") or "").strip():
            envelope["xCorrelationId"] = str(uuid.uuid4())
        cid = str((envelope or {}).get("xCorrelationId") or "") if isinstance(envelope, dict) else ""
        return {
            "id": item_id,
            "kind": "ingress",
            "operation": case.operation,
            "endpoint": _ingress_endpoint(),
            "editable": True,
            "payload": envelope,
            "correlation_id": cid,
            "hint": (
                "Edit the audit envelope and Send. "
                f"Filter with xCorrelationId already on the envelope ({cid[:8]}…)."
                if cid
                else "Edit the audit envelope (subject/actor/metadata) and Send to the Ingress API."
            ),
        }

    # cron
    case = _cron_case(key)
    if not case:
        return {"id": item_id, "kind": "cron", "editable": False, "note": f"Unknown cron case {key}"}
    payload = json.loads(Path(case.path).read_text(encoding="utf-8"))
    return {
        "id": item_id,
        "kind": "cron",
        "operation": case.operation,
        "endpoint": "raw-events exchange (RabbitMQ)",
        "editable": True,
        "payload": payload,
        "hint": "Edit the scheduler envelope and Send — it is published onto the raw-events queue.",
    }


def _send_graphql(
    operation: str, body: dict[str, Any], *, correlation_id: str = ""
) -> dict[str, Any]:
    endpoint = _graphql_endpoint(operation)
    authorization, real = _resolve_bearer()
    if not real:
        return {"ok": False, "detail": "No BEARER_TOKEN configured in .env."}
    body, from_payload = _extract_correlation_id(body, correlation_id or None)
    cid = from_payload or str(uuid.uuid4())
    resp = requests.post(
        endpoint,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": authorization,
            "x-correlation-id": cid,
        },
        json=body,
        timeout=_TIMEOUT,
    )
    try:
        data = resp.json()
    except ValueError:
        data = {"raw": (resp.text or "")[:2000]}
    ok = resp.status_code == 200 and isinstance(data, dict) and not data.get("errors")
    try:
        from .generation_tracker import record_generation

        record_generation(
            operation,
            cid,
            kind="graphql",
            meta={
                "trigger_status": "PASS" if ok else "FAIL",
                **({"trigger_error": "GraphQL errors or non-200"} if not ok else {}),
            },
        )
    except Exception:
        pass
    return {
        "ok": ok,
        "status_code": resp.status_code,
        "endpoint": endpoint,
        "correlation_id": cid,
        "response": data,
        "detail": "Mutation sent." if ok else "GraphQL returned errors — see response.",
    }


def _send_ingress(payload: Any, *, correlation_id: str = "") -> dict[str, Any]:
    endpoint = _ingress_endpoint()
    authorization, real = _resolve_bearer()
    envelope_list = payload if isinstance(payload, list) else [payload]
    # Freshen ids so the resolver treats it as a new event — keep user-supplied cid/eventId.
    for env in envelope_list:
        if not isinstance(env, dict):
            continue
        if correlation_id:
            env["xCorrelationId"] = correlation_id
        elif not str(env.get("xCorrelationId") or "").strip():
            env["xCorrelationId"] = str(uuid.uuid4())
        if not str(env.get("eventId") or "").strip():
            env["eventId"] = str(uuid.uuid4())
    cid = str((envelope_list[0] or {}).get("xCorrelationId") or "") if envelope_list else ""
    resp = requests.post(
        endpoint,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": authorization,
            "x-correlation-id": cid,
            "x-request-source": os.getenv("INGRESS_REQUEST_SOURCE", "MT_CONNECT_BS"),
            "x-os-platform": os.getenv("INGRESS_OS_PLATFORM", "MAC"),
        },
        json=envelope_list,
        timeout=_TIMEOUT,
    )
    try:
        data = resp.json()
    except ValueError:
        data = {"raw": (resp.text or "")[:2000]}
    ok = resp.status_code < 400
    op = ""
    if envelope_list and isinstance(envelope_list[0], dict):
        op = str(((envelope_list[0].get("source") or {}).get("operation")) or "")
    if op and cid:
        try:
            from .generation_tracker import record_generation

            eid = ""
            if envelope_list and isinstance(envelope_list[0], dict):
                eid = str(envelope_list[0].get("eventId") or "")
            record_generation(
                op,
                cid,
                kind="ingress",
                meta={
                    "trigger_status": "PASS" if ok else "FAIL",
                    **({"eventId": eid} if eid else {}),
                },
            )
        except Exception:
            pass
    return {
        "ok": ok,
        "status_code": resp.status_code,
        "endpoint": endpoint,
        "correlation_id": cid,
        "response": data,
        "detail": "Ingress event accepted." if ok else "Ingress API rejected the payload.",
    }


def _send_cron(case_id: str, payload: dict[str, Any], *, project_root: Path | None = None) -> dict[str, Any]:
    from .config import load_config
    from .cron.payloads import amqp_routing_key_for_payload, normalize_cron_payload
    from .rabbitmq.publisher import publish_raw_event

    cfg = load_config(project_root)
    normalized = normalize_cron_payload(payload, case_id=case_id)
    amqp_rk = amqp_routing_key_for_payload(normalized)
    publish_raw_event(cfg.rabbitmq, normalized, amqp_routing_key=amqp_rk)
    cid = str(normalized.get("xCorrelationId") or "")
    op = str(((normalized.get("source") or {}).get("operation")) or case_id)
    if cid:
        try:
            from .generation_tracker import record_generation

            record_generation(
                op,
                cid,
                kind="cron",
                project_root=project_root,
                meta={
                    "trigger_status": "PASS",
                    **(
                        {"eventId": str(normalized.get("eventId") or "")}
                        if normalized.get("eventId")
                        else {}
                    ),
                },
            )
        except Exception:
            pass
    return {
        "ok": True,
        "endpoint": f"raw-events exchange (key={amqp_rk})",
        "correlation_id": cid,
        "response": {"published": True},
        "detail": "Scheduler envelope published onto the raw-events queue.",
    }


def build_payload_curl(
    item_id: str,
    payload: Any,
    *,
    correlation_id: str = "",
) -> dict[str, Any]:
    """Copy-paste curl for the *edited* payload (same body Edit&Send would POST)."""
    from .curl_builder import _multiline_curl

    kind, key, _touchpoint = _kind_of(item_id)
    authorization, real = _resolve_bearer()
    token_note = "" if real else " Set BEARER_TOKEN in .env for a ready-to-run token."

    if kind == "graphql":
        body, cid = _extract_correlation_id(payload, correlation_id or None)
        cid = cid or str(uuid.uuid4())
        endpoint = _graphql_endpoint(key)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": authorization,
            "x-correlation-id": cid,
        }
        data = json.dumps(body, ensure_ascii=False, default=str)
        return {
            "ok": True,
            "kind": "graphql",
            "endpoint": endpoint,
            "correlation_id": cid,
            "curl": _multiline_curl("POST", endpoint, headers, data),
            "note": ("Uses the JSON in the editor (query + variables)." + token_note).strip(),
        }

    if kind == "ingress":
        endpoint = _ingress_endpoint()
        envelope_list = payload if isinstance(payload, list) else [payload]
        cid = correlation_id
        if not cid and envelope_list and isinstance(envelope_list[0], dict):
            cid = str(envelope_list[0].get("xCorrelationId") or "")
        if not cid:
            cid = str(uuid.uuid4())
        # Mirror send: ensure cid on first envelope for the copied body
        to_send = json.loads(json.dumps(envelope_list, default=str))
        if to_send and isinstance(to_send[0], dict):
            to_send[0]["xCorrelationId"] = cid
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": authorization,
            "x-correlation-id": cid,
            "x-request-source": os.getenv("INGRESS_REQUEST_SOURCE", "MT_CONNECT_BS"),
            "x-os-platform": os.getenv("INGRESS_OS_PLATFORM", "MAC"),
        }
        data = json.dumps(to_send, ensure_ascii=False, default=str)
        return {
            "ok": True,
            "kind": "ingress",
            "endpoint": endpoint,
            "correlation_id": cid,
            "curl": _multiline_curl("POST", endpoint, headers, data),
            "note": ("Ingress envelope as edited." + token_note).strip(),
        }

    return {
        "ok": False,
        "kind": "cron",
        "detail": "Cron publishes to RabbitMQ — there is no HTTP curl. Use Send payload.",
        "curl": "",
    }


def send_payload(
    item_id: str,
    payload: Any,
    *,
    project_root: Path | None = None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Send an (edited) payload to the right transport for its kind."""
    kind, key, touchpoint = _kind_of(item_id)
    cid = (correlation_id or "").strip()
    if kind == "graphql":
        if not isinstance(payload, dict):
            return {"ok": False, "detail": "GraphQL body must be a JSON object with a `query`."}
        body, _ = _extract_correlation_id(payload, cid or None)
        if not isinstance(body, dict) or "query" not in body:
            return {"ok": False, "detail": "GraphQL body must be a JSON object with a `query`."}
        result = _send_graphql(key, payload, correlation_id=cid)
        if touchpoint:
            result["touchpoint"] = touchpoint
        return result
    if kind == "ingress":
        return _send_ingress(payload, correlation_id=cid)
    return _send_cron(key, payload, project_root=project_root)
