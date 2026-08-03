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
        return raw
    text = str(raw).strip()
    if not text or text.lower() in {"nan", "none"}:
        return None
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


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
        op = _cell_str(series.get(event_col))
        cid = _cell_str(series.get(cid_col))
        if not op and not cid:
            continue
        if status_col is not None and not _is_ok_status(series.get(status_col)):
            continue
        if not cid or not is_valid_correlation_id(cid):
            continue
        if cid in seen_cid:
            continue
        seen_cid.add(cid)

        scenario = _cell_str(series.get(scenario_col)) if scenario_col else ""
        scenario = _normalize_scenario(scenario) if scenario else ""

        if pair_filter:
            key = f"{op}::{scenario}" if scenario else op
            if key not in pair_filter:
                continue
        else:
            if event_filter and op not in event_filter:
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

        auth_token = _cell_str(series.get(auth_col)) if auth_col else ""
        resp_raw = series.get(resp_col) if resp_col else None
        gql_resp = _parse_response_cell(resp_raw)
        rows.append(
            {
                "operation": op,
                "event_name": op,
                "touchpoint": scenario,
                "scenario": scenario,
                "target": row_target or (target or ""),
                "correlation_id": cid,
                "auth_token": auth_token,
                "status": "OK",
                "response": resp_raw if resp_raw is not None else "",
                "graphql_response": gql_resp,
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
        key = f"{op}::{touch}" if touch else op
        if not op or key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "id": key,
                "operation": op,
                "touchpoint": touch or None,
                "label": scenario_display_name(op, touch or None, ui=True)
                .replace("(UI)", "")
                .strip(),
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
            "source": "playwright_script",
            "recorded_at": _now(),
            "jwt_identity": ident,
            "jwt_identity_note": source_note,
        }
        if profile_id:
            item["our_profile_id"] = profile_id
        gql = r.get("graphql_response")
        if isinstance(gql, dict) and gql:
            item["graphql_response"] = gql
            if op and op not in gql and "data" not in gql:
                item["graphql_response"] = {op: gql}
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
