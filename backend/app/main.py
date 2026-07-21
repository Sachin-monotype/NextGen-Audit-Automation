"""NextGen Audit Automation — FastAPI backend."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .audit_bridge import AuditBridge, JobStore, JobStatus
from .config import load_settings
from .db import AuditDatabase, FILTER_FIELDS
from .ingestion_manager import IngestionManager
from .retention import RetentionScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

settings = load_settings()
db = AuditDatabase(settings)
job_store = JobStore(persist_path=settings.audit_project_root / "reports" / "jobs-state.json")
ingestion = IngestionManager(settings)
bridge = AuditBridge(settings.audit_project_root, job_store, db, ingestion=ingestion)
retention = RetentionScheduler(db, settings.retention_max_docs, settings.retention_interval_sec)

app = FastAPI(title="NextGen Audit Automation", version="1.1.0")


@app.on_event("startup")
def _start_background_tasks() -> None:
    retention.start()
    # Keep RabbitMQ → Mongo dump running so Generate/Compare always see fresh pairs.
    # Opt out with INGEST_AUTO_START=false if you want pure manual control.
    import os

    if os.getenv("INGEST_AUTO_START", "true").strip().lower() in {"1", "true", "yes", "on"}:
        try:
            status = ingestion.start()
            logging.getLogger(__name__).info(
                "Live ingestion auto-started (running=%s)", status.get("running")
            )
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).warning("Live ingestion auto-start failed: %s", exc)


@app.on_event("shutdown")
def _stop_background_tasks() -> None:
    try:
        ingestion.stop()
    except Exception:  # noqa: BLE001
        pass
    retention.stop()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins + ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class GenerateRequest(BaseModel):
    operations: list[str] = Field(default_factory=list, description="Empty = all operations")
    validate: bool = Field(default=False, description="Generate + source validation in one go")
    skip_passed: bool = False
    include_ingress: bool = False


class CompareRequest(BaseModel):
    operations: list[str]
    sample_source: str = "fresh"
    # Optional: operation → list of enriched JSON paths to validate (Compare attribute editor)
    field_paths_by_op: dict[str, list[str]] | None = None
    # Optional: operation → exact xCorrelationId to pair (Compare-from-Enrich/raw card).
    # Lets us compare the specific event on the card, including ones fired by others.
    correlation_by_op: dict[str, str] | None = None


def _job_payload(job) -> dict[str, Any]:
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status.value if isinstance(job.status, JobStatus) else job.status,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "params": job.params,
        "logs": job.logs[-500:],
        "result": job.result,
        "error": job.error,
    }


@app.get("/health")
def health() -> dict[str, Any]:
    mongo_ok = db.ping()
    return {
        "status": "ok" if mongo_ok else "degraded",
        "mongo": mongo_ok,
        "audit_project_root": str(settings.audit_project_root),
        "self_contained": True,
    }


@app.get("/api/config/ui")
def ui_config() -> dict[str, Any]:
    return {
        "defaultPageSize": settings.default_limit,
        "maxPageSize": settings.max_limit,
        "pageSizeOptions": settings.page_size_options,
        "filterFields": list(FILTER_FIELDS),
    }


@app.get("/api/meta/comparable-operations")
def comparable_operations() -> dict[str, Any]:
    items = db.comparable_operations_detail()

    # Trim to real scenarios: Mongo accumulates 400+ distinct operations (many
    # fired by other teams and never part of our audit coverage). Keep only the
    # operations we actually track — the UI Navigation sheet + resolver/cron
    # routing maps. Touchpoint/owned (UI)/(BE) labels are added back below.
    try:
        from audit_validator.event_categories import is_known_operation

        items = [i for i in items if is_known_operation(str(i.get("operation") or ""))]
    except Exception:
        pass

    seen = {i["operation"] for i in items}

    # Also surface touchpoint variants (activateFamily(global), (list), …) that were
    # staged/validated in a Generate run so they can be re-compared individually.
    try:
        from .comparison_store import list_latest

        from audit_validator.event_categories import resolve_category

        stored = list_latest(settings.audit_project_root)
        base_meta = {i["operation"]: i for i in items}
        for op in stored.get("operations", []):
            if op in seen or "(" not in op:
                continue
            base = op.split("(", 1)[0]
            bm = base_meta.get(base, {})
            items.append(
                {
                    "operation": op,
                    "category": resolve_category(op) or bm.get("category", ""),
                    "environment": bm.get("environment", ""),
                    "service": bm.get("service", ""),
                    "occurred_at": None,
                    "touchpoint": True,
                }
            )
            seen.add(op)
    except Exception:
        pass

    # Owned generate runs with (UI)/(BE) display labels
    try:
        from audit_validator.event_categories import resolve_category
        from audit_validator.generation_tracker import list_owned
        from audit_validator.touchpoint.scenarios import scenario_display_name

        owned = list_owned(project_root=settings.audit_project_root)
        base_meta = {i["operation"]: i for i in items}
        for op, entry in (owned.get("by_operation") or {}).items():
            if not isinstance(entry, dict):
                continue
            kind = str(entry.get("kind") or "graphql").lower()
            touch = entry.get("touchpoint")
            bare = str(op).split("(", 1)[0] if "(" in str(op) else str(op)
            label = scenario_display_name(
                bare,
                touch,
                ui=kind == "ui",
                be=kind != "ui",
            )
            if not label or label in seen:
                continue
            # Only add if base op is comparable in Mongo
            if bare not in seen and bare not in {i["operation"].split("(", 1)[0] for i in items}:
                continue
            bm = base_meta.get(bare, {})
            items.append(
                {
                    "operation": label,
                    "category": resolve_category(bare) or bm.get("category", ""),
                    "environment": bm.get("environment", ""),
                    "service": bm.get("service", ""),
                    "occurred_at": entry.get("generated_at"),
                    "touchpoint": True,
                    "channel": "UI" if kind == "ui" else "BE",
                }
            )
            seen.add(label)
    except Exception:
        pass

    items.sort(key=lambda x: str(x.get("operation") or ""))
    return {
        "operations": [i["operation"] for i in items],
        "items": items,
    }


@app.get("/api/results/latest")
def latest_comparison_results() -> dict[str, Any]:
    """Latest stored comparison per operation (merged rows for the Result view)."""
    from .comparison_store import list_latest

    return list_latest(settings.audit_project_root)


@app.get("/api/results/latest/{operation}")
def latest_comparison_operation(operation: str) -> dict[str, Any]:
    from .comparison_store import get_latest_operation

    item = get_latest_operation(settings.audit_project_root, operation)
    if not item:
        raise HTTPException(404, f"No stored comparison for {operation}")
    return item


@app.delete("/api/results/latest/{operation:path}")
def delete_comparison_operation(operation: str) -> dict[str, Any]:
    """Delete a single operation's stored comparison from the Result view."""
    from .comparison_store import delete_operation_result

    deleted = delete_operation_result(settings.audit_project_root, operation)
    if not deleted:
        raise HTTPException(404, f"No stored comparison for {operation}")
    return {"deleted": operation, "ok": True}


