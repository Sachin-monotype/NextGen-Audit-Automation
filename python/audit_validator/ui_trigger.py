"""UI-trigger handoff + CasePilot MCP dispatch.

Flow:
  1. User selects touchpoint scenarios on Generate.
  2. Clicks **Generate in UI** → we persist a handoff job with TestRail + context.
  3. **Send to CasePilot** queues ``run_testrail_ui_tests`` on the local connector.
  4. GraphQL/BFF response returns Cloudflare-safe ``correlation-id`` header.
  5. We pair raw ↔ enriched using that id (see :mod:`audit_validator.correlation`).
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from audit_validator.casepilot_mcp import (
    CasePilotMcpClient,
    CasePilotMcpError,
    extract_casepilot_job_ids,
    health_check,
    load_casepilot_config,
    parse_testrail_case_ids,
)
from audit_validator.ui_testrail_map import format_case_ids, map_selection_to_case_ids


_LOCK = threading.Lock()

# CasePilot agent must emit one of these in step reasoning / notes after the mutation.
_AUDIT_RESULT_RE = re.compile(
    r"AUDIT_RESULT\|(?P<body>[^\n\r]+)",
    re.IGNORECASE,
)
_KV_RE = re.compile(r"(?P<k>[A-Za-z0-9_]+)\s*=\s*(?P<v>[^|\s]+)")
_CID_LINE_RE = re.compile(
    r"(?:AUDIT_CORRELATION_ID|correlation[-_ ]?id)\s*[:=]\s*[\"']?(?P<cid>[A-Za-z0-9\-_]{8,})",
    re.IGNORECASE,
)
_UUID_RE = re.compile(
    r"\b(?P<cid>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
    re.IGNORECASE,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _store_dir(project_root: Path) -> Path:
    d = project_root / "reports" / "ui-trigger"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _append_log(data: dict[str, Any], line: str) -> None:
    logs = list(data.get("logs") or [])
    logs.append(f"{_now()}  {line}")
    data["logs"] = logs[-500:]


def _write_job(project_root: Path, data: dict[str, Any]) -> dict[str, Any]:
    job_id = str(data.get("id") or "")
    if not job_id:
        raise ValueError("job id required")
    path = _store_dir(project_root) / f"{job_id}.json"
    data["updated_at"] = _now()
    with _LOCK:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        latest = _store_dir(project_root) / "latest.json"
        latest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return data


def _walk_strings(node: Any, out: list[str], *, depth: int = 0) -> None:
    if depth > 12:
        return
    if isinstance(node, str):
        if node.strip():
            out.append(node)
    elif isinstance(node, dict):
        for v in node.values():
            _walk_strings(v, out, depth=depth + 1)
    elif isinstance(node, list):
        for v in node:
            _walk_strings(v, out, depth=depth + 1)


def extract_audit_details_from_casepilot_result(
    run_status: dict[str, Any],
    *,
    default_operation: str = "",
    default_touchpoint: str = "",
) -> list[dict[str, Any]]:
    """Parse CasePilot get_run_status payload for correlation_id + operation details.

    The UI agent is instructed to emit lines like::

        AUDIT_RESULT|operation=activateFamily|correlation_id=<uuid>|touchpoint=global
    """
    blobs: list[str] = []
    _walk_strings(run_status.get("result") or run_status, blobs)
    text = "\n".join(blobs)

    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(cid: str, **extra: Any) -> None:
        cid = (cid or "").strip()
        if not cid or cid in seen:
            return
        try:
            from audit_validator.touchpoint.scenarios import is_valid_correlation_id

            if not is_valid_correlation_id(cid):
                return
        except Exception:  # noqa: BLE001
            if cid.startswith("<") or "your-uuid" in cid.lower():
                return
        op = str(extra.get("operation") or default_operation or "").strip()
        touch = str(extra.get("touchpoint") or default_touchpoint or "").strip()
        # Prefer selection when agent mislabels project_list (etc.) as Discovery/global.
        if (
            default_touchpoint
            and default_operation
            and (not op or op == default_operation)
            and _short_touch_label(touch) == "global"
            and _short_touch_label(default_touchpoint) != "global"
        ):
            touch = default_touchpoint
        try:
            from audit_validator.touchpoint.scenarios import is_placeholder_scenario

            if is_placeholder_scenario(op, touch):
                return
        except Exception:  # noqa: BLE001
            if "<" in op or "<" in touch:
                return
        seen.add(cid)
        found.append(
            {
                "correlation_id": cid,
                "operation": op,
                "touchpoint": touch,
                "source": extra.get("source") or "casepilot_result",
                "raw_marker": extra.get("raw_marker"),
                "casepilot_job_id": run_status.get("job_id"),
                "issue_key": run_status.get("issue_key"),
                "recorded_at": _now(),
            }
        )

    for m in _AUDIT_RESULT_RE.finditer(text):
        body = m.group("body")
        kvs = {km.group("k").lower(): km.group("v") for km in _KV_RE.finditer(body)}
        cid = (
            kvs.get("correlation_id")
            or kvs.get("correlation-id")
            or kvs.get("cid")
            or kvs.get("xcorrelationid")
            or ""
        )
        _add(
            cid,
            operation=kvs.get("operation") or kvs.get("op") or default_operation,
            touchpoint=kvs.get("touchpoint") or kvs.get("touch") or default_touchpoint,
            source="audit_result_marker",
            raw_marker=m.group(0)[:240],
        )

    for m in _CID_LINE_RE.finditer(text):
        _add(m.group("cid"), source="correlation_line")

    # Last resort: UUID near the word correlation (avoid harvesting every UUID in the log)
    for m in re.finditer(
        r"correlation[^\n]{0,40}?" + _UUID_RE.pattern,
        text,
        re.IGNORECASE,
    ):
        _add(m.group("cid"), source="uuid_near_correlation")

    return found


def apply_extracted_results(
    project_root: Path,
    job: dict[str, Any],
    extracted: list[dict[str, Any]],
) -> dict[str, Any]:
    """Merge extracted UI details into the handoff and register for Compare."""
    if not extracted:
        return job

    selection = [s for s in (job.get("selection") or []) if isinstance(s, dict)]
    default_op = ""
    default_touch = ""
    if len(selection) == 1:
        default_op = str(selection[0].get("operation") or "")
        default_touch = str(selection[0].get("touchpoint") or "")

    results = list(job.get("results") or [])
    existing = {
        str(r.get("correlation_id") or "").strip()
        for r in results
        if isinstance(r, dict)
    }

    try:
        from audit_validator.generation_tracker import record_generation
        from audit_validator.simulation.trigger_context import (
            build_trigger_context,
            save_trigger_context,
        )
    except Exception:  # noqa: BLE001
        record_generation = None  # type: ignore[assignment]
        build_trigger_context = None  # type: ignore[assignment]
        save_trigger_context = None  # type: ignore[assignment]

    for item in extracted:
        cid = str(item.get("correlation_id") or "").strip()
        if not cid or cid in existing:
            continue
        try:
            from audit_validator.touchpoint.scenarios import is_valid_correlation_id

            if not is_valid_correlation_id(cid):
                _append_log(job, f"⚠ skipped invalid correlation_id={cid!r}")
                continue
        except Exception:  # noqa: BLE001
            if "your-uuid" in cid.lower() or "<" in cid:
                continue
        op = str(item.get("operation") or default_op or "").strip()
        touch = str(item.get("touchpoint") or default_touch or "").strip()
        # If agent omitted operation and we have multiple selection items, try label match later
        if not op and selection:
            op = str(selection[0].get("operation") or "")
        # Prefer the user's selected touchpoint when CasePilot mis-labels (e.g. global
        # instead of project_list) for the same primary operation.
        if len(selection) == 1 and default_touch and op == default_op:
            sel_short = _short_touch_label(default_touch)
            got_short = _short_touch_label(touch) if touch else ""
            if sel_short and got_short != sel_short:
                if got_short in {"", "global"} or sel_short == "project_list":
                    _append_log(
                        job,
                        f"⚠ remapping touchpoint {got_short or '∅'} → {sel_short} "
                        f"(selection={default_touch})",
                    )
                    touch = default_touch
        try:
            from audit_validator.touchpoint.scenarios import is_placeholder_scenario

            if is_placeholder_scenario(op, touch):
                _append_log(job, f"⚠ skipped placeholder AUDIT_RESULT op={op!r} touch={touch!r}")
                continue
        except Exception:  # noqa: BLE001
            if "<" in op or "<" in touch:
                continue
        row = {
            "operation": op,
            "touchpoint": touch,
            "correlation_id": cid,
            "correlation_source": "response_header:correlation-id",
            "status": "triggered",
            "source": item.get("source"),
            "casepilot_job_id": item.get("casepilot_job_id"),
            "issue_key": item.get("issue_key"),
            "raw_marker": item.get("raw_marker"),
            "recorded_at": item.get("recorded_at") or _now(),
        }
        results.append(row)
        existing.add(cid)
        _append_log(
            job,
            f"✓ captured correlation_id={cid} op={op or '?'} touch={touch or '-'}",
        )
        if record_generation and op:
            try:
                from audit_validator.touchpoint.scenarios import scenario_display_name

                display = scenario_display_name(op, touch, ui=True)
                record_generation(
                    op,
                    cid,
                    project_root=project_root,
                    kind="ui",
                    meta={
                        "touchpoint": touch,
                        "ui_trigger_job_id": job.get("id"),
                        "source": "casepilot",
                        "display": display,
                    },
                )
                # Also register UI display name (…(ui)) for Generation Status / Compare
                if display != op:
                    record_generation(
                        display,
                        cid,
                        project_root=project_root,
                        kind="ui",
                        meta={"touchpoint": touch, "ui_trigger_job_id": job.get("id")},
                    )
            except Exception as exc:  # noqa: BLE001
                _append_log(job, f"⚠ could not record_generation: {exc}")
        if build_trigger_context and save_trigger_context and op:
            try:
                ctx = build_trigger_context(
                    operation=op,
                    correlation_id=cid,
                    success=True,
                )
                save_trigger_context(project_root, op, ctx)
            except Exception as exc:  # noqa: BLE001
                _append_log(job, f"⚠ could not save trigger context: {exc}")

    job["results"] = results
    cids = [str(r.get("correlation_id")) for r in results if isinstance(r, dict) and r.get("correlation_id")]
    ops = sorted(
        {
            str(r.get("operation"))
            for r in results
            if isinstance(r, dict) and r.get("operation")
        }
    )
    job["verification"] = {
        "ready": bool(cids),
        "correlation_ids": cids,
        "operations": ops or [str(s.get("operation")) for s in selection if s.get("operation")],
        "note": (
            "UI browser may close — continue Compare/verify in this app using these "
            "correlation_ids (raw↔enriched pairing)."
        ),
    }
    return job


def create_ui_trigger_job(
    project_root: Path,
    *,
    selection: list[dict[str, Any]],
    test_case_id: str = "",
    cta_text: str = "",
    notes: str = "",
    extra: dict[str, Any] | None = None,
    dispatch: bool = False,
) -> dict[str, Any]:
    """Persist a Generate-in-UI handoff. Optionally dispatch to CasePilot immediately."""
    cfg = load_casepilot_config()
    job_id = str(uuid.uuid4())
    # Prefer per-scenario test_case_id on selection rows; fall back to bulk / auto-map.
    per_item: list[int] = []
    for s in selection:
        if not isinstance(s, dict):
            continue
        raw_cid = s.get("test_case_id") or s.get("testcase_id") or ""
        parsed = parse_testrail_case_ids(str(raw_cid)) if raw_cid else []
        if parsed:
            per_item.extend(int(x) for x in parsed if str(x).isdigit() or isinstance(x, int))
        else:
            from audit_validator.ui_testrail_map import case_id_for_selection_item

            mapped_one = case_id_for_selection_item(s)
            if mapped_one:
                per_item.append(int(mapped_one))
    # Dedupe preserving order
    seen_ids: set[int] = set()
    per_item_unique: list[int] = []
    for cid in per_item:
        if cid not in seen_ids:
            seen_ids.add(cid)
            per_item_unique.append(cid)

    mapped = map_selection_to_case_ids(selection)
    manual = parse_testrail_case_ids(test_case_id)
    # Priority: bulk manual field → per-item ids → catalog auto-map
    case_ids = manual or per_item_unique or mapped
    testcase_display = (test_case_id or "").strip() or format_case_ids(case_ids) or "TR-TBD"
    payload = {
        "id": job_id,
        "kind": "ui_trigger",
        "status": "pending_agent",  # pending_agent | queued | running | completed | failed
        "created_at": _now(),
        "updated_at": _now(),
        "selection": selection,
        "testrail": {
            "testcase_id": testcase_display,
            "case_ids": case_ids,
            "mapped_case_ids": mapped,
            "manual_case_ids": manual,
        },
        "cta_text": cta_text or "",
        "notes": notes or "",
        "correlation_strategy": {
            "request_header": None,
            "response_header": "correlation-id",
            "envelope_fields": ["correlationId", "xCorrelationId"],
            "note": (
                "UI triggers must NOT rely on x-correlation-id (Cloudflare rewrite). "
                "Pair raw/enrich with the response header correlation-id. "
                "Capture AUDIT_RESULT for every GraphQL mutation in the flow "
                "(including createProject / createAsset / add* helpers)."
            ),
        },
        "agent": {
            "channel": "casepilot_mcp",
            "mcp_url": cfg.mcp_url,
            "connected": cfg.configured,
            "send_status": "ready" if cfg.configured else "missing_api_key",
            "casepilot_job_ids": [],
            "last_error": None,
        },
        "results": [],
        "logs": [],
        "verification": {
            "ready": False,
            "correlation_ids": [],
            "operations": [str(s.get("operation") or "") for s in selection if isinstance(s, dict)],
            "note": (
                "When CasePilot finishes we auto-extract correlation_ids (including intermediate "
                "mutations) and verify raw/enrich into Generation Status."
            ),
            "auto_verify": True,
        },
        "extra": extra or {},
    }
    _append_log(
        payload,
        f"handoff created · {len(selection)} scenario(s) · testrail={testcase_display}",
    )
    if mapped:
        _append_log(payload, f"  auto-mapped CasePilot cases: {format_case_ids(mapped)}")
    if manual and manual != mapped:
        _append_log(payload, f"  manual override cases: {format_case_ids([int(x) for x in manual if str(x).isdigit()])}")
    for i, st in enumerate(
        ui_steps_for_selection([s for s in selection if isinstance(s, dict)]), 1
    ):
        _append_log(payload, f"  plan {i}. {st.get('step')}")
    _write_job(project_root, payload)
    if dispatch:
        payload = dispatch_ui_trigger_job(project_root, job_id) or payload
    return payload


def list_ui_trigger_jobs(project_root: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    root = _store_dir(project_root)
    files = sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out: list[dict[str, Any]] = []
    for path in files:
        if path.name == "latest.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                out.append(data)
        except Exception:
            continue
        if len(out) >= limit:
            break
    return out


def get_ui_trigger_job(project_root: Path, job_id: str) -> dict[str, Any] | None:
    path = _store_dir(project_root) / f"{job_id}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def record_ui_trigger_result(
    project_root: Path,
    job_id: str,
    *,
    operation: str,
    correlation_id: str,
    status: str = "triggered",
    response_headers: dict[str, str] | None = None,
    error: str | None = None,
) -> dict[str, Any] | None:
    """Append a per-scenario result once the UI agent reports back."""
    path = _store_dir(project_root) / f"{job_id}.json"
    with _LOCK:
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        results = list(data.get("results") or [])
        results.append(
            {
                "operation": operation,
                "correlation_id": correlation_id,
                "correlation_source": "response_header:correlation-id",
                "status": status,
                "error": error,
                "response_headers": response_headers or {},
                "recorded_at": _now(),
            }
        )
        data["results"] = results
        data["updated_at"] = _now()
        if status in {"failed", "error"}:
            data["status"] = "failed"
        elif all(str(r.get("status")) in {"triggered", "pass", "PASS"} for r in results):
            data["status"] = "completed" if results else data.get("status")
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return data


# Stable seed families used across audit automation (fast search path).
_SEED_FAMILIES = ("910042901", "910011880")


def _short_touch_label(touch: str) -> str:
    t = (touch or "").lower().replace("/", " ").replace(">", " ").replace("_", " ")
    t = " ".join(t.split())
    if "project" in t and "list" in t:
        return "project_list"
    if "favourite" in t or "favorite" in t:
        return "favourite"
    if t == "project" or t.startswith("project "):
        return "project"
    if "list" in t or "fontlist" in t:
        return "list"
    if "discover" in t or "browse" in t or "search" in t or "global" in t or not t:
        return "global"
    return t.replace(" ", "_") or "global"


def _audit_emit_step(op: str, touch_short: str) -> str:
    return (
        f"In DevTools Network, filter GraphQL/BFF for operationName={op} (or the request "
        f"whose payload contains \"{op}\"). Open THAT response only — ignore other GraphQL "
        f"calls on the page. Copy response header correlation-id (NOT x-correlation-id). "
        f"Emit exactly one line with the real UUID (never YOUR-UUID or angle brackets): "
        f"AUDIT_RESULT|operation={op}|correlation_id=PASTE-REAL-UUID|touchpoint={touch_short}"
    )


def _fast_search_activate_steps(op: str, touch: str, touch_short: str) -> list[dict[str, str]]:
    """Minimal path: search seed family → deactivate if needed → activate → emit."""
    seeds = " or ".join(_SEED_FAMILIES)
    return [
        {
            "op": op,
            "touchpoint": touch,
            "step": (
                f"FAST PATH — do not wander the UI. Use global Search for family id {seeds} "
                f"(prefer {_SEED_FAMILIES[0]}). Open that family card/detail."
            ),
        },
        {
            "op": op,
            "touchpoint": touch,
            "step": (
                "If the family is already activated, Deactivate it once, wait for success, "
                "then continue. Skip unrelated menus, projects, lists, or favourites."
            ),
        },
        {
            "op": op,
            "touchpoint": touch,
            "step": (
                "Activate the family from the card toggle or family-detail Activate button "
                f"(global / Discovery scope — touchpoint={touch_short})."
            ),
        },
        {
            "op": op,
            "touchpoint": touch,
            "step": _audit_emit_step(op, touch_short),
        },
        {
            "op": op,
            "touchpoint": touch,
            "step": "Close the browser. Do not perform extra clicks after AUDIT_RESULT.",
        },
    ]


def ui_steps_for_selection(selection: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Concise UI steps for CasePilot (override verbose TestRail prose).

    Prefer search → deactivate-if-needed → act. Avoid random navigation.
    """
    steps: list[dict[str, str]] = []
    for s in selection:
        if not isinstance(s, dict):
            continue
        op = str(s.get("operation") or "").strip()
        touch = str(s.get("touchpoint") or "").strip()
        label = str(s.get("label") or op).strip()
        extra = str(s.get("notes") or s.get("extra_details") or "").strip()
        touch_short = _short_touch_label(touch)
        touch_canon = touch or {
            "global": "Discovery/Browse (global)",
            "list": "List (FONTLIST)",
            "favourite": "Favourite",
            "project": "Project",
            "project_list": "Project > List",
        }.get(touch_short, touch)

        if extra:
            steps.append(
                {
                    "op": op,
                    "touchpoint": touch_canon,
                    "step": f"Operator hint for {label}: {extra}",
                }
            )

        if op == "activateFamily" and touch_short == "global":
            steps.extend(_fast_search_activate_steps(op, touch_canon, "global"))
            continue

        if op == "activateFamily" and touch_short == "favourite":
            seeds = " or ".join(_SEED_FAMILIES)
            steps.extend(
                [
                    {
                        "op": op,
                        "touchpoint": touch_canon,
                        "step": (
                            f"Search family {seeds}. Add to Favourites if missing "
                            "(skip if already favourited)."
                        ),
                    },
                    {
                        "op": op,
                        "touchpoint": touch_canon,
                        "step": (
                            "Open Favourites. Deactivate the family if already activated, "
                            "then Activate from the Favourites context (listType=FAVORITE)."
                        ),
                    },
                    {"op": op, "touchpoint": touch_canon, "step": _audit_emit_step(op, "favourite")},
                    {
                        "op": op,
                        "touchpoint": touch_canon,
                        "step": "Close the browser after AUDIT_RESULT.",
                    },
                ]
            )
            continue

        if op == "activateFamily" and touch_short == "list":
            seeds = " or ".join(_SEED_FAMILIES)
            steps.extend(
                [
                    {
                        "op": op,
                        "touchpoint": touch_canon,
                        "step": (
                            "Create a new font list (Assets) OR open an existing editable list. "
                            "Keep this short — no unrelated browsing."
                        ),
                    },
                    {
                        "op": op,
                        "touchpoint": touch_canon,
                        "step": f"Add family {seeds} to that list (addFontListFamilies).",
                    },
                    {
                        "op": op,
                        "touchpoint": touch_canon,
                        "step": (
                            "From the LIST context, deactivate if needed, then Activate family "
                            "(FONTLIST scope — must include listIds in the mutation)."
                        ),
                    },
                    {
                        "op": op,
                        "touchpoint": touch_canon,
                        "step": (
                            "Also emit AUDIT_RESULT for createAsset / addFontListFamilies if those "
                            "mutations ran, then: " + _audit_emit_step(op, "list")
                        ),
                    },
                    {
                        "op": op,
                        "touchpoint": touch_canon,
                        "step": "Close the browser after AUDIT_RESULT lines.",
                    },
                ]
            )
            continue

        if op == "activateFamily" and touch_short == "project":
            seeds = " or ".join(_SEED_FAMILIES)
            steps.extend(
                [
                    {
                        "op": op,
                        "touchpoint": touch_canon,
                        "step": "Create a project (or open a recent editable project). Stay on project flow.",
                    },
                    {
                        "op": op,
                        "touchpoint": touch_canon,
                        "step": f"Add family {seeds} to the project (addFontProjectFamilies).",
                    },
                    {
                        "op": op,
                        "touchpoint": touch_canon,
                        "step": (
                            "From the PROJECT family page, deactivate if needed, then Activate "
                            "(FONTPROJECT — mutation must include projectId)."
                        ),
                    },
                    {
                        "op": op,
                        "touchpoint": touch_canon,
                        "step": (
                            "Emit AUDIT_RESULT for createProject / addFontProjectFamilies if run, then: "
                            + _audit_emit_step(op, "project")
                        ),
                    },
                    {
                        "op": op,
                        "touchpoint": touch_canon,
                        "step": "Close the browser after AUDIT_RESULT lines.",
                    },
                ]
            )
            continue

        if op == "activateFamily" and touch_short == "project_list":
            seeds = " or ".join(_SEED_FAMILIES)
            steps.extend(
                [
                    {
                        "op": op,
                        "touchpoint": touch_canon,
                        "step": (
                            "CRITICAL — Project > List is NOT the same as List alone. "
                            "You must create/open a PROJECT, then create a LIST inside that project."
                        ),
                    },
                    {
                        "op": op,
                        "touchpoint": touch_canon,
                        "step": f"Create/open project → add family {seeds} to the project.",
                    },
                    {
                        "op": op,
                        "touchpoint": touch_canon,
                        "step": (
                            "Inside that project, create a font list (createAsset under project), "
                            f"then add family {seeds} to that project list (addFontListFamilies)."
                        ),
                    },
                    {
                        "op": op,
                        "touchpoint": touch_canon,
                        "step": (
                            "Activate the family FROM the project-list context so the mutation "
                            "includes BOTH projectId AND listIds. Deactivate first if already on."
                        ),
                    },
                    {
                        "op": op,
                        "touchpoint": touch_canon,
                        "step": (
                            "Emit AUDIT_RESULT for each helper mutation "
                            "(createProject, addFontProjectFamilies, createAsset, addFontListFamilies), "
                            "then: " + _audit_emit_step(op, "project_list")
                        ),
                    },
                    {
                        "op": op,
                        "touchpoint": touch_canon,
                        "step": "Close the browser. Do not activate from global/list-only screens.",
                    },
                ]
            )
            continue

        if op == "deactivateFamilies" and touch_short == "global":
            seeds = " or ".join(_SEED_FAMILIES)
            steps.extend(
                [
                    {
                        "op": op,
                        "touchpoint": touch_canon,
                        "step": (
                            f"Search family {seeds}. If not activated, Activate once first, "
                            "then Deactivate from Discovery/card."
                        ),
                    },
                    {"op": op, "touchpoint": touch_canon, "step": _audit_emit_step(op, "global")},
                    {
                        "op": op,
                        "touchpoint": touch_canon,
                        "step": "Close the browser after AUDIT_RESULT.",
                    },
                ]
            )
            continue

        if op == "activateStyle" and touch_short == "global":
            seeds = " or ".join(_SEED_FAMILIES)
            steps.extend(
                [
                    {
                        "op": op,
                        "touchpoint": touch_canon,
                        "step": (
                            f"Search family {seeds}, open family detail, activate one style "
                            "(not the whole family) from style row / right-click Activate Style."
                        ),
                    },
                    {"op": op, "touchpoint": touch_canon, "step": _audit_emit_step(op, "global")},
                    {
                        "op": op,
                        "touchpoint": touch_canon,
                        "step": "Close the browser after AUDIT_RESULT.",
                    },
                ]
            )
            continue

        # Generic compact path for other ops
        steps.append(
            {
                "op": op,
                "touchpoint": touch_canon,
                "step": (
                    f"Perform {label} in NextGen UI with the shortest path. "
                    f"Prefer Search with seed families {_SEED_FAMILIES[0]}/{_SEED_FAMILIES[1]} when fonts are involved. "
                    "Do not explore unrelated pages."
                ),
            }
        )
        steps.append(
            {
                "op": op,
                "touchpoint": touch_canon,
                "step": _audit_emit_step(op, touch_short or "global"),
            }
        )
        steps.append(
            {
                "op": op,
                "touchpoint": touch_canon,
                "step": "Close the browser after AUDIT_RESULT.",
            }
        )
    return steps


