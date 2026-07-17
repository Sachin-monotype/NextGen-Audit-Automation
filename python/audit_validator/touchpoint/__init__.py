"""TouchPoint builders — schema-correct GraphQL variables + module taxonomy."""

from audit_validator.touchpoint.modules import MODULES, OP_MODULE, module_for
from audit_validator.touchpoint.payloads import (
    FLOW_DEFS,
    SeedIds,
    assert_add_font_list_families_shape,
    variables_for,
)
from audit_validator.touchpoint.assertions import (
    assert_raw_input_matches_touchpoint,
    expected_activate_family_input_keys,
    normalize_touchpoint,
)
from audit_validator.touchpoint.scenarios import (
    expand_selection_to_scenarios,
    list_scenarios,
    parse_selection_id,
    scenario_id,
)

__all__ = [
    "MODULES",
    "OP_MODULE",
    "module_for",
    "FLOW_DEFS",
    "SeedIds",
    "assert_add_font_list_families_shape",
    "variables_for",
    "assert_raw_input_matches_touchpoint",
    "expected_activate_family_input_keys",
    "normalize_touchpoint",
    "expand_selection_to_scenarios",
    "list_scenarios",
    "parse_selection_id",
    "scenario_id",
]