@app.delete("/api/results/latest")
def clear_comparison_results() -> dict[str, Any]:
    """Delete every stored comparison (clears the Result view)."""
    from .comparison_store import clear_all_results

    removed = clear_all_results(settings.audit_project_root)
    return {"removed": removed, "ok": True}


@app.get("/api/meta/filter-values")
def filter_values(tab: str | None = Query(None, description="Collection tab for operation list")) -> dict[str, Any]:
    """Distinct env/service/state values and operations for filter dropdowns."""
    return db.distinct_filter_values(tab)


@app.get("/api/ingestion/status")
def ingestion_status() -> dict[str, Any]:
    """Live status of the RabbitMQ → Mongo ingestion service."""
    return ingestion.status()


@app.post("/api/ingestion/start")
def ingestion_start() -> dict[str, Any]:
    """Start continuously draining the platform subscription queues into Mongo."""
    return ingestion.start()


@app.post("/api/ingestion/stop")
def ingestion_stop() -> dict[str, Any]:
    """Stop the ingestion service."""
    return ingestion.stop()


@app.post("/api/ingestion/purge")
def ingestion_purge() -> dict[str, Any]:
    """Purge remaining backlog from the subscription queues (keep only fresh events)."""
    return ingestion.purge()


@app.post("/api/mongo/prune")
def mongo_prune(max_docs: int | None = Query(None, ge=1)) -> dict[str, Any]:
    """Trim each collection to the latest N docs per operation (defaults to configured retention)."""
    keep = max_docs or settings.retention_max_docs
    removed = db.prune_all(keep)
    return {"kept_per_operation": keep, "removed": removed, "total_removed": sum(removed.values())}


def _mongo_probe() -> dict[str, Any]:
    import time as _t

    start = _t.monotonic()
    ok = db.ping()
    return {
        "id": "mongo",
        "label": "MongoDB (audit store)",
        "category": "infra",
        "url": settings.mongo_db,
        "method": "ping",
        "why": (
            "Stores the latest raw + enriched audit envelopes. Enrich/Raw Collection, "
            "Compare, and Result all read from these collections."
        ),
        "state": "ok" if ok else "blocked",
        "ok": ok,
        "reachable": ok,
        "status_code": None,
        "latency_ms": int((_t.monotonic() - start) * 1000),
        "detail": "Ping succeeded." if ok else "Ping failed — Mongo unreachable.",
        "hint": "" if ok else "Check MONGO_DB_URL / VPN.",
        "response_snippet": "",
        "request": {
            "method": "ping",
            "url": settings.mongo_db,
            "headers": {},
            "params": {},
            "body": None,
        },
    }


@app.get("/api/health/apis")
def health_apis() -> dict[str, Any]:
    """Reachability + workability of every external dependency (Postman-like)."""
    from audit_validator.health_probes import probe_all

    probes = [_mongo_probe()] + probe_all()
    return {"probes": probes, "checked_at": _now_iso()}


