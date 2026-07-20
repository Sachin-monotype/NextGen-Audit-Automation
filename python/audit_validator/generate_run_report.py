"""Persist and verify a Generate run until events land in raw + enriched Mongo.

Primary key: owned ``xCorrelationId`` minted on trigger.
Fallback when the platform does not echo the header into the envelope:
``operation + actor.globalUserId + occurredAt window`` (and ``eventId`` /
``subject.id`` when recorded).

UI statuses (Generation Status):
- ``PASS`` — raw and enriched both present for our event
- ``FAIL`` — trigger failed, or only one side landed, or not found after settle
- ``N/A`` — skipped / no identity to verify (e.g. no cid and no actor fingerprint)
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .generation_tracker import get_owned_correlation, list_owned

_LOCK = threading.Lock()
_LAST_REL = Path("reports") / "generate-runs" / "last.json"

# Map internal status → UI status + default remark
_STATUS_META: dict[str, tuple[str, str]] = {
    "success": ("PASS", "Raw + enriched landed in Mongo"),
    "raw_only": ("FAIL", "Raw generated; enrichment not in Mongo yet"),
    "enrich_only": ("FAIL", "Enriched present; raw not generated / not ingested"),
    "missing": ("FAIL", "Event not found in Mongo (check ingestion / queues)"),
    "no_correlation": (
        "N/A",
        "No correlation id or actor fingerprint to verify this send",
    ),
    "trigger_failed": ("FAIL", "GraphQL / ingress / cron trigger failed"),
    "skipped": ("N/A", "Operation skipped in simulation"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value: Any) -> Any:
    """Make Mongo docs (ObjectId, datetime, …) JSON-serializable for reports/UI."""
    try:
        return json.loads(json.dumps(value, default=str))
    except Exception:
        return str(value) if value is not None else None


def _event_for_report(doc: Any) -> dict[str, Any] | None:
    if not isinstance(doc, dict):
        return None
    safe = _json_safe(doc)
    return safe if isinstance(safe, dict) else None


def _run_path(project_root: Path | None = None) -> Path:
    root = project_root or Path.cwd()
    return root / _LAST_REL


def save_generate_run(report: dict[str, Any], *, project_root: Path | None = None) -> Path:
    path = _run_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Prefer scenario-level PASS/FAIL when multi-touchpoint scenarios were run
    scenarios = report.get("scenarios")
    if isinstance(scenarios, list) and scenarios:
        report = {**report, "summary": summary_from_scenarios(scenarios)}
    payload = _json_safe({**report, "saved_at": _now()})
    with _LOCK:
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        job_id = str(report.get("job_id") or "anon")[:12]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive = path.parent / f"generate-run-{stamp}-{job_id}.json"
        archive.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def summary_from_scenarios(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    """PASS/FAIL/N/A counts from touchpoint scenario rows (not bare operation rollup)."""
    pass_n = 0
    fail_n = 0
    na_n = 0
    for s in scenarios:
        st = str(s.get("status") or "").upper()
        if st == "PASS":
            pass_n += 1
        elif st in {"FAIL", "ERROR"}:
            fail_n += 1
        elif st in {"SKIP", "N/A"}:
            na_n += 1
        elif s.get("error"):
            fail_n += 1
        else:
            na_n += 1
    return {
        "total": len(scenarios),
        "success": pass_n,
        "pass": pass_n,
        "fail": fail_n,
        "na": na_n,
        "needs_work": fail_n,
        "trigger_failed": fail_n,
        "no_correlation": 0,
        "raw_only": sum(1 for s in scenarios if s.get("raw") and not s.get("enriched")),
        "enrich_only": sum(1 for s in scenarios if s.get("enriched") and not s.get("raw")),
        "missing": sum(
            1
            for s in scenarios
            if str(s.get("status") or "").upper() == "PASS"
            and not s.get("raw")
            and not s.get("enriched")
        ),
    }


def load_last_generate_run(*, project_root: Path | None = None) -> dict[str, Any] | None:
    path = _run_path(project_root)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _owned_entry(operation: str, *, project_root: Path | None = None) -> dict[str, Any]:
    data = list_owned(project_root=project_root)
    entry = (data.get("by_operation") or {}).get(operation) or {}
    return entry if isinstance(entry, dict) else {}


def _ui_status_and_remark(row: dict[str, Any]) -> tuple[str, str]:
    internal = str(row.get("status") or "missing")
    ui, default_remark = _STATUS_META.get(internal, ("FAIL", internal))
    parts: list[str] = []
    trig = (row.get("trigger_status") or "").upper()
    if trig in {"FAIL", "XFAIL"}:
        err = str(row.get("trigger_error") or "").strip()
        parts.append(
            f"Trigger {trig}" + (f": {err[:180]}" if err else " — GraphQL/endpoint not hittable")
        )
        ui = "FAIL"
    method = row.get("pairing_method") or ""
    if method and method not in {"owned_cid", "none", ""}:
        parts.append(f"Matched via {method} (xCorrelationId not on envelope)")
    if not parts:
        parts.append(default_remark)
    if row.get("raw") and not row.get("enriched"):
        parts.append("Cannot validate enrichment — enrich queue empty for this event")
    if row.get("enriched") and not row.get("raw"):
        parts.append("Raw not generated / not dumped to Mongo")
    return ui, " · ".join(parts)


def _lookup(
    db: Any,
    op: str,
    *,
    cid: str,
    entry: dict[str, Any],
) -> tuple[Any, Any, str]:
    """Return (raw, enriched, pairing_method)."""
    if not db:
        return None, None, "none"
    if cid:
        raw, enriched = db.latest_pair(op, require_pair=False, correlation_id=cid)
        if raw or enriched:
            return raw, enriched, "owned_cid"
    # Fingerprint fallback — platform may drop x-correlation-id from the envelope.
    actor = str(entry.get("profile_id") or entry.get("globalUserId") or "").strip()
    since = str(entry.get("generated_at") or "").strip() or None
    event_id = str(entry.get("eventId") or entry.get("event_id") or "").strip() or None
    subject_id = ""
    subj = entry.get("subject_id") or entry.get("subject_ids")
    if isinstance(subj, list) and subj:
        subject_id = str(subj[0])
    elif subj:
        subject_id = str(subj)
    find = getattr(db, "find_fingerprint_pair", None)
    if callable(find):
        raw, enriched, method = find(
            op,
            actor_global_user_id=actor or None,
            since_iso=since,
            event_id=event_id,
            subject_id=subject_id or None,
        )
        return raw, enriched, method
    if actor:
        raw, enriched = db.latest_pair(
            op, require_pair=False, actor_global_user_id=actor
        )
        if raw or enriched:
            return raw, enriched, "actor_latest"
    return None, None, "none"


def verify_owned_queue_landing(
    db: Any,
    operations: list[str],
    *,
    project_root: Path | None = None,
    timeout_sec: float | None = None,
    poll_sec: float | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Poll Mongo until each owned event appears in raw + enrich, or timeout."""
    ops = [o for o in operations if (o or "").strip()]
    if not ops:
        return {
            "checked_at": _now(),
            "operations": [],
            "summary": {
                "total": 0,
                "success": 0,
                "needs_work": 0,
                "pass": 0,
                "fail": 0,
                "na": 0,
                "trigger_failed": 0,
                "no_correlation": 0,
            },
            "raw_queue": os.getenv("RAW_EVENTS_QUEUE", ""),
            "enriched_queue": os.getenv("ENRICHED_EVENTS_QUEUE", ""),
        }

    timeout = (
        timeout_sec
        if timeout_sec is not None
        else float(os.getenv("GENERATE_VERIFY_TIMEOUT_SEC", "90"))
    )
    interval = (
        poll_sec if poll_sec is not None else float(os.getenv("GENERATE_VERIFY_POLL_SEC", "5"))
    )

    def _emit(msg: str) -> None:
        if progress:
            try:
                progress(msg)
            except Exception:
                pass

    deadline = time.monotonic() + max(timeout, 0)
    by_op: dict[str, dict[str, Any]] = {}
    for op in ops:
        entry = _owned_entry(op, project_root=project_root)
        cid = str(entry.get("xCorrelationId") or "").strip() or (
            get_owned_correlation(op, project_root=project_root) or ""
        )
        if entry.get("cid_missing") or cid.startswith("missing-cid:"):
            cid = ""
        trigger = str(entry.get("trigger_status") or "").strip().upper()
        actor = str(entry.get("profile_id") or "").strip()
        can_fingerprint = bool(cid or actor or entry.get("eventId") or entry.get("generated_at"))
        by_op[op] = {
            "operation": op,
            "xCorrelationId": cid or None,
            "trigger_status": trigger or None,
            "trigger_error": entry.get("trigger_error") or entry.get("error"),
            "generated_at": entry.get("generated_at"),
            "profile_id": actor or None,
            "kind": entry.get("kind"),
            "raw": False,
            "enriched": False,
            "raw_event": None,
            "enriched_event": None,
            "status": "missing" if can_fingerprint else "no_correlation",
            "pairing_method": None,
            "occurred_at_raw": None,
            "occurred_at_enriched": None,
            "ui_status": "N/A" if not can_fingerprint else "FAIL",
            "remark": "",
        }

    _emit(
        f"▸ Verifying raw+enrich for {len(ops)} generated op(s) "
        f"(owned cid, then actor/event fingerprint; timeout {timeout:.0f}s)…"
    )

    while True:
        pending = 0
        for op, row in by_op.items():
            if row["status"] == "success":
                continue
            if row["status"] == "no_correlation" and not row.get("profile_id"):
                continue
            entry = _owned_entry(op, project_root=project_root)
            cid = str(row.get("xCorrelationId") or "")
            raw, enriched, method = _lookup(db, op, cid=cid, entry=entry)
            row["pairing_method"] = method if (raw or enriched) else row.get("pairing_method")
            row["raw"] = bool(raw)
            row["enriched"] = bool(enriched)
            row["raw_event"] = _event_for_report(raw)
            row["enriched_event"] = _event_for_report(enriched)
            if raw:
                row["occurred_at_raw"] = raw.get("occurredAt")
                # Backfill cid if envelope finally has it
                if not row.get("xCorrelationId") and raw.get("xCorrelationId"):
                    row["xCorrelationId"] = raw.get("xCorrelationId")
            if enriched:
                row["occurred_at_enriched"] = enriched.get("occurredAt")
            if raw and enriched:
                row["status"] = "success"
            elif raw and not enriched:
                row["status"] = "raw_only"
                pending += 1
            elif enriched and not raw:
                row["status"] = "enrich_only"
                pending += 1
            else:
                if row["status"] != "no_correlation":
                    row["status"] = "missing"
                pending += 1
        remaining = deadline - time.monotonic()
        if pending == 0 or remaining <= 0:
            break
        _emit(f"  … {pending} still need raw+enrich; next poll in {interval:.0f}s ({remaining:.0f}s left)")
        time.sleep(min(interval, max(remaining, 0.1)))

    for row in by_op.values():
        trig = (row.get("trigger_status") or "").upper()
        if trig in {"FAIL", "XFAIL"} and row["status"] != "success":
            row["status"] = "trigger_failed"
        elif trig == "SKIP":
            row["status"] = "skipped"
        ui, remark = _ui_status_and_remark(row)
        row["ui_status"] = ui
        row["remark"] = remark
        # Annotate channel: activateFamily(project_list)(BE) / …(UI)
        try:
            from audit_validator.touchpoint.scenarios import scenario_display_name

            bare = str(row.get("operation") or "")
            entry = _owned_entry(bare, project_root=project_root)
            touch = entry.get("touchpoint")
            kind = str(entry.get("kind") or row.get("kind") or "graphql").lower()
            row["channel"] = "UI" if kind == "ui" else "BE"
            row["operation"] = scenario_display_name(
                bare.split("(")[0] if "(" in bare else bare,
                touch,
                ui=kind == "ui",
                be=kind != "ui",
            )
        except Exception:
            pass

    rows = [by_op[op] for op in ops]
    success = [r for r in rows if r["status"] == "success"]
    needs_work = [r for r in rows if r.get("ui_status") != "PASS"]
    summary = {
        "total": len(rows),
        "success": len(success),
        "pass": sum(1 for r in rows if r.get("ui_status") == "PASS"),
        "fail": sum(1 for r in rows if r.get("ui_status") == "FAIL"),
        "na": sum(1 for r in rows if r.get("ui_status") == "N/A"),
        "needs_work": len(needs_work),
        "trigger_failed": sum(1 for r in rows if r["status"] == "trigger_failed"),
        "no_correlation": sum(1 for r in rows if r["status"] == "no_correlation"),
        "raw_only": sum(1 for r in rows if r["status"] == "raw_only"),
        "enrich_only": sum(1 for r in rows if r["status"] == "enrich_only"),
        "missing": sum(1 for r in rows if r["status"] == "missing"),
        "fingerprint_matched": sum(
            1
            for r in rows
            if r.get("ui_status") == "PASS"
            and (r.get("pairing_method") or "").startswith(("actor", "eventId"))
        ),
    }

    _emit(
        f"✓ Generation Status: {summary['pass']} PASS / {summary['fail']} FAIL / "
        f"{summary['na']} N/A of {summary['total']}"
    )
    for r in needs_work:
        _emit(f"  ⚠ {r['operation']}: {r.get('ui_status')} — {r.get('remark', '')[:120]}")

    return {
        "checked_at": _now(),
        "timeout_sec": timeout,
        "operations": rows,
        "summary": summary,
        "success_ops": [r["operation"] for r in success],
        "needs_work_ops": [r["operation"] for r in needs_work],
        "raw_found": [r["operation"] for r in rows if r.get("raw")],
        "enriched_found": [r["operation"] for r in rows if r.get("enriched")],
        "checked": ops,
        "raw_queue": os.getenv("RAW_EVENTS_QUEUE", ""),
        "enriched_queue": os.getenv("ENRICHED_EVENTS_QUEUE", ""),
    }
