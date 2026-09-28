"""Tests for Network Interceptor engine, scenario detection, batch companion, and manager."""

from pathlib import Path
from audit_validator.interceptor import (
    CapturedEvent,
    HeaderValues,
    InterceptorManager,
    create_batch_companion_event,
    detect_scenario,
    group_events,
)


def test_detect_scenario():
    # 1. Favorite scenario
    assert detect_scenario("activateFamily", {"input": {"listType": "FAVORITE"}}) == "favourite"

    # 2. List scenario
    assert detect_scenario("activateFamily", {"input": {"listType": "FONTLIST", "listId": "list-123"}}) == "list"

    # 3. Project scenario
    assert detect_scenario("activateFamily", {"input": {"listType": "FONTPROJECT", "projectId": "proj-456"}}) == "project"

    # 4. Project List scenario
    assert (
        detect_scenario("activateFamily", {"input": {"listType": "FONTLIST", "projectId": "proj-456", "listId": "list-1"}})
        == "project_list"
    )

    # 5. Global fallback
    assert detect_scenario("activateFamily", {}) == "global"


def test_batch_companion_event():
    event = CapturedEvent(
        operation_name="bulkActivateStyles",
        scenario="list",
        status_code=200,
        header_values=HeaderValues(correlation_id="test-cid-12345"),
    )
    companion = create_batch_companion_event(event)
    assert companion is not None
    assert companion.operation_name == "bulkActivateComplete"
    assert companion.scenario == "list"
    assert companion.header_values.correlation_id == "test-cid-12345"


def test_group_events_deduplication():
    events = [
        {"operation_name": "activateFamily", "scenario": "list", "id": "1"},
        {"operation_name": "activateFamily", "scenario": "list", "id": "2"},
        {"operation_name": "deactivateFamily", "scenario": "global", "id": "3"},
    ]
    grouped = group_events(events)
    assert len(grouped) == 2
    act = next(g for g in grouped if g["operation_name"] == "activateFamily")
    assert act["call_count"] == 2


def test_interceptor_manager_deduplication_and_queue():
    root = Path(__file__).resolve().parents[2]
    manager = InterceptorManager(project_root=root, bridge=None, db=None)
    manager.auto_compare_enabled = True
    manager.batch_size = 2

    # Simulate 1st event arrival with valid UUID correlation_id
    valid_uuid = "12345678-1234-5678-1234-567812345678"
    e1 = CapturedEvent(
        operation_name="activateFamily",
        scenario="list",
        header_values=HeaderValues(correlation_id=valid_uuid),
    )
    manager._on_intercepted_event(e1)

    assert len(manager.events) == 1
    assert len(manager.pending_queue) == 1
    assert e1.compare_status == "queued"
    assert "activateFamily::list" in manager.queued_pair_keys

    # Simulate duplicate 2nd event with same operation + scenario
    e2 = CapturedEvent(
        operation_name="activateFamily",
        scenario="list",
        header_values=HeaderValues(correlation_id=valid_uuid),
    )
    # Put key into compared_pair_keys to verify "only once" rule
    manager.compared_pair_keys.add("activateFamily::list")
    manager.queued_pair_keys.discard("activateFamily::list")
    manager._on_intercepted_event(e2)

    # The 2nd duplicate event should be marked already_compared
    assert e2.compare_status == "already_compared"

    # Status summary
    status = manager.get_status()
    assert status["captured_count"] >= 1
    assert status["compared_count"] == 1

    # Clear
    manager.clear()
    assert len(manager.events) == 0
    assert len(manager.compared_pair_keys) == 0


def test_interceptor_api_routes():
    import sys
    sys.path.insert(0, "backend")
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    # Test GET status
    resp = client.get("/api/interceptor/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "cdp_ready" in data
    assert "is_active" in data
    assert "batch_size" in data

    # Test GET events
    resp_evts = client.get("/api/interceptor/events")
    assert resp_evts.status_code == 200
    assert "events" in resp_evts.json()

    # Pending controls are available even when no request is currently paused.
    resp_paused = client.get("/api/interceptor/paused")
    assert resp_paused.status_code == 200
    assert resp_paused.json()["total"] == 0

    invalid_action = client.post(
        "/api/interceptor/paused/missing/control",
        json={"action": "rewrite"},
    )
    assert invalid_action.status_code == 400

    # Test Clear
    resp_clear = client.post("/api/interceptor/clear")
    assert resp_clear.status_code == 200