@app.post("/api/health/probe/{target}")
def health_probe(target: str) -> dict[str, Any]:
    """Run a single connectivity/workability probe on demand (button click)."""
    if target == "mongo":
        return _mongo_probe()
    from audit_validator.health_probes import probe_one

    try:
        return probe_one(target)
    except KeyError:
        raise HTTPException(400, f"unknown probe target: {target}")


class CustomProbeRequest(BaseModel):
    request: dict[str, Any] = Field(default_factory=dict)


@app.post("/api/health/custom")
def health_custom(req: CustomProbeRequest) -> dict[str, Any]:
    """Execute an edited probe request (Postman-like) from the API Health UI."""
    from audit_validator.health_probes import execute_custom_request

    return execute_custom_request(req.request or {})


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


@app.get("/api/meta/operation-stats")
def operation_stats() -> dict[str, Any]:
    """Funnel: generated (tracked) → in both collections → true raw+enrich pairs.

    Includes operation name lists so the Generate UI can open each funnel CTA
    as a table (tracked / in_both / true_pairs / raw_only / enriched_only).
    """
    stats = db.operation_stats()
    try:
        from audit_validator.operation_registry import tracked_operations

        tracked = list(tracked_operations())
        stats["tracked"] = len(tracked)
        stats["tracked_operations"] = tracked
    except Exception:
        stats["tracked"] = None
        stats["tracked_operations"] = []
    # in_both name list = union ops that appear in both collections
    paired = list(stats.get("paired_operations") or [])
    unpaired = list(stats.get("unpaired") or [])
    stats["in_both_operations"] = sorted(set(paired) | set(unpaired))
    return stats


@app.get("/api/results/failure-summary")
def results_failure_summary() -> dict[str, Any]:
    """Common Compare FAIL patterns with occurrence counts + mongo/curl investigate hints."""
    try:
        from .failure_summary import build_failure_summary

        return build_failure_summary(settings.audit_project_root)
    except Exception as exc:  # noqa: BLE001
        return {"total_fail_rows": 0, "groups": [], "error": str(exc)}


@app.get("/api/meta/ui-navigation")
def ui_navigation() -> dict[str, Any]:
    """UI navigation paths per operation (from docs/UI Navigation of Event.xlsx)."""
    try:
        from audit_validator.curl_builder import load_ui_navigation

        return {"navigation": load_ui_navigation()}
    except Exception as exc:
        return {"navigation": {}, "error": str(exc)}


@app.get("/api/curl/{operation}")
def operation_curl(operation: str) -> dict[str, Any]:
    """Build a copy-pasteable curl for an operation using its latest captured raw event."""
    try:
        from audit_validator.curl_builder import build_curl, ui_navigation_for

        raw, _ = db.latest_pair(operation, require_pair=False)
        result = build_curl(operation, raw).as_dict()
        result["ui_navigation"] = ui_navigation_for(operation)
        result["has_captured_event"] = bool(raw)
        return result
    except Exception as exc:
        return {"operation": operation, "error": str(exc)}


@app.get("/api/generate/payload/{item_id:path}")
def generate_default_payload(item_id: str) -> dict[str, Any]:
    """Return the editable default payload for a generatable event (graphql/ingress/cron)."""
    try:
        from audit_validator.custom_send import default_payload
        from audit_validator.touchpoint.scenarios import parse_selection_id

        raw = None
        if not (item_id.startswith("ingress:") or item_id.startswith("cron:")):
            operation, _touchpoint = parse_selection_id(item_id)
            raw, _ = db.latest_pair(operation or item_id, require_pair=False)
        return default_payload(
            item_id, raw=raw, project_root=settings.audit_project_root
        )
    except Exception as exc:  # noqa: BLE001
        return {"id": item_id, "editable": False, "error": str(exc)}


class SendCustomRequest(BaseModel):
    item_id: str
    payload: Any
    correlation_id: str | None = None


class PayloadCurlRequest(BaseModel):
    item_id: str
    payload: Any
    correlation_id: str | None = None


class PipelineTargetRequest(BaseModel):
    target: str


class PipelineQueuesRequest(BaseModel):
    raw_queue: str | None = None
    enriched_queue: str | None = None
    dlq: str | None = None


