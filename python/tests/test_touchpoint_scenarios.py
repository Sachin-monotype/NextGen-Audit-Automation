"""Unit tests for touchpoint input contracts (no network)."""

from __future__ import annotations

from audit_validator.touchpoint.assertions import (
    assert_raw_input_matches_touchpoint,
    normalize_touchpoint,
)
from audit_validator.touchpoint.payloads import FLOW_DEFS, SeedIds, variables_for
from audit_validator.touchpoint.scenarios import expand_selection_to_scenarios, list_scenarios


def test_normalize_touchpoint_aliases():
    assert normalize_touchpoint("Search/ Family / Discovery") == "discovery"
    assert normalize_touchpoint("Discovery/Browse (global)") == "discovery"
    assert normalize_touchpoint("List (FONTLIST)") == "list"
    assert normalize_touchpoint("Favourite") == "favourite"
    assert normalize_touchpoint("Project") == "project"
    assert normalize_touchpoint("Project > List") == "project_list"


def test_activate_family_input_shapes():
    seed = SeedIds(
        family_id="910130168",
        style_id="1",
        md5="x",
        list_id="list-uuid",
        project_id="proj-uuid",
    )
    for touch in FLOW_DEFS["activateFamily"]:
        inp = variables_for("activateFamily", seed, touch=touch)["input"]
        errs = assert_raw_input_matches_touchpoint("activateFamily", touch, inp)
        assert not errs, (touch, errs, inp)


def test_add_font_list_families_uses_font_list_id():
    seed = SeedIds(family_id="1", style_id="2", md5="x", list_id="abc")
    vars_ = variables_for("addFontListFamilies", seed, touch="List (FONTLIST)")
    assert "listId" not in (vars_.get("input") or {})
    assert vars_["input"]["fontListId"] == "abc"
    assert "styleFilterInput" in vars_


def test_create_list_under_project_sets_parent_id():
    seed = SeedIds(
        family_id="1", style_id="2", md5="x", project_id="proj-1", list_name="L"
    )
    vars_ = variables_for("createAsset", seed, touch="Project > List")
    assert vars_["input"]["parentId"] == "proj-1"
    assert "accessRight" not in vars_["input"]


def test_scenario_catalog_covers_activate_family_paths():
    ids = {s["id"] for s in list_scenarios() if s["operation"] == "activateFamily"}
    assert "activateFamily::List (FONTLIST)" in ids
    assert "activateFamily::Favourite" in ids
    assert "activateFamily::Search/ Family / Discovery" in ids


def test_expand_bare_op_expands_all_touchpoints():
    chosen = expand_selection_to_scenarios(["activateFamily"])
    assert len(chosen) >= 5
    assert all(c["operation"] == "activateFamily" for c in chosen)
