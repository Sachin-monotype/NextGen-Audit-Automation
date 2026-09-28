"""FastAPI router for Network Interceptor & Live Background Batch Comparison."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

log = logging.getLogger("InterceptorRoutes")

router = APIRouter(prefix="/api/interceptor", tags=["interceptor"])

# Global singleton will be initialized when backend starts
_manager: Any = None


def init_interceptor_manager(project_root: Path, bridge: Any = None, db: Any = None) -> Any:
    global _manager
    from audit_validator.interceptor import InterceptorManager

    _manager = InterceptorManager(project_root=project_root, bridge=bridge, db=db)
    return _manager


def get_manager() -> Any:
    global _manager
    if _manager is None:
        raise HTTPException(500, "Interceptor manager is not initialized")
    return _manager


class StartCaptureBody(BaseModel):
    port: int = 9222
    target: str = "web"  # "web" or "app"
    auto_compare: bool = True
    batch_size: int = 10
    filter_operation: Optional[str] = None
    url_keyword: str = "graph"
    ignore_get: bool = True
    ignore_query: bool = False
    interception_mode: str = "observe"  # "observe" or "pause"


class TriggerBatchBody(BaseModel):
    event_ids: Optional[List[str]] = None


class CaptureFiltersBody(BaseModel):
    ignore_get: Optional[bool] = None
    ignore_query: Optional[bool] = None
    auto_compare: Optional[bool] = None
    batch_size: Optional[int] = None


class LaunchTargetBody(BaseModel):
    port: int = 9222


class PausedRequestControlBody(BaseModel):
    action: str
    url: Optional[str] = None
    method: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    post_data: Optional[str] = None
    response_code: int = 200
    response_body: Any = None


@router.get("/status")
def get_interceptor_status(port: Optional[int] = Query(None)):
    mgr = get_manager()
    return mgr.get_status(port=port)


@router.post("/launch/chrome")
def launch_chrome_endpoint(body: Optional[LaunchTargetBody] = None):
    mgr = get_manager()
    port = body.port if body else 9222
    return mgr.launch_chrome(port=port)


@router.post("/launch/app")
def launch_app_endpoint(body: Optional[LaunchTargetBody] = None):
    mgr = get_manager()
    port = body.port if body else 9222
    return mgr.launch_app(port=port)


@router.post("/start")
def start_capture_endpoint(body: StartCaptureBody):
    mgr = get_manager()
    result = mgr.start_capture(
        port=body.port,
        target=body.target,
        filter_operation=body.filter_operation,
        url_keyword=body.url_keyword,
        ignore_get=body.ignore_get,
        ignore_query=body.ignore_query,
        auto_compare=body.auto_compare,
        batch_size=body.batch_size,
        interception_mode=body.interception_mode,
    )
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "Failed to start capture"))
    return result


@router.post("/filters")
def update_capture_filters_endpoint(body: CaptureFiltersBody):
    """Update Ignore Get* / Mutations only (and related) while capture is running."""
    mgr = get_manager()
    return mgr.update_capture_filters(
        ignore_get=body.ignore_get,
        ignore_query=body.ignore_query,
        auto_compare=body.auto_compare,
        batch_size=body.batch_size,
    )


@router.post("/stop")
def stop_capture_endpoint():
    mgr = get_manager()
    return mgr.stop_capture()


@router.post("/clear")
def clear_events_endpoint():
    mgr = get_manager()
    return mgr.clear()


@router.get("/events")
def get_events_endpoint(
    operation: str = Query("", description="Filter by operation name"),
    scenario: str = Query("", description="Filter by scenario"),
    limit: int = Query(500, ge=1, le=1000),
):
    mgr = get_manager()
    return mgr.get_events(operation=operation, scenario=scenario, limit=limit)


@router.get("/paused")
def get_paused_requests_endpoint():
    return get_manager().get_paused_requests()


@router.post("/paused/{fetch_id}/control")
def control_paused_request_endpoint(fetch_id: str, body: PausedRequestControlBody):
    if body.action not in {"continue", "abort", "mock"}:
        raise HTTPException(400, "action must be continue, abort, or mock")
    result = get_manager().control_paused(fetch_id, body.action, **body.model_dump(exclude={"action"}))
    if not result.get("ok"):
        raise HTTPException(409, result.get("error", "Unable to control paused request"))
    return result


@router.post("/trigger-batch")
def trigger_batch_endpoint(body: Optional[TriggerBatchBody] = None):
    mgr = get_manager()
    event_ids = body.event_ids if body else None
    result = mgr.trigger_batch(event_ids=event_ids)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "Failed to trigger batch"))
    return result


@router.get("/export-excel")
def export_excel_endpoint():
    mgr = get_manager()
    try:
        out_path = mgr.project_root / "reports" / "audit_results.xlsx"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        excel_path = mgr.export_excel(output_path=out_path)
        return FileResponse(
            path=excel_path,
            filename="audit_results.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as exc:
        raise HTTPException(500, f"Failed to export Excel: {exc}")