def _build_context(job: dict[str, Any]) -> tuple[str, str, dict[str, str]]:
    selection = job.get("selection") or []
    lines = []
    for i, s in enumerate(selection, 1):
        if not isinstance(s, dict):
            continue
        label = s.get("label") or s.get("operation") or "?"
        touch = s.get("touchpoint") or ""
        sid = s.get("id") or ""
        lines.append(
            f"{i}. {label}"
            + (f" · touchpoint={touch}" if touch else "")
            + (f" · id={sid}" if sid else "")
        )
    ui_steps = ui_steps_for_selection([s for s in selection if isinstance(s, dict)])
    step_lines = [f"{i}. {st['step']}" for i, st in enumerate(ui_steps, 1)]
    summary = (job.get("cta_text") or "").strip() or (
        f"Execute {len(selection)} NextGen audit UI scenario(s) for raw/enrich verification"
    )
    description = "\n".join(
        [
            "NextGen Audit Automation — Generate in UI handoff",
            "",
            "## FOLLOW THESE STEPS EXACTLY (override TestRail prose if it conflicts)",
            "Keep this path simple. Do not invent project/list/favourite setup unless listed below.",
            *step_lines,
            "",
            "## Selection",
            *lines,
            "",
            "## Extra notes",
            (job.get("notes") or "").strip() or "(none)",
            "",
            "## Correlation (CRITICAL)",
            "- Capture response header correlation-id (Cloudflare-safe) for EVERY GraphQL mutation.",
            "- Intermediate helpers also count: createProject, createAsset, addFontProjectFamilies,",
            "  addFontListFamilies, addFavoriteFamilies, deactivateFamilies, activateFamily, etc.",
            "- Never use x-correlation-id.",
            "- Emit one line per mutation with REAL uuids (never YOUR-UUID or <uuid>):",
            "  AUDIT_RESULT|operation=activateFamily|correlation_id=7a4f9f30-f35b-400c-89af-3cc21b15c51a|touchpoint=project_list",
            "- Pick the GraphQL call whose operationName / body matches the mutation "
            "(e.g. activateFamily), not browse/search/query traffic.",
            "- Example for Project > List flow:",
            "  AUDIT_RESULT|operation=createProject|correlation_id=...|touchpoint=project",
            "  AUDIT_RESULT|operation=addFontProjectFamilies|correlation_id=...|touchpoint=project",
            "  AUDIT_RESULT|operation=createAsset|correlation_id=...|touchpoint=list",
            "  AUDIT_RESULT|operation=addFontListFamilies|correlation_id=...|touchpoint=list",
            "  AUDIT_RESULT|operation=activateFamily|correlation_id=...|touchpoint=project_list",
            "- Then close the browser; the audit app auto-verifies raw↔enriched for ALL captured ops.",
            "- NEVER emit literal tokens like <op>, <touch>, YOUR-UUID, or <uuid>.",
        ]
    )
    # Fold per-scenario notes into description when present
    per_notes = []
    for s in selection:
        if not isinstance(s, dict):
            continue
        n = str(s.get("notes") or s.get("extra_details") or "").strip()
        if n:
            per_notes.append(f"- {s.get('label') or s.get('operation')}: {n}")
    if per_notes:
        description += "\n\n## Per-scenario hints\n" + "\n".join(per_notes)

    hints = {
        "correlation_header": "correlation-id",
        "avoid_header": "x-correlation-id",
        "audit_result_format": (
            "AUDIT_RESULT|operation=activateFamily|correlation_id=<real-uuid>|touchpoint=global"
        ),
        "capture_intermediate_mutations": "true",
        "product": "NextGen",
        "source": "nextgen-audit-automation",
        "after_ui": "close_browser_auto_verify_in_audit_app",
        "prefer_steps": "context_over_testrail",
        "seed_families": ",".join(_SEED_FAMILIES),
    }
    return summary, description, hints