@app.post("/api/generate/send-custom")
def generate_send_custom(req: SendCustomRequest) -> dict[str, Any]:
    """Send a (possibly edited) payload to the right transport and return the response."""
    try:
        from audit_validator.custom_send import send_payload

        return send_payload(
            req.item_id,
            req.payload,
            project_root=settings.audit_project_root,
            correlation_id=req.correlation_id,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": str(exc)}


@app.post("/api/generate/payload-curl")
def generate_payload_curl(req: PayloadCurlRequest) -> dict[str, Any]:
    """Build a runnable curl from the edited Edit&Send payload (includes bearer)."""
    try:
        from audit_validator.custom_send import build_payload_curl

        return build_payload_curl(
            req.item_id,
            req.payload,
            correlation_id=req.correlation_id or "",
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": str(exc), "curl": ""}


@app.get("/api/meta/generated-correlations")
def generated_correlations() -> dict[str, Any]:
    """(operation → xCorrelationId) pairs minted by our Generate runs."""
    try:
        from audit_validator.generation_tracker import list_owned

        return list_owned(project_root=settings.audit_project_root)
    except Exception as exc:  # noqa: BLE001
        return {"by_operation": {}, "error": str(exc)}


@app.get("/api/generate/last-run")
def generate_last_run() -> dict[str, Any]:
    """Last Generate run: which ops landed in raw+enrich vs still need work."""
    try:
        from audit_validator.generate_run_report import load_last_generate_run

        report = load_last_generate_run(project_root=settings.audit_project_root)
        if not report:
            return {"ok": False, "detail": "No generate-run report yet — run Generate first."}
        return {"ok": True, "report": report}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": str(exc)}


@app.get("/api/meta/operations")
def list_operations() -> dict[str, Any]:
    try:
        from audit_validator.operation_registry import tracked_operations

        return {"operations": tracked_operations()}
    except Exception as exc:
        return {"operations": [], "error": str(exc)}


@app.get("/api/meta/flows")
def list_flows() -> dict[str, Any]:
    try:
        from audit_validator.simulation.flows import FLOW_REGISTRY

        return {"flows": [name for name, _ in FLOW_REGISTRY]}
    except Exception as exc:
        return {"flows": [], "error": str(exc)}


@app.get("/api/meta/pipeline-config")
def pipeline_config() -> dict[str, Any]:
    try:
        from audit_validator.config import load_config
        from audit_validator.env_profiles import get_audit_profile
        from urllib.parse import quote, urlparse

        cfg = load_config(settings.audit_project_root)
        ingest = ingestion.status()
        profile = get_audit_profile()
        parsed = urlparse(cfg.rabbitmq.url)
        vhost = parsed.path.lstrip("/") or "/"

        def queue_url(queue: str) -> str:
            if not parsed.hostname or not queue:
                return ""
            return (
                f"https://{parsed.hostname}/#/queues/"
                f"{quote(vhost, safe='')}/{quote(queue, safe='')}"
            )

        return {
            "target": __import__("os").getenv("AUDIT_TARGET", "pp"),
            "target_label": profile.label,
            "nextgen_url": profile.nextgen_ui_url,
            "queue_environment": "pp" if profile.rabbitmq_vhost == "mt-connect-preprod" else profile.name,
            "queue_warning": (
                "UAT GraphQL selected; RabbitMQ still uses the configured PP/preprod tap queues "
                "until UAT broker/vhost details are configured."
                if profile.name == "uat"
                else ""
            ),
            "available_targets": [
                {"id": "pp", "label": "PP", "url": "https://nextgen.monotype-pp.com"},
                {"id": "qa", "label": "QA", "url": "https://nextgen-qa.monotype-pp.com"},
                {"id": "uat", "label": "UAT", "url": "https://nextgen.monotype-uat.com"},
            ],
            "graphql_endpoint": __import__("os").getenv("NEXTGEN_GRAPHQL_ENDPOINT", ""),
            "mongo_db": settings.mongo_db,
            "mongo_url_host": (urlparse(settings.mongo_url).hostname or ""),
            "raw_queue": cfg.rabbitmq.raw_queue,
            "raw_queue_url": queue_url(cfg.rabbitmq.raw_queue),
            "enriched_queue": cfg.rabbitmq.enriched_queue,
            "enriched_queue_url": queue_url(cfg.rabbitmq.enriched_queue),
            "dlq": cfg.rabbitmq.dead_letter_queue,
            "dlq_url": queue_url(cfg.rabbitmq.dead_letter_queue),
            "ingestion_running": bool(ingest.get("running")),
            "ingestion_auto_start": __import__("os")
            .getenv("INGEST_AUTO_START", "true")
            .strip()
            .lower()
            in {"1", "true", "yes", "on"},
        }
    except Exception as exc:
        return {"error": str(exc)}


@app.post("/api/meta/pipeline-target")
def set_pipeline_target(req: PipelineTargetRequest) -> dict[str, Any]:
    """Switch the runtime Generate target and rebuild queue consumers + Mongo DB."""
    target = req.target.strip().lower()
    if target not in {"pp", "qa", "uat"}:
        raise HTTPException(status_code=400, detail="target must be pp, qa, or uat")
    try:
        import os
        from dotenv import set_key
        from audit_validator.env_profiles import apply_audit_profile, mongo_db_for_profile

        os.environ["AUDIT_TARGET"] = target
        set_key(str(settings.audit_project_root / ".env"), "AUDIT_TARGET", target)
        profile = apply_audit_profile(project_root=settings.audit_project_root)
        mongo_name = mongo_db_for_profile(profile)
        os.environ["MONGO_DB_NAME"] = mongo_name
        set_key(str(settings.audit_project_root / ".env"), "MONGO_DB_NAME", mongo_name)
        # Keep CasePilot UI base in sync with the selected NextGen host
        os.environ["NEXTGEN_UI_URL"] = profile.nextgen_ui_url
        set_key(str(settings.audit_project_root / ".env"), "NEXTGEN_UI_URL", profile.nextgen_ui_url)
        # Clear sticky CASEPILOT_UI_BASE_URL override so Generate-in-UI follows AUDIT_TARGET
        if (os.getenv("CASEPILOT_UI_BASE_URL") or "").strip():
            os.environ["CASEPILOT_UI_BASE_URL"] = profile.nextgen_ui_url
            set_key(
                str(settings.audit_project_root / ".env"),
                "CASEPILOT_UI_BASE_URL",
                profile.nextgen_ui_url,
            )
        settings.mongo_db = mongo_name
        db.use_database(mongo_name)
        ingestion.reconfigure()
        return pipeline_config()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/meta/pipeline-queues")
def set_pipeline_queues(req: PipelineQueuesRequest) -> dict[str, Any]:
    """Update raw / enriched / DLQ queue names used by ingestion + Generate."""
    updates: dict[str, str] = {}
    if req.raw_queue is not None:
        q = req.raw_queue.strip()
        if not q:
            raise HTTPException(status_code=400, detail="raw_queue cannot be empty")
        updates["RAW_EVENTS_QUEUE"] = q
    if req.enriched_queue is not None:
        q = req.enriched_queue.strip()
        if not q:
            raise HTTPException(status_code=400, detail="enriched_queue cannot be empty")
        updates["ENRICHED_EVENTS_QUEUE"] = q
    if req.dlq is not None:
        q = req.dlq.strip()
        updates["DEAD_LETTER_QUEUE"] = q
    if not updates:
        raise HTTPException(status_code=400, detail="provide raw_queue and/or enriched_queue")
    try:
        import os
        from dotenv import set_key

        env_path = str(settings.audit_project_root / ".env")
        for key, value in updates.items():
            os.environ[key] = value
            set_key(env_path, key, value)
        ingestion.reconfigure()
        return pipeline_config()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/meta/coverage")
def coverage() -> dict[str, Any]:
    """Validation-mapping coverage across all tracked operations."""
    try:
        from audit_validator.coverage import mapping_coverage_report
        from audit_validator.event_categories import resolve_category

        report = mapping_coverage_report()
        for row in report.get("operations", []):
            row["category"] = resolve_category(str(row.get("operation", "")))
        return report
    except Exception as exc:
        return {"total": 0, "summary": {}, "operations": [], "error": str(exc)}


@app.get("/api/meta/categories")
def categories() -> dict[str, Any]:
    """Event categories (in-app notification groups) + operation → category map."""
    try:
        from audit_validator.event_categories import category_report
        from audit_validator.operation_registry import tracked_operations

        return category_report(tracked_operations())
    except Exception as exc:
        return {"categories": [], "by_operation": {}, "counts": {}, "error": str(exc)}


@app.get("/api/meta/operation-sources")
def operation_sources() -> dict[str, Any]:
    """Map each tracked operation to its source kind (graphql / ingress / cron).

    GraphQL items with known UI touchpoints are expanded to
    ``operation::touchpoint`` scenario ids (see FLOW_DEFS).
    """
    try:
        from audit_validator.operation_sources import operation_source_report

        # Bust cache so FLOW_DEFS / scenario edits show up without process restart
        operation_source_report.cache_clear()
        return operation_source_report()
    except Exception as exc:
        return {"by_operation": {}, "counts": {}, "catalog": [], "error": str(exc)}


@app.get("/api/meta/touchpoint-scenarios")
def touchpoint_scenarios() -> dict[str, Any]:
    """List GraphQL generate scenarios (operation × touchpoint × steps)."""
    try:
        from audit_validator.touchpoint.scenarios import list_scenarios

        scenarios = list_scenarios()
        return {"scenarios": scenarios, "count": len(scenarios)}
    except Exception as exc:
        return {"scenarios": [], "count": 0, "error": str(exc)}


@app.get("/api/meta/ui-navigation-mapping.xlsx")
def ui_navigation_mapping_xlsx():
    """Multi-sheet Excel: UI Navigation events ↔ FLOW_DEFS / generate catalog."""
    from fastapi.responses import FileResponse

    try:
        from audit_validator.export_ui_navigation_mapping import write_ui_navigation_mapping

        path = write_ui_navigation_mapping(project_root=settings.audit_project_root)
        return FileResponse(
            path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename="ui-navigation-event-mapping.xlsx",
        )
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.get("/api/token/status")
def token_status() -> dict[str, Any]:
    """Current Bearer-token health (expiry, derived org/gcid, can-regenerate)."""
    try:
        from audit_validator.token_manager import bearer_status, current_oauth_form_defaults

        st = bearer_status(settings.audit_project_root).as_dict()
        st["credentials"] = current_oauth_form_defaults(settings.audit_project_root)
        return st
    except Exception as exc:
        return {"present": False, "error": str(exc)}


@app.post("/api/token/refresh")
def token_refresh() -> dict[str, Any]:
    """Force a Bearer-token refresh (regenerate + persist to .env)."""
    try:
        from audit_validator.token_manager import ensure_fresh_bearer

        return ensure_fresh_bearer(settings.audit_project_root, min_ttl_hours=999).as_dict()
    except Exception as exc:
        return {"present": False, "error": str(exc)}


class TokenCredentialsRequest(BaseModel):
    username: str
    password: str
    org: str = ""
    gcid: str = ""


@app.post("/api/token/credentials")
def token_credentials(body: TokenCredentialsRequest) -> dict[str, Any]:
    """Generate a Bearer from username/password (+ optional org/gcid) and persist."""
    try:
        from audit_validator.token_manager import apply_credentials, current_oauth_form_defaults

        st = apply_credentials(
            settings.audit_project_root,
            username=body.username,
            password=body.password,
            org=body.org,
            gcid=body.gcid,
            persist=True,
        )
        out = st.as_dict()
        out["credentials"] = current_oauth_form_defaults(settings.audit_project_root)
        if not st.present:
            return JSONResponse(out, status_code=400)
        return out
    except Exception as exc:
        return JSONResponse({"present": False, "error": str(exc)}, status_code=500)


@app.post("/api/token/verify")
def token_verify() -> dict[str, Any]:
    """Generate a fresh token and compare its identity to the pasted BEARER_TOKEN."""
    try:
        from audit_validator.token_manager import compare_provided_vs_generated

        return compare_provided_vs_generated(settings.audit_project_root)
    except Exception as exc:
        return {"error": str(exc)}


class UiTriggerSelectionItem(BaseModel):
    id: str = ""
    operation: str
    touchpoint: str | None = None
    label: str = ""
    test_case_id: str = ""
    notes: str = ""


class UiTriggerRequest(BaseModel):
    selection: list[UiTriggerSelectionItem] = Field(default_factory=list)
    test_case_id: str = ""
    cta_text: str = ""
    notes: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)
    dispatch: bool = False
    # Browser mode for CasePilot connector (default headed / visible)
    headless: bool = False


@app.get("/api/meta/casepilot")
def casepilot_status() -> dict[str, Any]:
    """CasePilot MCP reachability (preflight + connector online)."""
    try:
        from audit_validator.ui_trigger import casepilot_health

        return casepilot_health()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/api/meta/ui-testrail-map")
def ui_testrail_map() -> dict[str, Any]:
    """FDC-14091 TestRail case map for Generate-in-UI (C73303503…)."""
    try:
        from audit_validator.ui_testrail_map import public_testrail_map

        return {"ok": True, **public_testrail_map()}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "by_key": {}, "by_label": {}, "cases": []}


