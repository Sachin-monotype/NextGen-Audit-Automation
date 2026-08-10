"""Plugin subject field scanning + host-app scenario labels."""

from __future__ import annotations

from audit_validator.plugin_types import normalize_plugin_type, resolve_plugin_scenario
from audit_validator.source_validation.enriched_field_scanner import (
    infer_source_system,
    scan_enriched_fields,
)
from audit_validator.source_validation.mapping_registry import get_operation_mapping
from audit_validator.touchpoint.scenarios import scenario_display_name


def test_normalize_plugin_types():
    assert normalize_plugin_type("KEYNOTES") == "keynotes"
    assert normalize_plugin_type("Keynote") == "keynotes"
    assert normalize_plugin_type("PAGES") == "pages"
    assert normalize_plugin_type("After Effects") == "aftereffects"
    assert normalize_plugin_type("photoshop") == "photoshop"
    assert normalize_plugin_type("plugin") == ""
    assert normalize_plugin_type("PANEL_ELECT_SBWIND") == "panel_elect_sbwind"


def test_resolve_plugin_scenario_prefers_subject():
    assert (
        resolve_plugin_scenario(
            excel_scenario="plugin",
            pluginsource="",
            subject={"plugin": "KEYNOTES"},
        )
        == "keynotes"
    )
    assert (
        resolve_plugin_scenario(
            excel_scenario="plugin",
            pluginsource="Illustrator",
            subject={"plugin": "KEYNOTES"},
        )
        == "illustrator"
    )


def test_scenario_display_name_plugin_host():
    assert (
        scenario_display_name("pluginMissingFontUnresolved", "plugin", plugin_type="KEYNOTES")
        == "pluginMissingFontUnresolved(keynotes)"
    )
    assert (
        scenario_display_name("pluginMissingFontUnresolved", "keynotes")
        == "pluginMissingFontUnresolved(keynotes)"
    )
    # Plugin type wins over (app) channel.
    assert (
        scenario_display_name(
            "pluginMissingFontUnresolved",
            "plugin",
            target="app",
            plugin_type="pages",
        )
        == "pluginMissingFontUnresolved(pages)"
    )


def test_scan_plugin_subject_fields_and_all_ids():
    ids = [f"id-{i}" for i in range(14)]
    enriched = {
        "source": {"operation": "pluginMissingFontUnresolved", "platformEnvironment": "plugin"},
        "subject": {
            "type": "fontVariation",
            "id": ids,
            "plugin": "KEYNOTES",
            "documentId": "DOC-1",
            "documentName": "Notes.key",
            "styleIds": ["s0", "s1"],
        },
        "actor": {"authenticationState": "authenticated"},
    }
    paths = {p for p, _ in scan_enriched_fields(enriched)}
    assert "subject.plugin" in paths
    assert "subject.documentId" in paths
    assert "subject.documentName" in paths
    assert "subject.type" in paths
    for i in range(14):
        assert f"subject.id[{i}]" in paths
    assert "subject.styleIds[0]" in paths
    assert "subject.styleIds[1]" in paths


def test_infer_plugin_subject_source_system():
    sys, api = infer_source_system("subject.plugin", "pluginMissingFontUnresolved")
    assert sys == "Trigger"
    assert "plugin" in api.lower()


def test_plugin_operation_mapping_includes_plugin_leaves():
    rows = get_operation_mapping("pluginMissingFontUnresolved")
    paths = {r.enriched_path for r in rows}
    assert "subject.plugin" in paths
    assert "subject.documentId" in paths
    assert "subject.documentName" in paths