def dispatch_ui_trigger_job(project_root: Path, job_id: str) -> dict[str, Any] | None:
    """Queue CasePilot ``run_testrail_ui_tests`` for this handoff job."""
    job = get_ui_trigger_job(project_root, job_id)
    if not job:
        return None

    cfg = load_casepilot_config()
    agent = dict(job.get("agent") or {})
    testrail = dict(job.get("testrail") or {})
    case_ids = list(testrail.get("case_ids") or []) or parse_testrail_case_ids(
        testrail.get("testcase_id")
    )
    if not case_ids:
        case_ids = map_selection_to_case_ids(
            [s for s in (job.get("selection") or []) if isinstance(s, dict)]
        )
    testrail["case_ids"] = case_ids
    job["testrail"] = testrail

    if not cfg.configured:
        agent.update({"send_status": "missing_api_key", "last_error": "CASEPILOT_API_KEY not set"})
        job["agent"] = agent
        job["status"] = "failed"
        return _write_job(project_root, job)

    if not case_ids:
        agent.update(
            {
                "send_status": "missing_testcase_id",
                "last_error": "Provide a numeric TestRail case id (e.g. C73298777)",
            }
        )
        job["agent"] = agent
        job["status"] = "failed"
        return _write_job(project_root, job)

    if not cfg.ui_config_ready():
        agent.update(
            {
                "send_status": "credentials_required",
                "last_error": "Set CASEPILOT_UI_BASE_URL / USERNAME / PASSWORD (or OAUTH_*)",
            }
        )
        job["agent"] = agent
        job["status"] = "failed"
        return _write_job(project_root, job)

    summary, description, hints = _build_context(job)
    try:
        from audit_validator.env_profiles import get_audit_profile

        profile = get_audit_profile()
        # Always drive CasePilot at the currently selected AUDIT_TARGET NextGen URL
        # (not a stale CASEPILOT_UI_BASE_URL pinned to PP).
        ui_cfg = cfg.ui_config()
        ui_cfg["base_url"] = (
            (os.getenv("NEXTGEN_UI_URL") or "").strip()
            or profile.nextgen_ui_url
            or ui_cfg.get("base_url")
            or ""
        )
        hints = {
            **hints,
            "audit_target": profile.name,
            "nextgen_ui_url": ui_cfg["base_url"],
            "mongo_db": (os.getenv("MONGO_DB_NAME") or "").strip(),
        }
        description = (
            description
            + f"\n\n## Environment\n- AUDIT_TARGET={profile.name}\n"
            + f"- NextGen UI: {ui_cfg['base_url']}\n"
            + "Use this URL only — do not switch environments mid-run.\n"
        )

        client = CasePilotMcpClient(cfg)
        # Preview cases first (surface not_found early)
        preview = client.fetch_testrail_cases(case_ids)
        if preview.get("not_found"):
            raise CasePilotMcpError(
                f"TestRail case(s) not found: {preview.get('not_found')}",
                payload=preview,
            )
        run = client.run_testrail_ui_tests(
            case_ids,
            ui_config=ui_cfg,
            context_summary=summary,
            context_description=description,
            context_hints=hints,
            wait_for_completion=False,
            stop_on_failure=True,
        )
        cp_jobs = extract_casepilot_job_ids(run)
        # Keep a compact but useful response snapshot for debugging freezes
        run_snap = {
            k: run.get(k)
            for k in (
                "ok",
                "error",
                "message",
                "job_id",
                "job_ids",
                "jobs",
                "runs",
                "results",
                "queued",
                "status",
            )
            if k in run
        }
        if not run_snap:
            run_snap = {"ok": run.get("ok"), "keys": sorted(str(k) for k in run.keys())[:40]}

        queued_ok = bool(cp_jobs)
        partial = bool(cp_jobs) and (
            run.get("ok") is False
            or (isinstance(run.get("queued_count"), int) and run.get("queued_count") < len(case_ids))
        )
        agent.update(
            {
                "channel": "casepilot_mcp",
                "connected": True,
                "send_status": "queued" if queued_ok else "error",
                "casepilot_job_ids": cp_jobs,
                "last_error": None
                if queued_ok
                else (
                    str(run.get("message") or run.get("error") or run.get("stop_reason") or "")
                    or "CasePilot returned no job_id — cannot poll UI run status"
                ),
                "preview": {
                    "found_count": preview.get("found_count"),
                    "case_ids": preview.get("case_ids"),
                },
                "run_response": run_snap,
                "planned_steps": ui_steps_for_selection(
                    [s for s in (job.get("selection") or []) if isinstance(s, dict)]
                ),
                "pending_case_ids": [
                    c
                    for c in case_ids
                    if str(c)
                    not in {
                        str(r.get("case_id"))
                        for r in (run.get("runs") or [])
                        if isinstance(r, dict) and r.get("job_id")
                    }
                ]
                if partial
                else [],
            }
        )
        job["agent"] = agent
        if not queued_ok:
            job["status"] = "failed"
            _append_log(job, f"✖ CasePilot queue failed: {agent.get('last_error')}")
            _append_log(
                job,
                f"  response keys={sorted(str(k) for k in run.keys())[:30]} snap={json.dumps(run_snap)[:500]}",
            )
        else:
            job["status"] = "queued"
            _append_log(
                job,
                f"▸ CasePilot queued job_ids={cp_jobs} · UI browser will run on local connector",
            )
            if partial:
                _append_log(
                    job,
                    f"⚠ Partial queue ({run.get('queued_count')}/{len(case_ids)}) — "
                    f"connector busy; remaining cases will retry on refresh: {agent.get('pending_case_ids')}",
                )
            for i, st in enumerate(agent.get("planned_steps") or [], 1):
                _append_log(job, f"  plan {i}. {st.get('step')}")
            _append_log(
                job,
                "▸ Waiting for UI event(s)… correlations (incl. intermediate mutations) "
                "auto-verify into Generation Status when browser closes",
            )
        return _write_job(project_root, job)
    except CasePilotMcpError as exc:
        agent.update({"send_status": "error", "last_error": str(exc), "connected": True})
        job["agent"] = agent
        job["status"] = "failed"
        job["extra"] = {**(job.get("extra") or {}), "casepilot_error_payload": exc.payload}
        return _write_job(project_root, job)
    except Exception as exc:
        agent.update({"send_status": "error", "last_error": str(exc)})
        job["agent"] = agent
        job["status"] = "failed"
        return _write_job(project_root, job)


