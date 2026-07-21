"""CasePilot / TestRail UI steps — locator-first click paths.

Pattern matches TestRail C73303503: login → try action → navigate with
``[data-qa-id='…']`` / ``[data-testid='…']`` → click → AUDIT_RESULT.

No family-id hardcoding. No recipe/source fluff. Locators from
MTConnectAutomation + mtconnect-ui.
"""

from __future__ import annotations

from typing import Any


def short_touch(touch: str) -> str:
    t = (touch or "").lower().replace("/", " ").replace(">", " ").replace("_", " ")
    t = " ".join(t.split())
    if "project" in t and "list" in t:
        return "project_list"
    if "favourite" in t or "favorite" in t:
        return "favourite"
    if "user" in t and "access" in t:
        return "user_access"
    if "account" in t or "workspace" in t:
        return "account"
    if "prefer" in t:
        return "preferences"
    if "notif" in t:
        return "notifications"
    if "tag" in t and "manage" in t:
        return "manage_tags"
    if "mylibrary" in t or ("library" in t and "asset" in t):
        return "library_assets"
    if "project" in t and "list" not in t:
        return "project"
    if "list" in t or "fontlist" in t:
        return "list"
    if "discover" in t or "browse" in t or "search" in t or "global" in t or not t:
        return "global"
    return t.replace(" ", "_") or "global"


def audit_emit(op: str, touch_short: str) -> str:
    return (
        f"Network filter operationName={op} → copy response header correlation-id "
        f"(NOT x-correlation-id) → emit exactly: "
        f"AUDIT_RESULT|operation={op}|correlation_id=<real-uuid>|touchpoint={touch_short}"
    )


def audit_expected(op: str) -> str:
    return (
        f"GraphQL {op} fires; response has correlation-id; "
        "AUDIT_RESULT emitted with real UUID; raw+enrich visible in Generation Status."
    )


def _row(step: str, expected: str = "") -> dict[str, str]:
    return {"step": step, "expected": expected}


LOGIN = (
    "LOGIN (skip if already signed in): open /search → click [data-qa-id='sign-in-button'] → "
    "Auth0 #username → button[data-action-button-primary='true'] → #password → "
    "button[data-action-button-primary='true'] → if /auth/workspace-switch pick company "
    "button[aria-label] → wait [data-qa-id='expandable-searchbar__wrapper']. "
    "Dismiss snackbars ([data-qa-id='snackbar-success'] close)."
)

SEARCH_NAV = (
    "Click [data-testid='menu-item-Search'] (or [data-qa-id='menu-item-Search']) → URL /search → "
    "type short query e.g. hel in [data-qa-id='expandable-searchbar_input'] → Enter "
    "(or click [data-qa-id='expandable-searchbar__search-button']) → "
    "wait [data-qa-id='font-name'] + [data-qa-id='toggle-btn'] on cards."
)

FAV_NAV = (
    "Click [data-qa-id='sidebar-my-library-favourites'] "
    "(fallback #menu-item-tooltip-favorites) → URL /library/favourites/fonts → "
    "wait [data-qa-id='toggle-btn']."
)

LIB_NAV = (
    "Open My Library Show all assets → URL /library "
    "(sidebar [data-testid='sidebar-my-library-show-all'])."
)

MANAGE_NAV = (
    "Click [data-testid='menu-item-Manage'] → wait Manage submenu visible."
)

TEAMS_USERS_NAV = (
    "Click [data-testid='menu-item-Users & teams'] → URL /manage/users-and-teams/users."
)

ROLES_TAB_NAV = (
    "Click Roles tab: md-tabs[data-qa-id='teams-users-tabs'] md-link[id='tab-2'] → "
    "URL /manage/users-and-teams/roles → wait [data-qa-id='add-role-button']."
)

TAGS_NAV = (
    "Click [data-testid='menu-item-Tags'] → URL /manage/tags/list → "
    "wait [data-qa-id='create-tag-button']."
)


def _S(*rows: dict[str, str], op: str, touch: str) -> list[dict[str, str]]:
    out = []
    for r in rows:
        out.append(
            {
                "op": op,
                "touchpoint": touch,
                "step": r["step"],
                "expected": r.get("expected") or "",
            }
        )
    return out


def _ensure_off() -> str:
    return (
        "On the target card: if [data-qa-id='toggle-btn'] looks ON, click once to deactivate; "
        "wait [data-testid='deactivation-toast-wrapper'] or [data-qa-id='snackbar-info-grey']; "
        "dismiss. Target must be OFF before activate."
    )


