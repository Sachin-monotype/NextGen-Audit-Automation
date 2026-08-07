"""Import Playwright UI-script Excel (datasource-latest.xlsx) into Generate verification."""

from __future__ import annotations

import json
import os
import re
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any

from audit_validator.ui_trigger import (
    _append_log,
    _now,
    _write_job,
    apply_extracted_results,
    finalize_ui_trigger_verification,
)

SHEET_BY_TARGET: dict[str, str] = {
    "web": "datasource - Web",
    "app": "datasource - App",
}

_DEFAULT_REL = Path(
    "MT Connect NextGen/MTConnectAutomation/tests/AuditAutomation/App/"
    "web-audit/datasource-latest.xlsx"
)


def resolve_ui_script_datasource_path() -> Path:
    """Resolve Playwright datasource-latest.xlsx (env override or sibling CodeBases path)."""
    explicit = (os.getenv("UI_SCRIPT_DATASOURCE_PATH") or "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file():
            return path.resolve()
        raise FileNotFoundError(f"UI_SCRIPT_DATASOURCE_PATH not found: {path}")

    here = Path(__file__).resolve()
    # …/NextGen-Audit Automation/python/audit_validator/ui_script_import.py
    project_root = here.parents[2]
    candidates = [
        project_root.parents[1] / _DEFAULT_REL,  # CodeBases/MT Connect NextGen/…
        project_root.parent / _DEFAULT_REL,
        Path.home() / "Documents/CodeBases" / _DEFAULT_REL,
    ]
    for cand in candidates:
        if cand.is_file():
            return cand.resolve()
    raise FileNotFoundError(
        "datasource-latest.xlsx not found. Set UI_SCRIPT_DATASOURCE_PATH or place the file at "
        f"{candidates[0]}"
    )


def _norm_col(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")


def _norm_target(raw: str | None) -> str:
    t = (raw or "web").strip().lower()
    if t in {"web", "app"}:
        return t
    raise ValueError("target must be 'web' or 'app'")


def _parse_response_cell(raw: Any) -> dict[str, Any] | None:
    if raw is None or (isinstance(raw, float) and str(raw) == "nan"):
        return None
    if isinstance(raw, dict):
        parsed: dict[str, Any] | None = raw
    else:
        text = str(raw).strip()
        if not text or text.lower() in {"nan", "none"}:
            return None
        try:
            loaded = json.loads(text)
            parsed = loaded if isinstance(loaded, dict) else None
        except json.JSONDecodeError:
            return None
    if not parsed:
        return None
    # Playwright captures full HTTP GraphQL body: {"data": {"op": {...}}, "errors": ...}
    data = parsed.get("data")
    if isinstance(data, dict) and data:
        return data
    return parsed


def _parse_full_response_cell(raw: Any) -> dict[str, Any] | None:
    """Full Response JSON (GraphQL HTTP body or ingress envelope) — not unwrapped."""
    if raw is None or (isinstance(raw, float) and str(raw) == "nan"):
        return None
    if isinstance(raw, dict):
        return raw
    text = str(raw).strip()
    if not text or text.lower() in {"nan", "none"}:
        return None
    try:
        loaded = json.loads(text)
        return loaded if isinstance(loaded, dict) else None
    except json.JSONDecodeError:
        return None


def _graphql_root_operation(gql: dict[str, Any] | None) -> str | None:
    """Primary GraphQL field name from an unwrapped ``data`` object."""
    if not isinstance(gql, dict) or not gql:
        return None
    skip = {"errors", "extensions", "__typename", "data"}
    keys = [k for k in gql.keys() if k not in skip and isinstance(gql.get(k), (dict, list))]
    if len(keys) == 1:
        return str(keys[0])
    # Prefer mutation-looking keys when multiple (ignore null siblings)
    non_null = [k for k in keys if gql.get(k) not in (None, {}, [])]
    if len(non_null) == 1:
        return str(non_null[0])
    return None


def _looks_like_audit_envelope(node: dict[str, Any] | None) -> bool:
    """True for desktop/UI audit POST bodies (source + actor/subject/CID)."""
    if not isinstance(node, dict) or not node:
        return False
    src = node.get("source")
    if not isinstance(src, dict) or not src:
        return False
    if not (src.get("service") or src.get("operation") or src.get("platformVersion")):
        return False
    return bool(
        node.get("actor")
        or node.get("subject")
        or node.get("xCorrelationId")
        or node.get("eventId")
        or src.get("osName")
        or src.get("actorUserAgent")
    )


def _unwrap_audit_envelope(full: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the audit envelope from a top-level body or ``{op: envelope}`` wrap."""
    if not isinstance(full, dict):
        return None
    if _looks_like_audit_envelope(full):
        return full
    data = full.get("data")
    if isinstance(data, dict) and data is not full:
        nested = _unwrap_audit_envelope(data)
        if nested is not None:
            return nested
    skip = {"data", "errors", "extensions", "__typename"}
    candidates = [
        v
        for k, v in full.items()
        if k not in skip and isinstance(v, dict) and _looks_like_audit_envelope(v)
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _response_body_correlation_id(full: dict[str, Any] | None) -> str | None:
    """CID from ingress/audit Response body (preferred over a stale Excel column)."""
    env = _unwrap_audit_envelope(full) or (full if isinstance(full, dict) else None)
    if not isinstance(env, dict):
        return None
    for key in ("xCorrelationId", "correlationId", "correlation_id", "x_correlation_id"):
        val = env.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _response_body_operation(full: dict[str, Any] | None, gql: dict[str, Any] | None) -> str | None:
    """Audit/ingress ``source.operation`` field, else GraphQL root field."""
    env = _unwrap_audit_envelope(full)
    if isinstance(env, dict):
        op = env.get("operation")
        if isinstance(op, str) and op.strip():
            return op.strip()
        src = env.get("source")
        if isinstance(src, dict):
            sop = src.get("operation")
            if isinstance(sop, str) and sop.strip():
                return sop.strip()
    return _graphql_root_operation(gql)


def _ingress_source_from_response(full: dict[str, Any] | None) -> dict[str, Any] | None:
    """Extract audit envelope ``source`` from Excel Response (desktop curl body)."""
    env = _unwrap_audit_envelope(full)
    if not isinstance(env, dict):
        return None
    src = env.get("source")
    return src if isinstance(src, dict) and src else None


def _ingress_actor_from_response(full: dict[str, Any] | None) -> dict[str, Any] | None:
    env = _unwrap_audit_envelope(full)
    if not isinstance(env, dict):
        return None
    actor = env.get("actor")
    return actor if isinstance(actor, dict) and actor else None


def _ingress_subject_from_response(full: dict[str, Any] | None) -> dict[str, Any] | None:
    env = _unwrap_audit_envelope(full)
    if not isinstance(env, dict):
        return None
    subject = env.get("subject")
    return subject if isinstance(subject, dict) and subject else None


def _ingress_headers_from_response(full: dict[str, Any] | None) -> dict[str, Any]:
    """Envelope leaves + request fingerprint headers for Compare.

    Prefers explicit ``request_headers`` / ``headers`` on the capture when present
    (User-Agent, X-Unified-Version). Falls back to audit ``source`` body leaves.
    """
    out: dict[str, Any] = {}
    if isinstance(full, dict):
        for key in ("request_headers", "headers", "ingress_headers"):
            raw = full.get(key)
            if isinstance(raw, dict):
                for hk, hv in raw.items():
                    if hv not in (None, "", [], {}):
                        out[str(hk).strip()] = hv
    env = _unwrap_audit_envelope(full)
    if isinstance(env, dict):
        for key in ("xCorrelationId", "eventId", "eventVersion", "occurredAt", "routingKey"):
            if env.get(key) not in (None, "", [], {}) and key not in out:
                out[key] = env.get(key)
        src = env.get("source") if isinstance(env.get("source"), dict) else {}
        # Promote body fingerprints into header map when HTTP headers weren't captured.
        if src.get("actorUserAgent") and not any(
            k.lower() == "user-agent" for k in out
        ):
            out["User-Agent"] = src.get("actorUserAgent")
        if src.get("platformVersion") and not any(
            k.lower() == "x-unified-version" for k in out
        ):
            out["X-Unified-Version"] = src.get("platformVersion")
    return out


def _graphql_call_failed(full: dict[str, Any] | None) -> bool:
    """True when GraphQL returned errors and no usable data (no audit event expected)."""
    if not isinstance(full, dict):
        return False
    errors = full.get("errors")
    if not isinstance(errors, list) or not errors:
        return False
    data = full.get("data")
    if data is None:
        return True
    if isinstance(data, dict) and not any(v not in (None, {}, []) for v in data.values()):
        return True
    return False


def _normalize_graphql_response(op: str, gql: dict[str, Any] | None) -> dict[str, Any] | None:
    """Ensure Compare digs see ``{operation: body}`` (not HTTP ``{data:...}`` wrapper)."""
    if not isinstance(gql, dict) or not gql:
        return None
    if op and op in gql:
        return gql
    data = gql.get("data")
    if isinstance(data, dict) and data:
        if op and op in data:
            return data
        return {op: data} if op else data
    return {op: gql} if op else gql


def _cell_str(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, float) and str(raw) == "nan":
        return ""
    text = str(raw).strip()
    if text.lower() in {"nan", "none"}:
        return ""
    return text


def _is_ok_status(raw: Any) -> bool:
    return _cell_str(raw).upper() in {"OK", "PASS", ""}


def _normalize_scenario(scenario: str) -> str:
    if not scenario:
        return ""
    try:
        from audit_validator.ui_case_recipes import short_touch

        return short_touch(scenario) or scenario
    except Exception:  # noqa: BLE001
        return scenario.replace(">", "_").replace(" ", "_").lower()


def _sheet_for_target(target: str) -> str:
    return SHEET_BY_TARGET[_norm_target(target)]


def _pair_keys(pairs: list[dict[str, Any]] | None) -> set[str]:
    """Normalize selected event+scenario pairs to ``event::scenario`` keys."""
    keys: set[str] = set()
    for p in pairs or []:
        if not isinstance(p, dict):
            continue
        op = str(
            p.get("event_name") or p.get("operation") or p.get("event") or ""
        ).strip()
        touch = str(
            p.get("scenario") or p.get("touchpoint") or p.get("touch") or ""
        ).strip()
        if not op:
            continue
        touch_n = _normalize_scenario(touch) if touch else ""
        keys.add(f"{op}::{touch_n}" if touch_n else op)
    return keys


def catalog_from_rows(
    rows: list[dict[str, Any]],
    *,
    target: str = "web",
    path: str | None = None,
    filename: str | None = None,
) -> dict[str, Any]:
    """Build catalog payload from parsed Excel rows."""
    events = sorted({str(r["event_name"]) for r in rows if r.get("event_name")})
    scenarios = sorted({str(r["scenario"]) for r in rows if r.get("scenario")})
    pairs = [
        {
            "id": (
                f"{r.get('event_name')}::{r.get('scenario')}"
                if r.get("scenario")
                else str(r.get("event_name") or "")
            ),
            "event_name": str(r.get("event_name") or ""),
            "scenario": str(r.get("scenario") or ""),
            "correlation_id": str(r.get("correlation_id") or ""),
            "has_auth_token": bool(str(r.get("auth_token") or "").strip()),
        }
        for r in rows
    ]
    return {
        "target": _norm_target(target),
        "path": path,
        "filename": filename,
        "sheet": _sheet_for_target(target),
        "events": events,
        "scenarios": scenarios,
        "rows": pairs,
        "count": len(pairs),
    }


def parse_ui_script_excel(
    content: bytes,
    *,
    target: str | None = None,
    sheet_name: str | None = None,
    events: list[str] | None = None,
    scenarios: list[str] | None = None,
    pairs: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Parse datasource sheet: event_name, scenario, correlation_id, auth_token, response.

    - Forward-fills blank ``event_name`` cells (merged-style Excel rows).
    - When a ``status`` column exists, only OK/PASS rows with a correlation_id are kept.
    - Rows without a usable correlation_id are skipped.
    - Optional ``pairs`` filters exact event+scenario combinations (preferred).
    - Optional ``events`` / ``scenarios`` filters match normalized names (empty = all).
    """
    import pandas as pd

    from audit_validator.touchpoint.scenarios import is_valid_correlation_id

    sheet = sheet_name
    if not sheet and target:
        sheet = _sheet_for_target(target)

    read_kw: dict[str, Any] = {}
    if sheet:
        read_kw["sheet_name"] = sheet
    else:
        read_kw["sheet_name"] = 0

    try:
        df = pd.read_excel(BytesIO(content), **read_kw)
    except ValueError as exc:
        # Fallback: first sheet if named sheet missing (older single-sheet files).
        if sheet and sheet != 0:
            df = pd.read_excel(BytesIO(content), sheet_name=0)
        else:
            raise ValueError(f"Could not read Excel sheet {sheet!r}: {exc}") from exc

    if df.empty:
        return []
    colmap = {_norm_col(str(c)): str(c) for c in df.columns}

    def pick(*names: str) -> str | None:
        for n in names:
            if n in colmap:
                return colmap[n]
        return None

    event_col = pick("event_name", "event", "operation", "eventname")
    scenario_col = pick("scenario", "touchpoint", "source", "touch")
    cid_col = pick("correlation_id", "correlationid", "correlation", "xcorrelationid")
    resp_col = pick("response", "graphql_response", "body")
    status_col = pick("status", "result", "run_status")
    auth_col = pick("auth_token", "authtoken", "bearer_token", "token", "jwt")
    target_col = pick("target", "platform", "channel")

    if not event_col or not cid_col:
        raise ValueError(
            "Excel must include event_name (or event/operation) and correlation_id columns"
        )

    pair_filter = _pair_keys(pairs)
    event_filter = {e.strip() for e in (events or []) if str(e).strip()}
    scenario_filter = {_normalize_scenario(s) for s in (scenarios or []) if str(s).strip()}
    scenario_filter |= {s.strip().lower() for s in (scenarios or []) if str(s).strip()}

    work = df.copy()
    work.loc[:, event_col] = work[event_col].ffill()

    rows: list[dict[str, Any]] = []
    seen_cid: set[str] = set()
    for _, series in work.iterrows():
        excel_op = _cell_str(series.get(event_col))
        col_cid = _cell_str(series.get(cid_col))
        if not excel_op and not col_cid:
            continue
        if status_col is not None and not _is_ok_status(series.get(status_col)):
            continue

        scenario = _cell_str(series.get(scenario_col)) if scenario_col else ""
        scenario = _normalize_scenario(scenario) if scenario else ""

        auth_token = _cell_str(series.get(auth_col)) if auth_col else ""
        resp_raw = series.get(resp_col) if resp_col else None
        full_resp = _parse_full_response_cell(resp_raw)
        gql_resp = _parse_response_cell(resp_raw)
        body_op = _response_body_operation(full_resp, gql_resp)
        op = body_op if body_op else excel_op
        body_cid = _response_body_correlation_id(full_resp)
        cid = body_cid if (body_cid and is_valid_correlation_id(body_cid)) else col_cid
        if not cid or not is_valid_correlation_id(cid):
            continue
        if cid in seen_cid:
            continue
        seen_cid.add(cid)
        if not op:
            continue

        if pair_filter:
            # Match either Excel label or resolved GraphQL op so UI filters still work.
            keys = {
                f"{op}::{scenario}" if scenario else op,
                f"{excel_op}::{scenario}" if scenario else excel_op,
            }
            if keys.isdisjoint(pair_filter):
                continue
        else:
            if event_filter and op not in event_filter and excel_op not in event_filter:
                continue
            if (
                scenario_filter
                and scenario not in scenario_filter
                and scenario.lower() not in scenario_filter
            ):
                continue

        row_target = _cell_str(series.get(target_col)) if target_col else ""
        if not row_target and target:
            row_target = _norm_target(target)

        gql_failed = _graphql_call_failed(full_resp)
        ingress_source = _ingress_source_from_response(full_resp)
        ingress_actor = _ingress_actor_from_response(full_resp)
        ingress_subject = _ingress_subject_from_response(full_resp)
        ingress_headers = _ingress_headers_from_response(full_resp)
        rows.append(
            {
                "operation": op,
                "event_name": op,
                "excel_event_name": excel_op or op,
                "touchpoint": scenario,
                "scenario": scenario,
                "target": row_target or (target or ""),
                "correlation_id": cid,
                "auth_token": auth_token,
                "status": "OK",
                "response": resp_raw if resp_raw is not None else "",
                # Keep envelope under graphql_response only for true GraphQL mutations.
                "graphql_response": gql_resp if not ingress_source else None,
                "graphql_failed": gql_failed,
                "ingress_source": ingress_source,
                "ingress_actor": ingress_actor,
                "ingress_subject": ingress_subject,
                "ingress_headers": ingress_headers or None,
            }
        )
    return rows


def load_ui_script_rows(
    *,
    target: str = "web",
    events: list[str] | None = None,
    scenarios: list[str] | None = None,
    pairs: list[dict[str, Any]] | None = None,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    """Read datasource-latest.xlsx for ``web`` or ``app`` and return filtered OK rows."""
    ds = path or resolve_ui_script_datasource_path()
    content = ds.read_bytes()
    return parse_ui_script_excel(
        content,
        target=target,
        events=events,
        scenarios=scenarios,
        pairs=pairs,
    )


def list_ui_script_catalog(
    *,
    target: str = "web",
    path: Path | None = None,
    content: bytes | None = None,
    filename: str | None = None,
) -> dict[str, Any]:
    """List events / scenarios / rows available in the Excel for the given target."""
    if content is not None:
        rows = parse_ui_script_excel(content, target=target)
        return catalog_from_rows(
            rows,
            target=target,
            path=None,
            filename=filename or "upload.xlsx",
        )
    ds = path or resolve_ui_script_datasource_path()
    rows = load_ui_script_rows(target=target, path=ds)
    return catalog_from_rows(rows, target=target, path=str(ds))


def _jwt_identity_for_row(auth_token: str) -> dict[str, Any]:
    """Actor JWT claims: Excel auth_token when present, else project logged-in user."""
    from audit_validator.auth import jwt_identity, resolve_our_profile_id

    token = (auth_token or "").strip()
    if token:
        ident = jwt_identity(token)
        note = "JWT claims from Excel auth_token"
    else:
        ident = jwt_identity()
        note = "JWT claims from project logged-in user"
    out = dict(ident or {})
    out["_source_note"] = note
    # Profile UUID is not in JWT — resolve via UMS when we can.
    # For Excel tokens, resolve against that identity's idp; for empty token use env user.
    try:
        if token:
            from audit_validator.auth import _identity_is_user
            from audit_validator.source_validation.clients import UmsClient
            from audit_validator.source_validation.config import load_source_validation_config

            idp = str(out.get("idp_user_id") or "").strip()
            if idp and _identity_is_user(out):
                cfg = load_source_validation_config(None)
                if cfg.ums_ready:
                    user = UmsClient(cfg).get_user_by_idp_user_id(
                        idp, correlation_id="ui-script-auth-token-profile"
                    )
                    if isinstance(user, dict):
                        gcid = str(out.get("gcid") or "")
                        for pr in user.get("profiles") or []:
                            if not isinstance(pr, dict):
                                continue
                            pid = pr.get("id") or (pr.get("profile") or {}).get("id")
                            if not pid:
                                continue
                            if gcid and str(pr.get("customerId") or "") == gcid:
                                out["_profile_id"] = str(pid)
                                break
                            out.setdefault("_profile_id", str(pid))
        else:
            pid = resolve_our_profile_id()
            if pid:
                out["_profile_id"] = pid
    except Exception:  # noqa: BLE001
        pass
    return out


def _selection_from_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from audit_validator.touchpoint.scenarios import scenario_display_name

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in rows:
        op = str(r.get("operation") or "").strip()
        touch = str(r.get("touchpoint") or "").strip()
        tgt = str(r.get("target") or "").strip().lower()
        key = f"{op}::{touch}" if touch else op
        if tgt == "app":
            key = f"{key}::app"
        if not op or key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "id": key,
                "operation": op,
                "touchpoint": touch or None,
                "target": tgt or None,
                "label": scenario_display_name(
                    op, touch or None, ui=True, target=tgt or None
                ),
            }
        )
    return out


def create_ui_script_job(
    project_root: Path,
    *,
    selection: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    target: str = "web",
) -> dict[str, Any]:
    job_id = str(uuid.uuid4())
    correlation_details = [
        {
            "event": str(r.get("operation") or r.get("event_name") or "").strip(),
            "scenario": str(r.get("touchpoint") or r.get("scenario") or "").strip(),
            "correlation_id": str(r.get("correlation_id") or "").strip(),
            "has_auth_token": bool(str(r.get("auth_token") or "").strip()),
            "status": "OK",
        }
        for r in rows
        if str(r.get("correlation_id") or "").strip()
    ]
    job: dict[str, Any] = {
        "id": job_id,
        "kind": "ui_trigger",
        "status": "completed",
        "created_at": _now(),
        "updated_at": _now(),
        "selection": selection,
        "testrail": {"testcase_id": "playwright-script", "case_ids": [], "mapped_case_ids": []},
        "cta_text": "",
        "notes": f"Imported from Playwright UI script Excel ({target})",
        "correlation_strategy": {
            "response_header": "correlation-id",
            "note": "Playwright script capture — correlation_id from datasource-latest.xlsx",
        },
        "agent": {
            "channel": "playwright_script",
            "target": _norm_target(target),
            "send_status": "skipped",
            "casepilot_job_ids": [],
            "last_error": None,
            "rows_imported": len(rows),
            "correlation_details": correlation_details,
        },
        "results": [],
        "logs": [],
        "verification": {
            "ready": False,
            "correlation_ids": [],
            "operations": [],
            "auto_verify_pending": True,
            "generate_run_saved": False,
        },
    }
    job = _write_job(project_root, job)
    extracted: list[dict[str, Any]] = []
    for r in rows:
        cid = str(r.get("correlation_id") or "").strip()
        if not cid:
            continue
        op = str(r.get("operation") or r.get("event_name") or "").strip()
        touch = str(r.get("touchpoint") or r.get("scenario") or "").strip()
        auth_token = str(r.get("auth_token") or "").strip()
        ident = _jwt_identity_for_row(auth_token)
        profile_id = ident.pop("_profile_id", None)
        source_note = ident.pop("_source_note", None)
        item: dict[str, Any] = {
            "correlation_id": cid,
            "operation": op,
            "touchpoint": touch,
            "target": str(r.get("target") or target or "").strip().lower() or None,
            "source": "playwright_script",
            "recorded_at": _now(),
            "jwt_identity": ident,
            "jwt_identity_note": source_note,
            "jwt_from_excel": bool(auth_token),
        }
        if auth_token:
            item["auth_token"] = auth_token
        if profile_id:
            item["our_profile_id"] = profile_id
        if r.get("excel_event_name"):
            item["excel_event_name"] = str(r.get("excel_event_name"))
        if r.get("graphql_failed"):
            item["graphql_failed"] = True
        if isinstance(r.get("ingress_source"), dict) and r.get("ingress_source"):
            item["ingress_source"] = r["ingress_source"]
        if isinstance(r.get("ingress_actor"), dict) and r.get("ingress_actor"):
            item["ingress_actor"] = r["ingress_actor"]
        if isinstance(r.get("ingress_subject"), dict) and r.get("ingress_subject"):
            item["ingress_subject"] = r["ingress_subject"]
        if isinstance(r.get("ingress_headers"), dict) and r.get("ingress_headers"):
            item["ingress_headers"] = r["ingress_headers"]
        gql = _normalize_graphql_response(
            op, r.get("graphql_response") if isinstance(r.get("graphql_response"), dict) else None
        )
        if gql and not item.get("ingress_source"):
            item["graphql_response"] = gql
        extracted.append(item)
    if not extracted:
        _append_log(job, "✖ No valid correlation_id values found in Excel (need OK rows with UUID)")
        job["agent"]["last_error"] = "No valid correlation_id values found in Excel"
        return _write_job(project_root, job)
    # Keep logs empty for UI script — UI shows correlation_details table instead.
    job = apply_extracted_results(project_root, job, extracted)
    job["logs"] = []
    job["status"] = "completed"
    job.setdefault("agent", {})["correlation_details"] = correlation_details
    return _write_job(project_root, job)


def import_ui_script_excel(
    project_root: Path,
    content: bytes | None = None,
    *,
    target: str = "web",
    events: list[str] | None = None,
    scenarios: list[str] | None = None,
    pairs: list[dict[str, Any]] | None = None,
    selection: list[dict[str, Any]] | None = None,
    db: Any = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Import Excel rows for target/filters. Selection always comes from Excel rows."""
    del selection  # page selection must not override Excel list
    tgt = _norm_target(target)
    if content is not None:
        rows = parse_ui_script_excel(
            content,
            target=tgt,
            events=events,
            scenarios=scenarios,
            pairs=pairs,
        )
    else:
        rows = load_ui_script_rows(
            target=tgt,
            events=events,
            scenarios=scenarios,
            pairs=pairs,
            path=path,
        )
    if not rows:
        raise ValueError(
            "No OK rows with a valid correlation_id found for the selected "
            f"target={tgt!r} / events / scenarios. "
            "Excel needs event_name, scenario, correlation_id (and status=OK when present)."
        )
    sel = _selection_from_rows(rows)
    job = create_ui_script_job(project_root, selection=sel, rows=rows, target=tgt)
    details = list((job.get("agent") or {}).get("correlation_details") or [])
    finalized = finalize_ui_trigger_verification(project_root, job["id"], db=db)
    out = finalized or job
    # Keep UI Script panel log-free and retain the Excel correlation table.
    agent = dict(out.get("agent") or {})
    agent["channel"] = "playwright_script"
    agent["target"] = tgt
    agent["correlation_details"] = details or agent.get("correlation_details") or []
    agent["rows_imported"] = len(rows)
    out["agent"] = agent
    out["logs"] = []
    return _write_job(project_root, out)