def refresh_casepilot_status(project_root: Path, job_id: str) -> dict[str, Any] | None:
    """Poll CasePilot get_run_status, extract correlation_id, keep log open for verify."""
    job = get_ui_trigger_job(project_root, job_id)
    if not job:
        return None
    agent = dict(job.get("agent") or {})
    cp_ids = [
        int(x)
        for x in (agent.get("casepilot_job_ids") or [])
        if str(x).isdigit() or isinstance(x, int)
    ]
    if not cp_ids:
        return job
    selection = [s for s in (job.get("selection") or []) if isinstance(s, dict)]
    default_op = str(selection[0].get("operation") or "") if selection else ""
    default_touch = str(selection[0].get("touchpoint") or "") if selection else ""
    try:
        client = CasePilotMcpClient()
        statuses = []
        for jid in cp_ids:
            st = client.get_run_status(jid)
            if "job_id" not in st:
                st = {"job_id": jid, **st}
            statuses.append(st)
        agent["run_statuses"] = statuses
        finals = {str(s.get("status") or "").lower() for s in statuses}
        prev = str(job.get("status") or "")

        if finals & {"failed", "error", "cancelled"} and not (
            finals & {"completed", "passed", "pass", "success"}
        ):
            job["status"] = "failed"
            agent["send_status"] = "failed"
            if prev != "failed":
                _append_log(job, f"✖ CasePilot run failed · statuses={sorted(finals)}")
                # Still try to harvest any correlation markers from partial results
                extracted: list[dict[str, Any]] = []
                for st in statuses:
                    extracted.extend(
                        extract_audit_details_from_casepilot_result(
                            st,
                            default_operation=default_op,
                            default_touchpoint=default_touch,
                        )
                    )
                job = apply_extracted_results(project_root, job, extracted)
        elif finals and finals <= {"completed", "passed", "pass", "success"}:
            job["status"] = "completed"
            agent["send_status"] = "completed"
            if prev != "completed":
                _append_log(job, "✓ CasePilot UI run completed — extracting correlation_id…")
            extracted = []
            for st in statuses:
                extracted.extend(
                    extract_audit_details_from_casepilot_result(
                        st,
                        default_operation=default_op,
                        default_touchpoint=default_touch,
                    )
                )
            before = len(job.get("results") or [])
            job = apply_extracted_results(project_root, job, extracted)
            after = len(job.get("results") or [])
            if after == before:
                _append_log(
                    job,
                    "⚠ No AUDIT_RESULT/correlation_id found in CasePilot notes — "
                    "paste correlation_id in the log panel (fallback)",
                )
            else:
                _append_log(
                    job,
                    f"✓ Captured {after} correlation_id(s) including intermediate mutations — "
                    "auto-verifying raw/enrich…",
                )
                job["verification"] = {
                    **(job.get("verification") or {}),
                    "ready": True,
                    "auto_verify_pending": True,
                }
        else:
            job["status"] = "running"
            agent["send_status"] = "running"
            if prev not in {"running", "queued"}:
                _append_log(job, f"▸ CasePilot running… {sorted(finals) or ['pending']}")
            elif prev == "queued":
                _append_log(job, "▸ CasePilot running on connector (UI browser open)")
        job["agent"] = agent
        return _write_job(project_root, job)
    except Exception as exc:
        agent["last_error"] = str(exc)
        job["agent"] = agent
        _append_log(job, f"⚠ refresh error: {exc}")
        return _write_job(project_root, job)