def _ensure_on() -> str:
    return (
        "On the target card: if [data-qa-id='toggle-btn'] looks OFF, click once to activate; "
        "wait [data-qa-id='snackbar-success']; dismiss. Target must be ON before deactivate."
    )


def _open_list_steps() -> list[dict[str, str]]:
    return [
        _row(LIB_NAV),
        _row(
            "If a FontList exists: click [data-qa-id^='asset-name-link-'] for that list "
            "(URL /library/FontList/{id}). Else: click [data-qa-id='create-list-button'] → "
            "[data-qa-id='asset-name-input'] → [data-qa-id='drawer-primary-button']."
        ),
        _row(
            "If list empty: "
            + SEARCH_NAV
            + " Card kebab [data-qa-id='search-card-options-trigger'] → "
            "[data-qa-id='context-menu-item-add-to-list-or-tag'] → "
            "click [data-qa-id^='add-to-list-or-tag-drawer-add-'] → reopen list."
        ),
    ]


def _open_project_steps() -> list[dict[str, str]]:
    return [
        _row(
            "Open a recent project [data-qa-id^='sidebar-recent-project-'] OR "
            "Show all projects → pick one with fonts. Prefer reuse."
        ),
        _row(
            "If none: click [data-testid='sidebar-add-project-button'] → /projects/create → "
            "name → [data-qa-id='project-creation-desktop-next'] through wizard → finish. "
            "Land /projects/library/{projectId}/fonts."
        ),
        _row(
            "On project fonts: wait [data-qa-id='toggle-btn'] "
            "(nav [data-qa-id='project-nav-project-fonts'] if needed)."
        ),
    ]


def _open_project_list_steps() -> list[dict[str, str]]:
    return [
        *_open_project_steps(),
        _row(
            "Open Project Library All assets → /projects/library/{projectId}. "
            "If project FontList exists open it. Else [data-qa-id='create-list-button'] → "
            "[data-qa-id='asset-name-input'] → [data-qa-id='drawer-primary-button']."
        ),
        _row(
            "Add a family into that project list: on project fonts card kebab → "
            "[data-qa-id='context-menu-item-add-to-list-or-tag'] → "
            "[data-qa-id^='add-to-list-or-tag-drawer-add-']."
        ),
        _row(
            "Open the project FontList grid (URL under /projects/library/{projectId}/… — "
            "NOT global /library). Stay here for the click."
        ),
    ]