@app.post("/api/jobs/generate-ui")
def start_generate_ui(body: UiTriggerRequest) -> dict[str, Any]:
    """Create a Generate-in-UI handoff job; optionally dispatch to CasePilot MCP.

    Correlation for UI triggers must use response header ``correlation-id``
    (Cloudflare rewrites ``x-correlation-id``).
    """
    if not body.selection:
        raise HTTPException(400, "Select at least one scenario / operation")
    try:
        from audit_validator.casepilot_mcp import load_casepilot_config
        from audit_validator.ui_trigger import create_ui_trigger_job

        cfg = load_casepilot_config()
        extra = dict(body.extra or {})
        extra["headless"] = bool(body.headless)
        job = create_ui_trigger_job(
            settings.audit_project_root,
            selection=[s.model_dump() for s in body.selection],
            test_case_id=body.test_case_id,
            cta_text=body.cta_text,
            notes=body.notes,
            extra=extra,
            dispatch=bool(body.dispatch),
        )
        return {
            "ok": True,
            "job": job,
            "mcp_ready": cfg.configured,
            "ui_config_ready": cfg.ui_config_ready(),
        }
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.get("/api/jobs/generate-ui")
def list_generate_ui() -> dict[str, Any]:
    try:
        from audit_validator.ui_trigger import list_ui_trigger_jobs

        jobs = list_ui_trigger_jobs(settings.audit_project_root)
        return {"jobs": jobs, "count": len(jobs)}
    except Exception as exc:
        return {"jobs": [], "count": 0, "error": str(exc)}


