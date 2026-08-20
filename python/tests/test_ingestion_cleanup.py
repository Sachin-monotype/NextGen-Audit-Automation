"""Ingestion cleanup must not delete docs inside the retention window."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from audit_validator.ingestion.repository import MongoWriter


def test_cleanup_keeps_recent_docs_even_over_cap():
    now = datetime.now(timezone.utc)
    recent = [
        {"_id": f"r{i}", "occurredAt": (now - timedelta(minutes=i)).isoformat()}
        for i in range(40)
    ]
    older = [
        {"_id": f"o{i}", "occurredAt": (now - timedelta(hours=5, minutes=i)).isoformat()}
        for i in range(35)
    ]

    col = MagicMock()
    col.aggregate.return_value = [{"_id": "getBatchProgress", "docs": recent + older}]
    col.delete_many.return_value = SimpleNamespace(deleted_count=5)

    writer = MongoWriter.__new__(MongoWriter)
    writer.collection = lambda _name: col  # type: ignore[method-assign]

    deleted = writer.cleanup_collection("raw", max_retain=30, keep_hours=3)
    assert deleted == 5
    ids = col.delete_many.call_args[0][0]["_id"]["$in"]
    assert ids == [f"o{i}" for i in range(30, 35)]
    assert not any(i.startswith("r") for i in ids)


def test_cleanup_without_keep_hours_still_caps_per_operation():
    now = datetime.now(timezone.utc)
    docs = [
        {"_id": f"d{i}", "occurredAt": (now - timedelta(hours=8, minutes=i)).isoformat()}
        for i in range(35)
    ]
    col = MagicMock()
    col.aggregate.return_value = [{"_id": "activateFamily", "docs": docs}]
    col.delete_many.return_value = SimpleNamespace(deleted_count=5)

    writer = MongoWriter.__new__(MongoWriter)
    writer.collection = lambda _name: col  # type: ignore[method-assign]

    writer.cleanup_collection("enriched", max_retain=30, keep_hours=0)
    ids = col.delete_many.call_args[0][0]["_id"]["$in"]
    assert ids == [f"d{i}" for i in range(30, 35)]
