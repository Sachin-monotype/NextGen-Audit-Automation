"""API routes for Notification Test Guide (catalog + cycles + Excel export)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .config import load_settings
from . import notification_test_store as store

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


def _root():
    return load_settings().audit_project_root


class CreateCycleBody(BaseModel):
    env: str = "uat"
    name: str = Field(..., min_length=1)
    seed_from_catalog: bool = True


class UpsertResultBody(BaseModel):
    env: str = "uat"
    cycle_id: str
    item_id: str
    status: Optional[str] = None
    actual: Optional[str] = None
    comments: Optional[str] = None
    how_to_notes: Optional[str] = None
    tester: Optional[str] = None


@router.get("/catalog")
def get_catalog() -> dict[str, Any]:
    return store.load_catalog(_root())


@router.get("/cycles")
def get_cycles(env: Optional[str] = Query(None)) -> dict[str, Any]:
    return {"cycles": store.list_cycles(_root(), env=env)}


@router.get("/view")
def get_view(
    env: str = Query("uat"),
    cycle_id: Optional[str] = Query(None),
) -> dict[str, Any]:
    return store.merged_view(_root(), env=env, cycle_id=cycle_id)


@router.post("/cycles")
def post_cycle(body: CreateCycleBody) -> dict[str, Any]:
    doc = store.create_cycle(
        _root(),
        env=body.env,
        name=body.name,
        seed_from_catalog=body.seed_from_catalog,
    )
    return {"ok": True, "cycle": doc}


@router.patch("/results")
def patch_result(body: UpsertResultBody) -> dict[str, Any]:
    if not body.cycle_id or not body.item_id:
        raise HTTPException(400, "cycle_id and item_id are required")
    patch: dict[str, Any] = {}
    for key in ("status", "actual", "comments", "how_to_notes", "tester"):
        val = getattr(body, key)
        if val is not None:
            patch[key] = val
    doc = store.upsert_result(
        _root(),
        env=body.env,
        cycle_id=body.cycle_id,
        item_id=body.item_id,
        patch=patch,
    )
    return {"ok": True, "cycle": {"id": doc.get("id"), "updated_at": doc.get("updated_at")}}


@router.get("/export-excel")
def export_excel(
    env: str = Query("uat"),
    cycle_id: str = Query(...),
) -> Response:
    if not cycle_id:
        raise HTTPException(400, "cycle_id is required")
    if store.load_cycle(_root(), env, cycle_id) is None:
        raise HTTPException(404, f"Cycle not found: {cycle_id}")
    raw = store.export_excel_bytes(_root(), env=env, cycle_id=cycle_id)
    filename = f"notification-tests-{env}-{cycle_id}.xlsx"
    return Response(
        content=raw,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
