"""Unit tests for QA Results Mongo document shaping (no network)."""

from __future__ import annotations

from app.qa_results_store import _doc_from_item


def test_doc_uses_scenario_as_unique_key():
    item = {
        "operation": "activateVariation(global)(app)",
        "compared_at": "2026-08-07T12:00:00Z",
        "job_id": "j1",
        "job_kind": "compare",
        "summary": {"passed": 10, "failed": 0, "skipped": 1, "na": 0},
        "rows": [
            {
                "field_path": "source.platformEnvironment",
                "value_in_enriched": "app",
                "value_in_source": "app",
                "match_status": "PASS",
            }
        ],
    }
    doc = _doc_from_item("activateVariation(global)(app)", item)
    assert doc["scenario"] == "activateVariation(global)(app)"
    assert doc["audit_target"] == "qa"
    assert doc["row_count"] == 1
    assert doc["summary"]["passed"] == 10
    assert doc["platformEnvironment"] == "app"
    assert "updated_at" in doc