def recipe_for(op: str, touch: str, *, label: str = "") -> list[dict[str, str]]:
    op = (op or "").strip()
    touch = (touch or "").strip()
    ts = short_touch(touch)
    touch_canon = touch or {
        "global": "Discovery/Browse (global)",
        "list": "List (FONTLIST)",
        "favourite": "Favourite",
        "project": "Project",
        "project_list": "Project > List",
    }.get(ts, touch)
    label = label or f"{op}({ts})"

    # ── activateFamily global (C73303503 pattern) ───────────────────
    if op == "activateFamily" and ts == "global":
        return _S(
            _row(LOGIN),
            _row(
                "Click the first [data-qa-id='toggle-btn'] on a Search card to Activate. "
                "Wait [data-qa-id='snackbar-success'].",
                "If done then go to AUDIT_RESULT step directly; if not follow next steps.",
            ),
            _row(SEARCH_NAV),
            _row(
                "Stay on Search font-family cards. Do NOT open /family/… detail. "
                "Do NOT open Quick View ([data-qa-id='font preview text'])."
            ),
            _row(_ensure_off()),
            _row(
                "Click [data-qa-id='toggle-btn'] on that card to Activate. "
                "Wait [data-qa-id='snackbar-success']."
            ),
            _row(audit_emit(op, "global"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )

    if op == "activateFamily" and ts == "favourite":
        return _S(
            _row(LOGIN),
            _row(
                "On /library/favourites/fonts click first [data-qa-id='toggle-btn'] to Activate. "
                "Wait [data-qa-id='snackbar-success'].",
                "If done go to AUDIT_RESULT; else continue.",
            ),
            _row(
                SEARCH_NAV
                + " If needed click [data-qa-id='icon-favorite'] (outline) OR kebab → "
                "[data-qa-id='context-menu-item-add-to-favourites']."
            ),
            _row(FAV_NAV),
            _row("Do NOT open /family/… detail."),
            _row(_ensure_off()),
            _row("Click [data-qa-id='toggle-btn'] to Activate. Wait [data-qa-id='snackbar-success']."),
            _row(audit_emit(op, "favourite"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )

    if op == "activateFamily" and ts == "list":
        return _S(
            _row(LOGIN),
            *_open_list_steps(),
            _row("Do NOT open /family/… detail. Do NOT use kebab Activate all fonts."),
            _row(_ensure_off()),
            _row(
                "Click family [data-qa-id='toggle-btn'] to Activate. "
                "Wait [data-qa-id='snackbar-success']."
            ),
            _row(audit_emit(op, "list"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )

    if op == "activateFamily" and ts == "project":
        return _S(
            _row(LOGIN),
            *_open_project_steps(),
            _row("Stay on project fonts grid — do NOT use global Search for this click."),
            _row(_ensure_off()),
            _row(
                "Click [data-qa-id='toggle-btn'] to Activate "
                "(or [data-qa-id='activate-all-button'] if only one family). "
                "Wait [data-qa-id='snackbar-success']."
            ),
            _row(audit_emit(op, "project"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )

    if op == "activateFamily" and ts == "project_list":
        return _S(
            _row(LOGIN),
            *_open_project_list_steps(),
            _row("On project list grid only — do NOT use global Search toggle."),
            _row(_ensure_off()),
            _row("Click family [data-qa-id='toggle-btn'] to Activate. Wait [data-qa-id='snackbar-success']."),
            _row(audit_emit(op, "project_list"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )

    if op == "deactivateFamilies":
        prep = {
            "global": [_row(SEARCH_NAV), _row("Stay on Search cards. Do NOT open /family/….")],
            "favourite": [_row(FAV_NAV)],
            "list": _open_list_steps(),
            "project": _open_project_steps(),
            "project_list": _open_project_list_steps(),
        }.get(ts, [_row(SEARCH_NAV)])
        return _S(
            _row(LOGIN),
            *prep,
            _row(_ensure_on()),
            _row(
                "Click [data-qa-id='toggle-btn'] to Deactivate. "
                "Wait [data-testid='deactivation-toast-wrapper']."
            ),
            _row(audit_emit(op, ts), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )

    if op in {"activateStyle", "deactivateStyle"}:
        act = "Activate" if op == "activateStyle" else "Deactivate"
        need = "OFF" if op == "activateStyle" else "ON"
        prep = {
            "global": [_row(SEARCH_NAV)],
            "favourite": [
                _row(
                    SEARCH_NAV
                    + " If needed click [data-qa-id='icon-favorite'] (outline) OR kebab → "
                    "[data-qa-id='context-menu-item-add-to-favourites']."
                ),
                _row(FAV_NAV),
            ],
            "list": _open_list_steps(),
            "project": _open_project_steps(),
            "project_list": _open_project_list_steps(),
        }.get(ts, [_row(SEARCH_NAV)])
        return _S(
            _row(LOGIN),
            *prep,
            _row(
                "Open ONE style control: (A) click [data-qa-id='font preview text'] → QV → "
                "[data-qa-id='toggle-btn'] on a style row under [data-qa-id='fontFamilies Cards']; "
                "OR (B) [data-qa-id='search-card-options-trigger'] → "
                "[data-qa-id='context-menu-item-activate-styles'] → pick one style; "
                "OR (C) /family/… Styles tab → [data-qa-id*='family-style-card-'] toggle."
            ),
            _row(f"Ensure chosen style is {need}, then {act} that ONE style."),
            _row(audit_emit(op, ts), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )

    if op in {"activateVariation", "deactivateVariation"}:
        act = "Activate" if op == "activateVariation" else "Deactivate"
        prep = {
            "global": [_row(SEARCH_NAV)],
            "favourite": [_row(FAV_NAV)],
            "list": _open_list_steps(),
            "project": _open_project_steps(),
            "project_list": _open_project_list_steps(),
        }.get(ts, [_row(SEARCH_NAV)])
        return _S(
            _row(LOGIN),
            *prep,
            _row(
                "Kebab [data-qa-id='search-card-options-trigger'] → "
                "[data-qa-id='context-menu-item-more-actions'] → "
                "[data-qa-id='context-menu-item-font-versions'] "
                "(or [data-qa-id='more-actions-submenu-item-font-versions'])."
            ),
            _row(
                "In [data-qa-id='font-versions-drawer-list'] click "
                "[data-qa-id^='font-versions-drawer-item-'] → "
                f"{act} non-default version via [data-qa-id^='font-version-details-toggle-']."
            ),
            _row(audit_emit(op, ts), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )

    if op in {"activateList", "deActivateList"}:
        kebab = (
            "context-menu-item-activate-all-fonts"
            if op == "activateList"
            else "context-menu-item-deactivate-all-fonts"
        )
        prep = _open_project_list_steps() if ts == "project_list" else _open_list_steps()
        emit_ts = "project_list" if ts == "project_list" else "list"
        return _S(
            _row(LOGIN),
            *prep,
            _row(
                f"FontList row kebab → [data-qa-id='{kebab}']. "
                "Alternate inside list: [data-qa-id='bulk-select-all-button'] → "
                "[data-qa-id='activate-all-button'] / [data-qa-id='deactivate-all-button']."
            ),
            _row("Wait list snackbar success message."),
            _row(audit_emit(op, emit_ts), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )

    if op in {"activateFontProject", "deActivateFontProject"}:
        btn = (
            "[data-qa-id='activate-all-button']"
            if op == "activateFontProject"
            else "[data-qa-id='deactivate-all-button']"
        )
        return _S(
            _row(LOGIN),
            *_open_project_steps(),
            _row(
                "Click [data-qa-id='bulk-select-all-button'] or "
                "[data-qa-id='asset-list-select-all-checkbox'] or "
                "[data-qa-id='select-all-checkbox']."
            ),
            _row(
                f"Click {btn}. If Network shows activateFamily instead of {op}, "
                "emit AUDIT_RESULT with the ACTUAL operationName."
            ),
            _row(audit_emit(op, "project"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )

    if op in {"bulkActivateStyles", "bulkDeactivateStyles"}:
        btn = (
            "[data-qa-id='activate-all-button']"
            if op == "bulkActivateStyles"
            else "[data-qa-id='deactivate-all-button']"
        )
        prep = _open_project_steps() if ts == "project" else [_row(SEARCH_NAV)]
        return _S(
            _row(LOGIN),
            *prep,
            _row(
                "Select 2+ cards via checkboxes or [data-qa-id='bulk-select-all-button'] "
                "/ [data-qa-id='select-all-checkbox']."
            ),
            _row(f"In [data-qa-id='action-buttons'] click {btn}."),
            _row(audit_emit(op, ts if ts in {"global", "project"} else "global"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )

    if op in {"bulkActivateLists", "bulkDeactivateLists"}:
        kebab = (
            "context-menu-item-activate-all-fonts"
            if op == "bulkActivateLists"
            else "context-menu-item-deactivate-all-fonts"
        )
        where = (
            "Project Library assets table"
            if ts in {"project", "project_list"}
            else "My Library /library [data-qa-id='assets-table-view']"
        )
        return _S(
            _row(LOGIN),
            _row(f"Open {where}."),
            _row("Checkbox-select 2+ FontList rows only."),
            _row(f"Right-click → [data-qa-id='{kebab}']."),
            _row(audit_emit(op, "list" if ts == "list" else ts), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )

    if op == "pinAsset":
        return _S(
            _row(LOGIN),
            _row(
                LIB_NAV
                + " OR Search → [data-qa-id='sorted-search-location-dropdown'] → Lists & folders."
            ),
            _row(
                "Unpinned Folder/FontList kebab → [data-qa-id='context-menu-item-pin-list'] "
                "OR [data-qa-id='context-menu-item-pin-folder']."
            ),
            _row(audit_emit(op, "global"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "unpinAsset":
        return _S(
            _row(LOGIN),
            _row("Open a pinned Folder/FontList (sidebar pin or /library)."),
            _row(
                "Kebab → [data-qa-id='context-menu-item-unpin-list'] OR "
                "[data-qa-id='context-menu-item-unpin-folder']."
            ),
            _row(audit_emit(op, "global"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "updateAssets":
        return _S(
            _row(LOGIN),
            _row("Open My Library or Project Library → Webkits tab."),
            _row(
                "Click [data-qa-id^='webkit-actions-take-offline-'] OR "
                "[data-qa-id^='webkit-actions-take-online-']."
            ),
            _row(audit_emit(op, "global"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )

    if op == "addFavoriteFamilies":
        return _S(
            _row(LOGIN),
            _row(SEARCH_NAV),
            _row(
                "On card with outline heart click [data-qa-id='icon-favorite'] OR kebab → "
                "[data-qa-id='context-menu-item-add-to-favourites']. "
                "Wait [data-qa-id='snackbar-success']."
            ),
            _row(audit_emit(op, "favourite" if ts == "favourite" else "global"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "removeFavoriteFamilies":
        return _S(
            _row(LOGIN),
            _row(FAV_NAV),
            _row("Click filled [data-qa-id='icon-favorite'] (solid → outline)."),
            _row(audit_emit(op, "favourite"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "addFavoriteStyles":
        return _S(
            _row(LOGIN),
            _row(SEARCH_NAV),
            _row(
                "Open QV ([data-qa-id='font preview text']) or family Styles → "
                "click [data-qa-id='icon-favorite'] on ONE style row."
            ),
            _row(audit_emit(op, ts), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "removeFavoriteStyles":
        return _S(
            _row(LOGIN),
            _row(FAV_NAV + " Or open favourited style in QV."),
            _row("Click filled [data-qa-id='icon-favorite'] on the style row."),
            _row(audit_emit(op, "favourite"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "addFavoritePair":
        return _S(
            _row(LOGIN),
            _row(SEARCH_NAV),
            _row(
                "Kebab → [data-qa-id='context-menu-item-pairs-of-this-fonts'] → "
                "click [data-qa-id^='pairing-font-card-favorite-']."
            ),
            _row(audit_emit(op, ts), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "removeFavoritePair":
        return _S(
            _row(LOGIN),
            _row("Open /library/favourites/pairs."),
            _row("Click [data-qa-id^='pairing-font-card-favorite-'] to toggle OFF."),
            _row(audit_emit(op, "favourite"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )

    if op == "addFontListFamilies":
        return _S(
            _row(LOGIN),
            _row(SEARCH_NAV),
            _row(
                "Family kebab [data-qa-id='search-card-options-trigger'] → "
                "[data-qa-id='context-menu-item-add-to-list-or-tag'] → "
                "optional search [data-qa-id='add-to-list-or-folder-drawer-search'] → "
                "click [data-qa-id^='add-to-list-or-tag-drawer-add-']."
            ),
            _row(audit_emit(op, "list" if ts == "list" else ts), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "addFontListStyles":
        return _S(
            _row(LOGIN),
            _row(SEARCH_NAV),
            _row(
                "Open style/QV → [data-qa-id='context-menu-item-add-to-list-or-tag'] → "
                "[data-qa-id^='add-to-list-or-tag-drawer-add-']."
            ),
            _row(audit_emit(op, "list" if ts == "list" else ts), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "addFontProjectFamilies":
        return _S(
            _row(LOGIN),
            _row(SEARCH_NAV),
            _row(
                "Family kebab → Add to project → [data-qa-id='add-to-project-drawer-body'] → "
                "select project → [data-qa-id='add-to-project-drawer-submit']."
            ),
            _row(audit_emit(op, "project"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "addFontProjectStyles":
        return _S(
            _row(LOGIN),
            *_open_project_steps(),
            _row(
                "Use [data-qa-id='project-fonts-browse-inventory-btn'] or Search add-to-project "
                "for a STYLE → [data-qa-id='add-to-project-drawer-submit']."
            ),
            _row(audit_emit(op, "project"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "removeFontProjectStyles":
        return _S(
            _row(LOGIN),
            *_open_project_steps(),
            _row("Remove one style from project (manage fonts / Remove from project → apply)."),
            _row(audit_emit(op, "project"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )

    if op == "bulkAddStylesToList":
        return _S(
            _row(LOGIN),
            _row(SEARCH_NAV),
            _row(
                "Multi-select 2+ → [data-qa-id='action-buttons-options-trigger'] Add to list "
                "OR [data-qa-id='context-menu-item-add-to-list-or-tag'] → "
                "[data-qa-id^='add-to-list-or-tag-drawer-add-']."
            ),
            _row(audit_emit(op, "list"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "bulkRemoveStylesFromList":
        return _S(
            _row(LOGIN),
            *_open_list_steps(),
            _row(
                "Multi-select → Add to list drawer → "
                "[data-qa-id^='add-to-list-or-tag-drawer-remove-']."
            ),
            _row(audit_emit(op, "list"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "bulkRemoveStylesFromFavourites":
        return _S(
            _row(LOGIN),
            _row(FAV_NAV),
            _row(
                "Multi-select → [data-qa-id='favourites-bulk-remove-from-favorites-item'] OR "
                "[data-qa-id='context-menu-item-remove-from-favourites']."
            ),
            _row(audit_emit(op, "favourite"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "bulkCopyAssets":
        return _S(
            _row(LOGIN),
            _row(LIB_NAV),
            _row(
                "Select row(s) → [data-qa-id='context-menu-item-copy-to'] → destination → "
                "[data-qa-id='copy-assets-drawer-copy']."
            ),
            _row(audit_emit(op, ts), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "bulkMoveAssets":
        return _S(
            _row(LOGIN),
            _row(LIB_NAV),
            _row(
                "Select row(s) → [data-qa-id='context-menu-item-move-to'] → destination → "
                "[data-qa-id='move-assets-drawer-move']."
            ),
            _row(audit_emit(op, ts), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "bulkTagStyles":
        return _S(
            _row(LOGIN),
            _row(SEARCH_NAV),
            _row(
                "Multi-select 2+ → [data-qa-id='context-menu-item-add-to-list-or-tag'] → "
                "pick tag / Create tag with selection."
            ),
            _row(audit_emit(op, ts), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "bulkUntagStyles":
        return _S(
            _row(LOGIN),
            _row("Open /manage/tags/list → [data-qa-id^='configure-button-']."),
            _row(
                "Fonts tagged → select → [data-qa-id='Untag-fonts-btn'] "
                "or [data-qa-id^='fonts-tagged-untag-']."
            ),
            _row(audit_emit(op, "manage_tags"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "updatePrivateTag":
        return _S(
            _row(LOGIN + " Requires company admin."),
            _row(MANAGE_NAV),
            _row(TAGS_NAV),
            _row(
                "Click first visible [data-qa-id^='configure-button-'] OR tag name "
                "[data-qa-id^='tag-name-text-'] → [data-qa-id='configure-tag-drawer'] opens."
            ),
            _row(
                "Edit [data-qa-id='configure-tag-name-input'] input — append _audit suffix "
                "(keep name unique, max 64 chars)."
            ),
            _row(
                "Click [data-qa-id='configure-tag-update-button'] button. "
                "Wait [data-qa-id='snackbar-success']."
            ),
            _row(audit_emit(op, "manage_tags"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "updatePrivateTagAssociations":
        return _S(
            _row(LOGIN),
            _row(
                "/manage/tags/list → configure → [data-qa-id^='assign-fonts-button-'] → "
                "toggle fonts → apply."
            ),
            _row(audit_emit(op, "manage_tags"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )

    if op == "createAsset":
        if ts in {"project", "project_list"}:
            return _S(
                _row(LOGIN),
                *_open_project_steps(),
                _row(
                    "Project Library → [data-qa-id='create-list-button'] "
                    "(or [data-qa-id='create-folder-button']) → "
                    "[data-qa-id='asset-name-input'] → [data-qa-id='drawer-primary-button']."
                ),
                _row(audit_emit(op, ts), audit_expected(op)),
                op=op,
                touch=touch_canon,
            )
        return _S(
            _row(LOGIN),
            _row(LIB_NAV),
            _row(
                "[data-qa-id='create-list-button'] OR [data-qa-id='create-folder-button'] OR "
                "[data-testid='sidebar-add-library-button'] → "
                "[data-qa-id='asset-type-list-button'] if needed → "
                "[data-qa-id='asset-name-input'] → [data-qa-id='drawer-primary-button']."
            ),
            _row(audit_emit(op, "list" if ts == "list" else ts), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "createProject":
        return _S(
            _row(LOGIN),
            _row(
                "Click [data-testid='sidebar-add-project-button'] → /projects/create → "
                "enter name → [data-qa-id='project-creation-desktop-next'] through steps → finish."
            ),
            _row(audit_emit(op, "project" if ts == "project" else "global"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "duplicateProject":
        return _S(
            _row(LOGIN),
            *_open_project_steps(),
            _row(
                "[data-qa-id='project-library-title-kebab'] → "
                "[data-qa-id='project-library-kebab-duplicate'] "
                "(or [data-qa-id='context-menu-item-duplicate-project']). "
                "Emit AUDIT_RESULT with ACTUAL operationName from Network."
            ),
            _row(audit_emit(op, "project"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )

    if op == "dismissNotification":
        return _S(
            _row(LOGIN),
            _row("Click [data-qa-id='notification-btn'] → /notifications."),
            _row(
                "Hover [data-qa-id^='notification-row-'] → "
                "[data-qa-id^='notification-action-tooltip-notification-delete-tt-'] "
                "or [data-qa-id^='notification-delete-'] → confirm Yes, dismiss."
            ),
            _row(audit_emit(op, "notifications"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "markNotificationRead":
        return _S(
            _row(LOGIN),
            _row("Click [data-qa-id='notification-btn'] → /notifications."),
            _row(
                "Click [data-qa-id^='notification-mark-read-'] OR "
                "[data-qa-id='notifications-mark-all-read'] → confirm."
            ),
            _row(audit_emit(op, "notifications"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )

    if op == "setLanguagePreference":
        return _S(
            _row(LOGIN),
            _row(
                "Profile → [data-qa-id='profile-menu-item-language'] or /preferences/general → "
                "[data-qa-id='preferences-language-dropdown'] → pick different language."
            ),
            _row(audit_emit(op, "preferences"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "updateCustomerSettings":
        return _S(
            _row(LOGIN),
            _row("Open /company-setup/review-details (Manage → Company Settings)."),
            _row(
                "Toggle a setting → [data-qa-id='save-button']. Wait snackbar."
            ),
            _row(audit_emit(op, "account"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "getCustomerSettings":
        return _S(
            _row(LOGIN),
            _row(
                "Navigate /company-setup/review-details — Network filter getCustomerSettings "
                "(fires on load)."
            ),
            _row(audit_emit(op, "account"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "createUserInvitations":
        return _S(
            _row(LOGIN),
            _row(
                "/manage/users-and-teams/users → [data-qa-id='add-users-button'] → "
                "[data-qa-id='email-input-input'] → [data-qa-id='email-add-button'] → "
                "[data-qa-id='primary-action-button']."
            ),
            _row(audit_emit(op, "user_access"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "createRole":
        return _S(
            _row(LOGIN + " Requires company admin (Manage → Users & teams → Roles)."),
            _row(MANAGE_NAV),
            _row(TEAMS_USERS_NAV),
            _row(ROLES_TAB_NAV),
            _row(
                "Click [data-qa-id='add-role-button'] → [data-qa-id='drawer-body'] opens. "
                "Step 1 [data-qa-id='role-drawer-tab-0'] active."
            ),
            _row(
                "Type unique role name in [data-qa-id='add-role-name-input'] input "
                "(e.g. AuditRole_<short-random>). Optional: description in "
                "[data-qa-id='add-team-description-textarea'] textarea."
            ),
            _row(
                "Click [data-qa-id='drawer-primary-button'] (Next) → step 2 "
                "[data-qa-id='role-drawer-tab-1'] Configure permissions."
            ),
            _row(
                "Leave all permission checkboxes at defaults (do not toggle). "
                "Click [data-qa-id='drawer-primary-button'] (Next) → step 3 Users assigned."
            ),
            _row(
                "Do NOT add users. Click [data-qa-id='drawer-primary-button'] "
                "(Create role). Wait [data-qa-id='snackbar-success'] → dismiss."
            ),
            _row(audit_emit(op, "user_access"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "createPrivateTags":
        emit_ts = "global" if ts in {"", "global"} else ts
        return _S(
            _row(LOGIN + " Requires company admin (Manage → Tags)."),
            _row(MANAGE_NAV),
            _row(TAGS_NAV),
            _row(
                "Click [data-qa-id='create-tag-button'] → [data-qa-id='create-tags-drawer'] opens."
            ),
            _row(
                "Type ONE unique tag name in [data-qa-id='tag-names-input'] "
                "(e.g. AuditTag_<short-random>). Do not reuse existing tag names."
            ),
            _row(
                "Click [data-qa-id='create-tags-drawer-submit'] button (enabled). "
                "Wait [data-qa-id='snackbar-success'] → dismiss."
            ),
            _row(audit_emit(op, emit_ts), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "deleteRoles":
        return _S(
            _row(LOGIN),
            _row(
                "/manage/users-and-teams/roles → [data-qa-id^='roles-actions-menu-'] → "
                "[data-qa-id^='roles-action-delete-'] → confirm."
            ),
            _row(audit_emit(op, "user_access"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "deleteTeams":
        return _S(
            _row(LOGIN),
            _row(
                "/manage/users-and-teams/teams → select team → delete → confirm "
                "[data-qa-id='mtc-add-edit-teams-delete-team-modal']."
            ),
            _row(audit_emit(op, "user_access"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "createServiceAccount":
        return _S(
            _row(LOGIN),
            _row(
                "/manage/users-and-teams/servers → [data-qa-id='create-server-account-button'] → "
                "fill → [data-qa-id='create-server-drawer-submit']."
            ),
            _row(audit_emit(op, "user_access"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "bulkUpdateProfiles":
        return _S(
            _row(LOGIN),
            _row(
                "/manage/users-and-teams/users → select 2+ → [data-qa-id='changeroles__button'] → "
                "Apply [data-qa-id='listcard-apply-selection-button']."
            ),
            _row(audit_emit(op, "user_access"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )

    if op == "processUploadSessionFonts":
        return _S(
            _row(LOGIN),
            _row(
                "[data-qa-id='sidebar-scan-document-button'] → upload file → "
                "[data-qa-id='document-scan-scan-now-button'] → wait "
                "[data-qa-id='document-scan-results']."
            ),
            _row(audit_emit(op, "global"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "updateSessionFiles":
        return _S(
            _row(LOGIN),
            _row(
                "Document scan drawer with files → "
                "click [data-qa-id^='document-scan-delete-docscan-']."
            ),
            _row(audit_emit(op, "global"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "addStyleDocument":
        return _S(
            _row(LOGIN),
            _row(
                "Manage → Imported fonts → [data-qa-id='imported-fonts-fonts-import'] → "
                "detail → [data-qa-id='import-font-detail-upload-docs']."
            ),
            _row(audit_emit(op, "global"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "submitIntentForProduction":
        return _S(
            _row(LOGIN),
            _row(SEARCH_NAV),
            _row(
                "Kebab → [data-qa-id='context-menu-item-request-for-production'] → "
                "complete [data-qa-id='new-production-font-drawer'] → submit."
            ),
            _row(audit_emit(op, "global"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "parseAndCreateContract":
        return _S(
            _row(LOGIN),
            _row(
                "/manage/imported-fonts/licenses → "
                "[data-qa-id='imported-fonts-add-license-upload-entry'] → Verify upload row."
            ),
            _row(audit_emit(op, "global"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "createContract":
        return _S(
            _row(LOGIN),
            _row(
                "/manage/imported-fonts/licenses → "
                "[data-qa-id='imported-fonts-add-license-manual-entry'] → fill → Save."
            ),
            _row(audit_emit(op, "global"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "cancelBatch":
        return _S(
            _row(
                "WEB GAP: no cancel control in mtconnect-ui. If bulk drawer exposes Cancel, click it; "
                "else stop — do not invent clicks."
            ),
            _row(audit_emit(op, ts), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )
    if op == "syncUnSyncVariations":
        return _S(
            _row(
                "WEB GAP: desktop Connect only. Do not wander web UI. "
                "If desktop available use font-version toggles; else stop."
            ),
            _row(audit_emit(op, "global"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )

    return _S(
        _row(LOGIN),
        _row(
            f"Navigate to UI for touchpoint={touch_canon}. "
            f"Click the control that posts operationName={op}. Use [data-qa-id=…] when visible."
        ),
        _row(audit_emit(op, ts), audit_expected(op)),
        op=op,
        touch=touch_canon,
    )


def steps_for_selection(selection: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    items = [s for s in selection if isinstance(s, dict)]
    n = len(items)
    for idx, s in enumerate(items, 1):
        op = str(s.get("operation") or "").strip()
        touch = str(s.get("touchpoint") or "").strip()
        label = str(s.get("label") or op).strip()
        if n > 1:
            out.append(
                {
                    "op": op,
                    "touchpoint": touch,
                    "step": (
                        f"=== EVENT {idx}/{n}: {label} — follow locators, emit AUDIT_RESULT, "
                        f"then event {idx + 1 if idx < n else 'DONE'}. NO RETRIES. ==="
                    ),
                    "expected": "",
                }
            )
        out.extend(recipe_for(op, touch, label=label))
    if n > 1:
        out.append(
            {
                "op": "",
                "touchpoint": "",
                "step": f"After all {n} AUDIT_RESULT lines, close browser. No retries.",
                "expected": "",
            }
        )
    return out


def compact_checklist(selection: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for i, s in enumerate([x for x in selection if isinstance(x, dict)], 1):
        op = str(s.get("operation") or "").strip()
        touch = str(s.get("touchpoint") or "").strip()
        ts = short_touch(touch)
        label = str(s.get("label") or f"{op}({ts})")
        lines.append(
            f"{i}. {label} → fire {op} once (NO RETRY) → "
            f"AUDIT_RESULT|operation={op}|correlation_id=<uuid>|touchpoint={ts}"
        )
    return lines


def testrail_steps_separated(op: str, touch: str, *, label: str = "") -> list[dict[str, str]]:
    rows = recipe_for(op, touch, label=label)
    return [
        {"content": r["step"], "expected": r.get("expected") or ""}
        for r in rows
    ]


def testrail_steps_text(op: str, touch: str, *, label: str = "") -> str:
    rows = recipe_for(op, touch, label=label)
    lines = []
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. {r['step']}")
        if r.get("expected"):
            lines.append(f"   Expected: {r['expected']}")
    return "\n".join(lines)