def record_manual_ui_results(
    project_root: Path,
    job_id: str,
    results: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Manual paste of correlation_id(s) when CasePilot notes omitted AUDIT_RESULT."""
    job = get_ui_trigger_job(project_root, job_id)
    if not job:
        return None
    extracted = []
    for r in results:
        if not isinstance(r, dict):
            continue
        cid = str(r.get("correlation_id") or r.get("correlationId") or "").strip()
        if not cid:
            continue
        extracted.append(
            {
                "correlation_id": cid,
                "operation": str(r.get("operation") or "").strip(),
                "touchpoint": str(r.get("touchpoint") or "").strip(),
                "source": "manual",
                "recorded_at": _now(),
            }
        )
    if not extracted:
        return job
    _append_log(job, f"▸ Manual correlation_id paste · {len(extracted)} value(s)")
    job = apply_extracted_results(project_root, job, extracted)
    if job.get("status") in {"queued", "running", "pending_agent"}:
        job["status"] = "completed"
    return _write_job(project_root, job)


def finalize_ui_trigger_verification(
    project_root: Path,
    job_id: str,
    *,
    db: Any = None,
    progress: Any = None,
) -> dict[str, Any] | None:
    """After UI event + correlation_id: poll Mongo raw/enrich and write Generation Status."""
    job = get_ui_trigger_job(project_root, job_id)
    if not job:
        return None

    results = [r for r in (job.get("results") or []) if isinstance(r, dict) and r.get("correlation_id")]
    if not results:
        _append_log(
            job,
            "✖ Continue verification blocked — no correlation_id yet "
            "(paste from DevTools or wait for CasePilot AUDIT_RESULT)",
        )
        return _write_job(project_root, job)

    ops = sorted({str(r.get("operation") or "").strip() for r in results if r.get("operation")})
    if not ops:
        ops = [
            str(s.get("operation") or "").strip()
            for s in (job.get("selection") or [])
            if isinstance(s, dict) and s.get("operation")
        ]

    def _log(msg: str) -> None:
        _append_log(job, msg)
        if callable(progress):
            progress(msg)

    _log(f"▸ Continue verification · ops={ops} · cids={len(results)}")
    # Drop invalid template cids (YOUR-UUID) before Mongo lookup
    try:
        from audit_validator.touchpoint.scenarios import is_valid_correlation_id

        valid_results = [
            r for r in results if is_valid_correlation_id(str(r.get("correlation_id") or ""))
        ]
        skipped = len(results) - len(valid_results)
        if skipped:
            _log(f"⚠ dropped {skipped} invalid correlation_id placeholder(s)")
        results = valid_results
    except Exception:  # noqa: BLE001
        pass
    if not results:
        _log("✖ No valid correlation_id left after filtering placeholders")
        return _write_job(project_root, job)

    report: dict[str, Any] = {
        "job_id": job_id,
        "kind": "ui_trigger",
        "validate": True,
        "checked_at": _now(),
        "operations": [],
        "scenarios": [],
        "source": "generate_in_ui",
    }
    try:
        from audit_validator.generate_run_report import (
            _event_for_report,
            save_generate_run,
            summary_from_scenarios,
        )
        from audit_validator.touchpoint.scenarios import scenario_display_name

        # UI path: look up each cid directly — do NOT run the 90s owned-landing poll
        # (that blocks Continue verification forever when one cid never lands).
        scenarios: list[dict[str, Any]] = []
        for r in results:
            op = str(r.get("operation") or "").strip()
            touch = str(r.get("touchpoint") or "").strip()
            cid = str(r.get("correlation_id") or "").strip()
            if not op:
                continue
            try:
                from audit_validator.touchpoint.scenarios import is_placeholder_scenario

                if is_placeholder_scenario(op, touch):
                    _log(f"  ⚠ skip placeholder scenario op={op!r} touch={touch!r}")
                    continue
            except Exception:  # noqa: BLE001
                if "<" in op or "<" in touch:
                    continue
            display = scenario_display_name(op, touch, ui=True)
            raw_doc = None
            enr_doc = None
            if db is not None and cid:
                try:
                    raw2, enr2 = db.latest_pair(op, require_pair=False, correlation_id=cid)
                    if raw2:
                        raw_doc = _event_for_report(raw2)
                    if enr2:
                        enr_doc = _event_for_report(enr2)
                except Exception as exc:  # noqa: BLE001
                    _log(f"  ⚠ cid lookup for {op}: {exc}")

            raw_ok = bool(raw_doc)
            enr_ok = bool(enr_doc)
            if raw_ok and enr_ok:
                status = "PASS"
                remark = "UI-triggered · raw + enriched landed in Mongo"
            elif raw_ok and not enr_ok:
                status = "FAIL"
                remark = "UI-triggered · raw landed; enrichment missing"
            elif enr_ok and not raw_ok:
                status = "FAIL"
                remark = "UI-triggered · enriched landed; raw missing"
            elif cid:
                status = "FAIL"
                remark = "UI-triggered · correlation captured; event not in Mongo yet"
            else:
                status = "N/A"
                remark = "Missing correlation_id"

            scenarios.append(
                {
                    "scenario_id": f"{op}::{touch}" if touch else op,
                    "operation": op,
                    "touchpoint": touch,
                    "label": display,
                    "status": status,
                    "xCorrelationId": cid,
                    "correlation_id": cid,
                    "raw": raw_ok,
                    "enriched": enr_ok,
                    "raw_event": raw_doc,
                    "enriched_event": enr_doc,
                    "source": "ui",
                    "channel": "UI",
                    "ui_status": status,
                    "remark": remark,
                    "pairing_method": "owned_cid" if cid else None,
                }
            )
            _log(
                f"  · {display}: {status} raw={'yes' if raw_ok else 'no'} "
                f"enrich={'yes' if enr_ok else 'no'} cid={(cid or '')[:8]}"
            )

        report["scenarios"] = scenarios
        report["summary"] = summary_from_scenarios(scenarios)
        report["operations"] = [
            {
                "operation": s["label"] or s["operation"],
                "xCorrelationId": s.get("xCorrelationId"),
                "raw": s.get("raw"),
                "enriched": s.get("enriched"),
                "raw_event": s.get("raw_event"),
                "enriched_event": s.get("enriched_event"),
                "status": "success" if s.get("status") == "PASS" else "missing",
                "ui_status": s.get("ui_status"),
                "remark": s.get("remark"),
                "channel": "UI",
            }
            for s in scenarios
        ]
        save_generate_run(report, project_root=project_root)
        _log(
            f"✓ Generation Status saved · "
            f"PASS={report['summary'].get('pass')} FAIL={report['summary'].get('fail')} "
            f"(raw/enrich JSON attached)"
        )
        job["verification"] = {
            **(job.get("verification") or {}),
            "ready": True,
            "completed": True,
            "generate_run_saved": True,
            "correlation_ids": [str(r.get("correlation_id")) for r in results],
            "operations": ops,
            "note": "Generation Status updated with raw/enrich JSON — same as API generate.",
        }
        job["status"] = "completed"
        job["generate_run"] = {
            "summary": report.get("summary"),
            "scenarios": scenarios,
            "validate": True,
        }
    except Exception as exc:  # noqa: BLE001
        _log(f"✖ Verification finalize failed: {exc}")
        job["status"] = "failed"
        agent = dict(job.get("agent") or {})
        agent["last_error"] = str(exc)
        job["agent"] = agent
    return _write_job(project_root, job)


def casepilot_health() -> dict[str, Any]:
    return health_check()