@app.get("/api/jobs/generate-ui/{job_id}")
def get_generate_ui(job_id: str) -> dict[str, Any]:
    try:
        from audit_validator.ui_trigger import get_ui_trigger_job

        job = get_ui_trigger_job(settings.audit_project_root, job_id)
        if not job:
            raise HTTPException(404, f"No UI trigger job {job_id}")
        return job
    except HTTPException:
        raise
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/jobs/generate-ui/{job_id}/cancel")
def cancel_generate_ui(job_id: str) -> dict[str, Any]:
    """Close/stop a Generate-in-UI session (stop polling + pending CasePilot retries)."""
    try:
        from audit_validator.ui_trigger import cancel_ui_trigger_job, get_ui_trigger_job

        if not get_ui_trigger_job(settings.audit_project_root, job_id):
            raise HTTPException(404, f"No UI trigger job {job_id}")
        job = cancel_ui_trigger_job(settings.audit_project_root, job_id)
        return {"ok": True, "job": job}
    except HTTPException:
        raise
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.post("/api/jobs/generate-ui/{job_id}/send")
def send_generate_ui(job_id: str) -> dict[str, Any]:
    """Dispatch a saved handoff to CasePilot ``run_testrail_ui_tests``."""
    try:
        from audit_validator.ui_trigger import dispatch_ui_trigger_job, get_ui_trigger_job

        if not get_ui_trigger_job(settings.audit_project_root, job_id):
            raise HTTPException(404, f"No UI trigger job {job_id}")
        job = dispatch_ui_trigger_job(settings.audit_project_root, job_id)
        ok = bool(job) and job.get("status") in {"queued", "running", "completed"}
        return {"ok": ok, "job": job}
    except HTTPException:
        raise
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.post("/api/jobs/generate-ui/{job_id}/refresh")
def refresh_generate_ui(job_id: str) -> dict[str, Any]:
    """Poll CasePilot run status; auto-verify raw/enrich when correlations are ready."""
    try:
        from audit_validator.ui_trigger import (
            finalize_ui_trigger_verification,
            get_ui_trigger_job,
            refresh_casepilot_status,
        )

        if not get_ui_trigger_job(settings.audit_project_root, job_id):
            raise HTTPException(404, f"No UI trigger job {job_id}")
        job = refresh_casepilot_status(settings.audit_project_root, job_id)
        ver = (job or {}).get("verification") or {}
        has_cids = bool(ver.get("ready") or ver.get("auto_verify_pending") or (job or {}).get("results"))
        already = bool(ver.get("generate_run_saved"))
        if job and has_cids and not already and (job.get("results") or []):
            job = finalize_ui_trigger_verification(
                settings.audit_project_root,
                job_id,
                db=db,
            )
        return {"ok": True, "job": job}
    except HTTPException:
        raise
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


