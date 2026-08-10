from pathlib import Path
from app.failure_summary import build_failure_summary

def test_build_failure_summary_qa():
    project_root = Path(".").resolve()
    summary = build_failure_summary(project_root, target="qa")
    assert "total_fail_rows" in summary
    assert "distinct_patterns" in summary
    assert "operations_with_fails" in summary
    assert "groups" in summary
    assert summary["audit_target"] == "qa"
    assert isinstance(summary["groups"], list)

def test_build_failure_summary_pp():
    project_root = Path(".").resolve()
    summary = build_failure_summary(project_root, target="pp")
    assert "total_fail_rows" in summary
    assert summary["audit_target"] == "pp"
