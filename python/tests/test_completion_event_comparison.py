from audit_validator.source_validation.comparison_rows import build_comparison_rows


def test_completion_event_only_compares_enriched_snapshots() -> None:
    enriched = {
        "eventId": "event-1",
        "source": {"operation": "byofFontDeleteComplete"},
        "subject": {
            "enrichedSnapshot": {"asset": {"id": "asset-1"}},
            "metadata": {"result": {"success": True}},
        },
    }

    rows = build_comparison_rows("byofFontDeleteComplete", enriched)

    non_snapshot = [row for row in rows if "enrichedSnapshot" not in row.field_path]
    assert non_snapshot
    assert all(row.match_status == "N/A" for row in non_snapshot)
    assert all("not applicable" in row.notes.lower() for row in non_snapshot)