class UiTriggerResultsBody(BaseModel):
    results: list[dict[str, Any]] = Field(default_factory=list)


@app.post("/api/jobs/generate-ui/{job_id}/results")
def record_generate_ui_results(job_id: str, body: UiTriggerResultsBody) -> dict[str, Any]:
    """Manually record correlation_id(s); auto-runs raw/enrich verification."""
    try:
        from audit_validator.ui_trigger import (
            finalize_ui_trigger_verification,
            get_ui_trigger_job,
            record_manual_ui_results,
        )

        if not get_ui_trigger_job(settings.audit_project_root, job_id):
            raise HTTPException(404, f"No UI trigger job {job_id}")
        if not body.results:
            raise HTTPException(400, "Provide at least one result with correlation_id")
        job = record_manual_ui_results(settings.audit_project_root, job_id, body.results)
        job = finalize_ui_trigger_verification(
            settings.audit_project_root,
            job_id,
            db=db,
        )
        ready = bool((job or {}).get("verification", {}).get("generate_run_saved"))
        return {"ok": ready, "job": job}
    except HTTPException:
        raise
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.post("/api/jobs/generate-ui/{job_id}/verify")
def verify_generate_ui(job_id: str) -> dict[str, Any]:
    """After UI trigger: poll Mongo raw/enrich by correlation_id and write Generation Status."""
    try:
        from audit_validator.ui_trigger import finalize_ui_trigger_verification, get_ui_trigger_job

        if not get_ui_trigger_job(settings.audit_project_root, job_id):
            raise HTTPException(404, f"No UI trigger job {job_id}")
        job = finalize_ui_trigger_verification(
            settings.audit_project_root,
            job_id,
            db=db,
        )
        ok = bool((job or {}).get("verification", {}).get("generate_run_saved"))
        return {"ok": ok, "job": job}
    except HTTPException:
        raise
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.post("/api/jobs/generate")
def start_generate(body: GenerateRequest) -> dict[str, Any]:
    job = bridge.start_generate(
        operations=body.operations or None,
        validate=body.validate,
        skip_passed=body.skip_passed,
        include_ingress=body.include_ingress,
    )
    return _job_payload(job)


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    """Abort a running Generate / Generate & validate / Compare job."""
    job = bridge.cancel_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {"ok": True, "job": _job_payload(job)}


@app.post("/api/jobs/compare")
def start_compare(body: CompareRequest) -> dict[str, Any]:
    if not body.operations:
        raise HTTPException(400, "Select at least one operation")
    job = bridge.start_compare(
        body.operations,
        body.sample_source,
        field_paths_by_op=body.field_paths_by_op,
        correlation_by_op=body.correlation_by_op,
    )
    return _job_payload(job)


@app.get("/api/meta/enriched-fields/{operation}")
def enriched_fields_for_operation(operation: str) -> dict[str, Any]:
    """List enriched JSON paths from the latest staged sample (Compare attribute editor)."""
    root = settings.audit_project_root
    try:
        from audit_validator.source_validation.config import load_source_validation_config
        from audit_validator.source_validation.enriched_field_scanner import scan_enriched_fields
        from audit_validator.source_validation.runner import _load_enriched_sample

        cfg = load_source_validation_config(root)
        enriched = _load_enriched_sample(cfg, operation, sample_source="fresh")
        if not enriched:
            return {"operation": operation, "fields": [], "detail": "No enriched sample"}
        fields = [p for p, _ in scan_enriched_fields(enriched)]
        return {"operation": operation, "fields": fields, "count": len(fields)}
    except Exception as exc:
        return {"operation": operation, "fields": [], "detail": str(exc)}


@app.get("/api/meta/enrichment-scope/{operation}")
def enrichment_scope_for_operation(operation: str) -> dict[str, Any]:
    """Produce + require enrichment scope from audit-resolver manifest."""
    try:
        from audit_validator.source_validation.enrichment_scope import (
            load_enrichment_scope_manifest,
        )

        man = load_enrichment_scope_manifest()
        spec = man.get(operation) or {}
        return {"operation": operation, **spec}
    except Exception as exc:
        return {"operation": operation, "detail": str(exc)}


@app.get("/api/jobs")
def list_jobs(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    return {"jobs": [_job_payload(j) for j in job_store.list_jobs(limit)]}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return _job_payload(job)


@app.get("/api/preview/pair/{operation}")
def preview_pair(operation: str) -> dict[str, Any]:
    raw, enriched = db.latest_pair(operation)
    return {"operation": operation, "raw": raw, "enriched": enriched}


def _owned_correlation_index() -> dict[str, dict[str, str]]:
    """xCorrelationId → {scenario label, channel} for events *we* generated.

    Lets the Enrich/raw browser label a card as e.g. ``activateFamily(global)(UI)``
    when its correlation matches one we minted. Events fired by other teams (read
    straight off the queue) have no match and stay on their bare operation name —
    still valid to compare, just not tagged as ours.
    """
    try:
        from audit_validator.generation_tracker import list_owned
    except Exception:
        return {}
    try:
        owned = list_owned(project_root=settings.audit_project_root)
    except Exception:
        return {}
    idx: dict[str, dict[str, str]] = {}
    for op, entry in (owned.get("by_operation") or {}).items():
        if not isinstance(entry, dict):
            continue
        channel = "UI" if str(entry.get("kind") or "").lower() == "ui" else "BE"
        base = str(op)
        for legacy in ("(ui)", "(be)", "(UI)", "(BE)"):
            if base.endswith(legacy):
                base = base[: -len(legacy)]
        label = f"{base}({channel})"
        cids = {str(entry.get("xCorrelationId") or "").strip()}
        for hist in entry.get("history") or []:
            cids.add(str((hist or {}).get("xCorrelationId") or "").strip())
        for cid in cids:
            if cid:
                idx.setdefault(cid, {"scenario": label, "channel": channel})
    return idx


def _annotate_scenarios(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tag rows minted by us with their scenario label + UI/BE channel."""
    idx = _owned_correlation_index()
    if not idx:
        return results
    for row in results:
        cid = row.get("xCorrelationId") or row.get("correlationId") or ""
        info = idx.get(cid)
        if info:
            row["scenario"] = info["scenario"]
            row["channel"] = info["channel"]
    return results


# Catch-all MUST be last — otherwise it steals /api/jobs, /api/meta/*, etc.
@app.get("/api/{tab}")
def list_logs(
    tab: str,
    page: int = Query(1, ge=1),
    limit: int | None = None,
    unique: bool = Query(True, description="Latest entry per operation when no filters"),
    xCorrelationId: str = Query("", alias="xCorrelationId"),
    source_operation: str = Query("", alias="source.operation"),
    actor_globalUserId: str = Query("", alias="actor.globalUserId"),
    source_platformEnvironment: str = Query("", alias="source.platformEnvironment"),
    source_service: str = Query("", alias="source.service"),
    source_operationState: str = Query("", alias="source.operationState"),
) -> dict[str, Any]:
    if tab not in {"raw", "enriched", "dlq"}:
        raise HTTPException(400, "tab must be raw, enriched, or dlq")
    requested = limit or settings.default_limit
    if requested not in settings.page_size_options:
        lim = min(requested, settings.max_limit)
    else:
        lim = requested
    query_filters = {
        "xCorrelationId": xCorrelationId,
        "source.operation": source_operation,
        "actor.globalUserId": actor_globalUserId,
        "source.platformEnvironment": source_platformEnvironment,
        "source.service": source_service,
        "source.operationState": source_operationState,
    }
    result = db.find_logs(tab, filters=query_filters, limit=lim, page=page, unique=unique)
    try:
        _annotate_scenarios(result.get("results") or [])
    except Exception:
        pass
    return result
