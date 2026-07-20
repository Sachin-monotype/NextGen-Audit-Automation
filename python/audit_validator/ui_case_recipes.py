"""CasePilot UI recipes — detailed, ordered click paths for an anonymous AI runner.

Sources of truth for selectors / navigation:
  - MTConnectAutomation page objects + tests (data-qa-id, click order)
  - mtconnect-ui routes and menus

Goal: TRIGGER the GraphQL mutation and emit AUDIT_RESULT. Prefer reuse of existing
projects/lists/favourites. Do NOT hardcode family IDs (no 910052505 etc.) — pick any
visible card matching the needed ON/OFF state.

``prefer_steps=context_over_testrail`` — these recipes are what CasePilot follows.
"""

from __future__ import annotations

import os
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
        f"CRITICAL — Network filter operationName={op} → copy response header "
        f"**correlation-id** (NOT x-correlation-id) → emit exactly: "
        f"AUDIT_RESULT|operation={op}|correlation_id=<real-uuid>|touchpoint={touch_short}"
    )


def _seed_hint() -> str:
    env = (
        os.getenv("SEED_FAMILY_ID", "").strip()
        or os.getenv("TOUCHPOINT_FAMILY_ID", "").strip()
        or ""
    )
    if env:
        return (
            f"Optional seed: if SEED_FAMILY_ID is set ({env}), you may search that id; "
            "otherwise pick ANY visible family card — never invent hardcoded ids."
        )
    return (
        "Pick ANY visible family/style on screen that matches the needed ON/OFF state. "
        "Do NOT hardcode family ids."
    )


def _S(*lines: str, op: str, touch: str) -> list[dict[str, str]]:
    return [{"op": op, "touchpoint": touch, "step": x} for x in lines if x]


def _login_block() -> list[str]:
    return [
        "LOGIN (skip if already signed in): open /search → click data-qa-id=sign-in-button → "
        "Auth0 #username → Continue → #password → Continue → pick workspace if shown → "
        "wait for data-qa-id=expandable-searchbar__wrapper. Dismiss snackbars/overlays.",
    ]


def _nav_search() -> str:
    return (
        "Sidebar Search (data-testid=menu-item-Search) → URL /search → "
        "type a short query (e.g. hel) in data-qa-id=expandable-searchbar_input → Enter → "
        "wait for family cards (font-name + toggle-btn)."
    )


def _nav_favourites() -> str:
    return (
        "Sidebar Favorites (data-testid=menu-item-Favorites or #menu-item-tooltip-favorites) → "
        "URL /library/favourites (fonts tab). Wait for cards with toggle-btn."
    )


def _nav_my_library() -> str:
    return (
        "Sidebar My Library → Show all assets (data-testid=sidebar-my-library-show-all) → "
        "URL /library. Prefer an existing FontList row; only create if none exist."
    )


def _open_or_create_list() -> list[str]:
    return [
        _nav_my_library(),
        "If a FontList already exists: open it (URL /library/FontList/{id}). "
        "Else: click data-qa-id=create-list-button (or sidebar-add-library-button) → "
        "fill data-qa-id=asset-name-input with a unique name → data-qa-id=drawer-primary-button.",
        "If the list is empty: Discover fonts or Search → pick any deactivated family card → "
        "kebab data-qa-id=search-card-options-trigger → "
        "data-qa-id=context-menu-item-add-to-list-or-tag → "
        "search list name → click data-qa-id^=add-to-list-or-tag-drawer-add- → reopen the list.",
    ]


def _open_or_create_project() -> list[str]:
    return [
        "Sidebar Projects → Show all projects (or click a recent project "
        "data-qa-id^=sidebar-recent-project-). Prefer a project that already has fonts.",
        "If none: data-testid=sidebar-add-project-button → /projects/create → "
        "enter name → data-qa-id=project-creation-desktop-next through wizard → "
        "add at least one family on Add fonts if prompted → finish.",
        "Land on /projects/library/{projectId}/fonts (project fonts grid).",
    ]


def _open_or_create_project_list() -> list[str]:
    return [
        *_open_or_create_project(),
        "Open Project Library (Show all assets inside project) → /projects/library/{projectId}.",
        "If a FontList already exists inside this project with fonts: open it. "
        "Else: data-qa-id=create-list-button → name → drawer-primary-button.",
        "On project Fonts tab, ensure at least one family is in the project; then add that "
        "family to the project list (card kebab → Add to list or tag → pick the project list).",
        "Open the project FontList grid (URL under /projects/library/{projectId}/… — "
        "NOT global /library). Stay on this scoped grid for the activate/deactivate click.",
    ]


def _ensure_toggle(state: str) -> str:
    """state is 'off' (need deactivated) or 'on' (need activated)."""
    if state == "off":
        return (
            "On the target card: if data-qa-id=toggle-btn looks ON/activated, click once to "
            "deactivate; wait grey deactivation snackbar (data-testid=deactivation-toast-wrapper) "
            "and dismiss. Target must be OFF before the activate click."
        )
    return (
        "On the target card: if data-qa-id=toggle-btn looks OFF, click once to activate; "
        "wait success snackbar and dismiss. Target must be ON before the deactivate click."
    )


def recipe_for(op: str, touch: str, *, label: str = "") -> list[dict[str, str]]:
    """Detailed ordered steps: navigate → prepare → click → AUDIT_RESULT."""
    op = (op or "").strip()
    touch = (touch or "").strip()
    ts = short_touch(touch)
    touch_canon = touch or {
        "global": "Discovery/Browse (global)",
        "list": "List (FONTLIST)",
        "favourite": "Favourite",
        "project": "Project",
        "project_list": "Project > List",
        "notifications": "Notifications",
        "preferences": "Preferences",
        "account": "Account / Workspace",
        "user_access": "User Access",
        "manage_tags": "Manage Tags",
        "library_assets": "My Library assets",
    }.get(ts, touch)
    label = label or f"{op}({ts})"
    seed = _seed_hint()

    # ── Family activate ─────────────────────────────────────────────
    if op == "activateFamily" and ts == "global":
        return _S(
            f"GOAL: fire activateFamily with global scope (no projectId / listIds). {seed}",
            *_login_block(),
            _nav_search(),
            "Stay on Search font-family cards. Do NOT open /family/… detail. Do NOT open Quick View.",
            _ensure_toggle("off"),
            "Click data-qa-id=toggle-btn on that card to Activate. Wait snackbar-success.",
            audit_emit(op, "global"),
            op=op,
            touch=touch_canon,
        )
    if op == "activateFamily" and ts == "favourite":
        return _S(
            f"GOAL: fire activateFamily with listType=Favorite. {seed}",
            *_login_block(),
            "If no favourites yet: " + _nav_search() + " Then click data-qa-id=icon-favorite "
            "(outline heart) on a card OR kebab → context-menu-item-add-to-favourites.",
            _nav_favourites(),
            "Do NOT open family detail.",
            _ensure_toggle("off"),
            "Click data-qa-id=toggle-btn to Activate on the Favourites grid.",
            audit_emit(op, "favourite"),
            op=op,
            touch=touch_canon,
        )
    if op == "activateFamily" and ts == "list":
        return _S(
            f"GOAL: fire activateFamily with listType=Fontlist + listIds (no projectId). {seed}",
            *_login_block(),
            *_open_or_create_list(),
            "On the list grid: do NOT open family detail.",
            _ensure_toggle("off"),
            "Click family data-qa-id=toggle-btn to Activate (card toggle — NOT kebab "
            "Activate all fonts, which fires activateList).",
            audit_emit(op, "list"),
            op=op,
            touch=touch_canon,
        )
    if op == "activateFamily" and ts == "project":
        return _S(
            f"GOAL: fire activateFamily with listType=Fontproject + projectId. {seed}",
            *_login_block(),
            *_open_or_create_project(),
            "Stay on /projects/library/{id}/fonts. Do NOT navigate to global Search for this click.",
            _ensure_toggle("off"),
            "Click family data-qa-id=toggle-btn to Activate (or Activate fonts if only one family).",
            audit_emit(op, "project"),
            op=op,
            touch=touch_canon,
        )
    if op == "activateFamily" and ts == "project_list":
        return _S(
            f"GOAL: fire activateFamily with BOTH projectId AND listIds. {seed}",
            *_login_block(),
            *_open_or_create_project_list(),
            "On the project list grid only — do NOT use global Search toggle.",
            _ensure_toggle("off"),
            "Click family data-qa-id=toggle-btn to Activate.",
            audit_emit(op, "project_list"),
            op=op,
            touch=touch_canon,
        )

    # ── Family deactivate ───────────────────────────────────────────
    if op == "deactivateFamilies":
        prep = {
            "global": [_nav_search(), "Stay on Search cards. Do NOT open /family/…."],
            "favourite": [_nav_favourites()],
            "list": _open_or_create_list(),
            "project": _open_or_create_project(),
            "project_list": _open_or_create_project_list(),
        }.get(ts, [_nav_search()])
        return _S(
            f"GOAL: fire deactivateFamilies ({ts}). Family must be ON in this scope first. {seed}",
            *_login_block(),
            *prep,
            _ensure_toggle("on"),
            "Click data-qa-id=toggle-btn to Deactivate. Wait grey deactivation snackbar.",
            audit_emit(op, ts),
            op=op,
            touch=touch_canon,
        )

    # ── Style activate / deactivate (detail / QV required) ──────────
    if op in {"activateStyle", "deactivateStyle"}:
        act = "Activate" if op == "activateStyle" else "Deactivate"
        need = "OFF" if op == "activateStyle" else "ON"
        scope_nav = {
            "global": [_nav_search()],
            "favourite": [_nav_favourites()],
            "list": _open_or_create_list(),
            "project": _open_or_create_project(),
            "project_list": _open_or_create_project_list(),
        }.get(ts, [_nav_search()])
        return _S(
            f"GOAL: fire {op} ({ts}) — ONE style only (not whole family). {seed}",
            *_login_block(),
            *scope_nav,
            "Open style controls via ONE of: (A) click font preview text to open Quick View "
            "drawer → style rows with toggle-btn under drawer-body; "
            "(B) card kebab data-qa-id=search-card-options-trigger → "
            "context-menu-item-activate-styles → hover flyout panel-1 → pick one style; "
            "(C) open family detail /family/… → Styles tab → family-style-card-* toggle.",
            f"Ensure the chosen style is {need}, then {act} that ONE style toggle.",
            audit_emit(op, ts),
            op=op,
            touch=touch_canon,
        )

    # ── Variation ───────────────────────────────────────────────────
    if op in {"activateVariation", "deactivateVariation"}:
        act = "Activate" if op == "activateVariation" else "Deactivate"
        scope_nav = {
            "global": [_nav_search()],
            "favourite": [_nav_favourites()],
            "list": _open_or_create_list(),
            "project": _open_or_create_project(),
            "project_list": _open_or_create_project_list(),
        }.get(ts, [_nav_search()])
        return _S(
            f"GOAL: fire {op} ({ts}). Requires Font versions drawer. {seed}",
            *_login_block(),
            *scope_nav,
            "Card kebab → data-qa-id=context-menu-item-more-actions → "
            "data-qa-id=context-menu-item-font-versions.",
            "In font-versions-drawer-list click first style row → "
            "font-version-details-drawer-body → "
            f"{act} a NON-default version via data-qa-id^=font-version-details-toggle-.",
            audit_emit(op, ts),
            op=op,
            touch=touch_canon,
        )

    # ── List-wide activate / deactivate ─────────────────────────────
    if op in {"activateList", "deActivateList"}:
        kebab = (
            "context-menu-item-activate-all-fonts"
            if op == "activateList"
            else "context-menu-item-deactivate-all-fonts"
        )
        prep = (
            _open_or_create_project_list()
            if ts == "project_list"
            else _open_or_create_list()
        )
        emit_ts = "project_list" if ts == "project_list" else "list"
        return _S(
            f"GOAL: fire {op} ({emit_ts}) — list-wide mutation, NOT activateFamily. {seed}",
            *_login_block(),
            *prep,
            "From All assets / Project Library table OR inside the open list: open FontList "
            f"row kebab (md-button data-qa-id^=menu-icon-) → data-qa-id={kebab}.",
            "Alternate inside list: bulk-select-all-button → activate-fonts-toolbar-button "
            "(or deactivate equivalent).",
            "Wait list activation/deactivation snackbar.",
            audit_emit(op, emit_ts),
            op=op,
            touch=touch_canon,
        )

    # ── Project-wide ────────────────────────────────────────────────
    if op in {"activateFontProject", "deActivateFontProject"}:
        return _S(
            f"GOAL: fire {op}. Verify Network operationName matches (UI may batch activateFamily). {seed}",
            *_login_block(),
            *_open_or_create_project(),
            "Click data-qa-id=bulk-select-all-button (or select-all-checkbox).",
            "Click activate-fonts-toolbar-button / activate-all-button "
            "(or deactivate-all-button / deactivate-fonts-toolbar-button).",
            f"If Network shows {op}, use that; if it shows activateFamily/deactivateFamilies "
            "with project scope, still emit AUDIT_RESULT with the ACTUAL operationName.",
            audit_emit(op, "project"),
            op=op,
            touch=touch_canon,
        )

    # ── Bulk activate / deactivate styles ───────────────────────────
    if op in {"bulkActivateStyles", "bulkDeactivateStyles"}:
        btn = "activate-all-button" if op == "bulkActivateStyles" else "deactivate-all-button"
        prep = (
            _open_or_create_project()
            if ts == "project"
            else [_nav_search()]
        )
        return _S(
            f"GOAL: fire {op} ({ts}). Multi-select 2+ cards. {seed}",
            *_login_block(),
            *prep,
            "Select 2+ family cards (checkboxes) OR data-qa-id=bulk-select-all-button (cap ~50).",
            f"Click data-qa-id={btn} in action-buttons. Alternate: right-click selection → "
            "context-menu-item-activate-all-families / deactivate-all-styles.",
            "Wait bulk progress toast.",
            audit_emit(op, ts if ts in {"global", "project"} else "global"),
            op=op,
            touch=touch_canon,
        )

    # ── Bulk lists ──────────────────────────────────────────────────
    if op in {"bulkActivateLists", "bulkDeactivateLists"}:
        kebab = (
            "context-menu-item-activate-all-fonts"
            if op == "bulkActivateLists"
            else "context-menu-item-deactivate-all-fonts"
        )
        where = (
            "Project Library assets table"
            if ts in {"project", "project_list"}
            else "My Library /library assets table (assets-table-view)"
        )
        return _S(
            f"GOAL: fire {op} ({ts}). Select TWO OR MORE FontList rows only. {seed}",
            *_login_block(),
            f"Open {where}.",
            "Checkbox-select 2+ FontList rows (lists only — not folders/fonts).",
            f"Right-click → data-qa-id={kebab}.",
            audit_emit(op, "list" if ts == "list" else ts),
            op=op,
            touch=touch_canon,
        )

    # ── Pin / unpin / updateAssets ──────────────────────────────────
    if op == "pinAsset":
        return _S(
            f"GOAL: fire pinAsset. {seed}",
            *_login_block(),
            _nav_my_library() + " OR Search → data-qa-id=sorted-search-location-dropdown → Lists & folders.",
            "Find an unpinned Folder or FontList row/card.",
            "Kebab → data-qa-id=context-menu-item-pin-list OR context-menu-item-pin-folder.",
            "Wait snackbar “pinned successfully”.",
            audit_emit(op, "global"),
            op=op,
            touch=touch_canon,
        )
    if op == "unpinAsset":
        return _S(
            f"GOAL: fire unpinAsset. {seed}",
            *_login_block(),
            "Open /library or pinned sidebar entry for a pinned Folder/FontList.",
            "Kebab → data-qa-id=context-menu-item-unpin-list OR context-menu-item-unpin-folder.",
            audit_emit(op, "global"),
            op=op,
            touch=touch_canon,
        )
    if op == "updateAssets":
        return _S(
            f"GOAL: fire updateAssets (webkit online/offline). {seed}",
            *_login_block(),
            "Open My Library or Project Library → Webkits tab/section.",
            "On a webkit row click data-qa-id^=webkit-actions-take-offline- OR "
            "webkit-actions-take-online-.",
            audit_emit(op, "global"),
            op=op,
            touch=touch_canon,
        )

    # ── Favourites add / remove ─────────────────────────────────────
    if op == "addFavoriteFamilies":
        return _S(
            f"GOAL: fire addFavoriteFamilies. {seed}",
            *_login_block(),
            _nav_search(),
            "Pick a family card whose data-qa-id=icon-favorite is outline (not favourited).",
            "Click icon-favorite OR kebab → context-menu-item-add-to-favourites.",
            "Wait “added to Favourites” snackbar.",
            audit_emit(op, "favourite" if ts == "favourite" else "global"),
            op=op,
            touch=touch_canon,
        )
    if op == "removeFavoriteFamilies":
        return _S(
            f"GOAL: fire removeFavoriteFamilies. {seed}",
            *_login_block(),
            _nav_favourites(),
            "Click filled data-qa-id=icon-favorite on a family card (solid → outline).",
            audit_emit(op, "favourite"),
            op=op,
            touch=touch_canon,
        )
    if op == "addFavoriteStyles":
        return _S(
            f"GOAL: fire addFavoriteStyles — style-level heart, not family. {seed}",
            *_login_block(),
            _nav_search(),
            "Open QV (click font preview) or family detail Styles tab.",
            "Click data-qa-id=icon-favorite on ONE style row.",
            audit_emit(op, ts),
            op=op,
            touch=touch_canon,
        )
    if op == "removeFavoriteStyles":
        return _S(
            f"GOAL: fire removeFavoriteStyles. {seed}",
            *_login_block(),
            _nav_favourites() + " Or open a favourited style in QV/family detail.",
            "Click filled icon-favorite on the style row.",
            audit_emit(op, "favourite"),
            op=op,
            touch=touch_canon,
        )
    if op == "addFavoritePair":
        return _S(
            f"GOAL: fire addFavoritePair. {seed}",
            *_login_block(),
            _nav_search(),
            "Card kebab → data-qa-id=context-menu-item-pairs-of-this-fonts.",
            "On pairing grid click data-qa-id^=pairing-font-card-favorite- on a pair.",
            audit_emit(op, ts),
            op=op,
            touch=touch_canon,
        )
    if op == "removeFavoritePair":
        return _S(
            f"GOAL: fire removeFavoritePair. {seed}",
            *_login_block(),
            "Open /library/favourites/pairs.",
            "Click favourite control on an existing pair to toggle OFF.",
            audit_emit(op, "favourite"),
            op=op,
            touch=touch_canon,
        )

    # ── Add to list / project ───────────────────────────────────────
    if op == "addFontListFamilies":
        return _S(
            f"GOAL: fire addFontListFamilies. {seed}",
            *_login_block(),
            _nav_search(),
            "Family card kebab → context-menu-item-add-to-list-or-tag.",
            "In drawer search an existing FontList (add-to-list-or-folder-drawer-search) → "
            "click data-qa-id^=add-to-list-or-tag-drawer-add-.",
            audit_emit(op, "list" if ts == "list" else ts),
            op=op,
            touch=touch_canon,
        )
    if op == "addFontListStyles":
        return _S(
            f"GOAL: fire addFontListStyles (style-scoped). {seed}",
            *_login_block(),
            _nav_search(),
            "Open QV or style row → kebab → context-menu-item-add-to-list-or-tag → "
            "add to an existing FontList.",
            audit_emit(op, "list" if ts == "list" else ts),
            op=op,
            touch=touch_canon,
        )
    if op == "addFontProjectFamilies":
        return _S(
            f"GOAL: fire addFontProjectFamilies. {seed}",
            *_login_block(),
            _nav_search(),
            "Family card kebab → Add to project (or recent-projects-submenu-add-to-project-button).",
            "In add-to-project-drawer select a project → data-qa-id=add-to-project-drawer-submit.",
            audit_emit(op, "project"),
            op=op,
            touch=touch_canon,
        )
    if op == "addFontProjectStyles":
        return _S(
            f"GOAL: fire addFontProjectStyles. {seed}",
            *_login_block(),
            *_open_or_create_project(),
            "Use project-fonts-browse-inventory-btn or Search Add-to-project for a STYLE → submit drawer.",
            audit_emit(op, "project"),
            op=op,
            touch=touch_canon,
        )
    if op == "removeFontProjectStyles":
        return _S(
            f"GOAL: fire removeFontProjectStyles. {seed}",
            *_login_block(),
            *_open_or_create_project(),
            "On project fonts grid: remove one style from project "
            "(manage fonts / Remove from project / drawer deselect → submit).",
            audit_emit(op, "project"),
            op=op,
            touch=touch_canon,
        )

    # ── Bulk list / favourites / tag / assets ───────────────────────
    if op == "bulkAddStylesToList":
        return _S(
            f"GOAL: fire bulkAddStylesToList. {seed}",
            *_login_block(),
            _nav_search(),
            "Multi-select 2+ cards (select-all-checkbox or individual checks).",
            "Bulk Add to → list OR right-click → Create list with selection / "
            "context-menu-item-add-to-list-or-tag → add to existing list.",
            "Wait batch toast.",
            audit_emit(op, "list"),
            op=op,
            touch=touch_canon,
        )
    if op == "bulkRemoveStylesFromList":
        return _S(
            f"GOAL: fire bulkRemoveStylesFromList. {seed}",
            *_login_block(),
            *_open_or_create_list(),
            "Multi-select styles in the list → open Add to list drawer → "
            "click data-qa-id^=add-to-list-or-tag-drawer-remove- for that list.",
            audit_emit(op, "list"),
            op=op,
            touch=touch_canon,
        )
    if op == "bulkRemoveStylesFromFavourites":
        return _S(
            f"GOAL: fire bulkRemoveStylesFromFavourites. {seed}",
            *_login_block(),
            _nav_favourites(),
            "Multi-select cards → bulk more → favourites-bulk-remove-from-favorites-item "
            "OR right-click → context-menu-item-remove-from-favourites.",
            audit_emit(op, "favourite"),
            op=op,
            touch=touch_canon,
        )
    if op == "bulkCopyAssets":
        return _S(
            f"GOAL: fire bulkCopyAssets. {seed}",
            *_login_block(),
            _nav_my_library(),
            "Select one or more asset rows → kebab context-menu-item-copy-to "
            "(or toolbar Copy) → pick destination → data-qa-id=copy-assets-drawer-copy.",
            audit_emit(op, ts),
            op=op,
            touch=touch_canon,
        )
    if op == "bulkMoveAssets":
        return _S(
            f"GOAL: fire bulkMoveAssets. {seed}",
            *_login_block(),
            _nav_my_library(),
            "Select asset row(s) → kebab context-menu-item-move-to → "
            "pick destination → data-qa-id=move-assets-drawer-move.",
            audit_emit(op, ts),
            op=op,
            touch=touch_canon,
        )
    if op == "bulkTagStyles":
        return _S(
            f"GOAL: fire bulkTagStyles. {seed}",
            *_login_block(),
            _nav_search(),
            "Multi-select 2+ styles → right-click → Add to list or tag → pick existing tag "
            "OR Create tag with selection → confirm.",
            audit_emit(op, ts),
            op=op,
            touch=touch_canon,
        )
    if op == "bulkUntagStyles":
        return _S(
            f"GOAL: fire bulkUntagStyles. {seed}",
            *_login_block(),
            "Manage → Tags → /manage/tags/list → open a tag (configure-button-*).",
            "Fonts tagged tab → select fonts → data-qa-id=Untag-fonts-btn "
            "(or row untag fonts-tagged-untag-*).",
            audit_emit(op, "manage_tags"),
            op=op,
            touch=touch_canon,
        )
    if op == "updatePrivateTag":
        return _S(
            f"GOAL: fire updatePrivateTag. {seed}",
            *_login_block(),
            "Open /manage/tags/list → configure-button-* on a tag.",
            "Edit configure-tag-name-input → configure-tag-update-button.",
            audit_emit(op, "manage_tags"),
            op=op,
            touch=touch_canon,
        )
    if op == "updatePrivateTagAssociations":
        return _S(
            f"GOAL: fire updatePrivateTagAssociations. {seed}",
            *_login_block(),
            "Open /manage/tags/list → configure tag → assign-fonts-button-* → "
            "search/toggle fonts → apply. "
            "Alternate: Search Add-to-tag drawer apply associations.",
            audit_emit(op, "manage_tags"),
            op=op,
            touch=touch_canon,
        )

    # ── Create / duplicate ──────────────────────────────────────────
    if op == "createAsset":
        if ts == "project_list" or ts == "project":
            return _S(
                f"GOAL: fire createAsset inside a project ({ts}). {seed}",
                *_login_block(),
                *_open_or_create_project(),
                "Open Project Library All assets → data-qa-id=create-list-button "
                "(or create-folder-button) → asset-name-input → drawer-primary-button.",
                audit_emit(op, ts),
                op=op,
                touch=touch_canon,
            )
        return _S(
            f"GOAL: fire createAsset in My Library ({ts}). {seed}",
            *_login_block(),
            _nav_my_library(),
            "Click create-list-button (FontList) OR create-folder-button (Folder) "
            "OR sidebar-add-library-button → asset-name-input → drawer-primary-button.",
            audit_emit(op, "list" if ts == "list" else ts),
            op=op,
            touch=touch_canon,
        )
    if op == "createProject":
        return _S(
            f"GOAL: fire createProject. {seed}",
            *_login_block(),
            "Click data-testid=sidebar-add-project-button → /projects/create.",
            "Enter project name → project-creation-desktop-next through steps "
            "(skip members if allowed; optional one font) → finish Create.",
            audit_emit(op, "project" if ts == "project" else "global"),
            op=op,
            touch=touch_canon,
        )
    if op == "duplicateProject":
        return _S(
            f"GOAL: fire duplicateProject (UI may use bulkCopyAssets under the hood). {seed}",
            *_login_block(),
            *_open_or_create_project(),
            "Title kebab data-qa-id=project-library-title-kebab → "
            "project-library-kebab-duplicate. Wait duplicate snackbar.",
            "Emit AUDIT_RESULT with the ACTUAL operationName from Network.",
            audit_emit(op, "project"),
            op=op,
            touch=touch_canon,
        )

    # ── Notifications ───────────────────────────────────────────────
    if op == "dismissNotification":
        return _S(
            f"GOAL: fire dismissNotification. {seed}",
            *_login_block(),
            "Click header data-qa-id=notification-btn → /notifications.",
            "Hover first data-qa-id^=notification-row- → click delete "
            "data-qa-id^=notification-action-tooltip-notification-delete-tt- → "
            "confirm modal “Yes, dismiss”.",
            audit_emit(op, "notifications"),
            op=op,
            touch=touch_canon,
        )
    if op == "markNotificationRead":
        return _S(
            f"GOAL: fire markNotificationRead. {seed}",
            *_login_block(),
            "Open Notifications (notification-btn).",
            "Click data-qa-id^=notification-mark-read- on one item OR "
            "notifications-mark-all-read → confirm.",
            audit_emit(op, "notifications"),
            op=op,
            touch=touch_canon,
        )

    # ── Settings / admin ────────────────────────────────────────────
    if op == "setLanguagePreference":
        return _S(
            f"GOAL: fire setLanguagePreference. {seed}",
            *_login_block(),
            "Profile avatar → profile-menu-item-language OR open /preferences/general.",
            "preferences-language-dropdown → pick a DIFFERENT language → wait save.",
            audit_emit(op, "preferences"),
            op=op,
            touch=touch_canon,
        )
    if op == "updateCustomerSettings":
        return _S(
            f"GOAL: fire updateCustomerSettings. {seed}",
            *_login_block(),
            "Manage → Company Settings (or /company-setup/review-details).",
            "Toggle any setting (e.g. review-details-setting-allow-downloading) → "
            "data-qa-id=save-button. Wait snackbar.",
            audit_emit(op, "account"),
            op=op,
            touch=touch_canon,
        )
    if op == "getCustomerSettings":
        return _S(
            f"GOAL: fire getCustomerSettings (query on page load). {seed}",
            *_login_block(),
            "Navigate to /company-setup/review-details (Manage → Company Settings).",
            "Wait review-details-page ready — filter Network for getCustomerSettings / "
            "GetCustomerSettings (no extra click needed).",
            audit_emit(op, "account"),
            op=op,
            touch=touch_canon,
        )
    if op == "createUserInvitations":
        return _S(
            f"GOAL: fire createUserInvitations. {seed}",
            *_login_block(),
            "Open /manage/users-and-teams/users → data-qa-id=add-users-button.",
            "Enter a unique test email in email-input-input → email-add-button → "
            "primary-action-button (Invite all).",
            audit_emit(op, "user_access"),
            op=op,
            touch=touch_canon,
        )
    if op == "deleteRoles":
        return _S(
            f"GOAL: fire deleteRoles. Prefer a disposable/non-default role. {seed}",
            *_login_block(),
            "Open /manage/users-and-teams/roles.",
            "Row kebab roles-actions-menu-* → roles-action-delete-* → confirm.",
            audit_emit(op, "user_access"),
            op=op,
            touch=touch_canon,
        )
    if op == "deleteTeams":
        return _S(
            f"GOAL: fire deleteTeams. Prefer a disposable team. {seed}",
            *_login_block(),
            "Open /manage/users-and-teams/teams → select team checkbox(es).",
            "Toolbar delete or row delete → confirm mtc-add-edit-teams-delete-team-modal.",
            audit_emit(op, "user_access"),
            op=op,
            touch=touch_canon,
        )
    if op == "createServiceAccount":
        return _S(
            f"GOAL: fire createServiceAccount (Servers / Font Bridge). {seed}",
            *_login_block(),
            "Open /manage/users-and-teams/servers → create-server-account-button.",
            "Fill name/description → create-server-drawer-submit. "
            "Dismiss token modal if shown.",
            audit_emit(op, "user_access"),
            op=op,
            touch=touch_canon,
        )
    if op == "bulkUpdateProfiles":
        return _S(
            f"GOAL: fire bulkUpdateProfiles. {seed}",
            *_login_block(),
            "Open /manage/users-and-teams/users → select 2+ users (checkbox-select-all).",
            "Bulk bar changeroles__button → pick role → listcard-apply-selection-button / Save.",
            audit_emit(op, "user_access"),
            op=op,
            touch=touch_canon,
        )

    # ── Import / scan / licensing ───────────────────────────────────
    if op == "processUploadSessionFonts":
        return _S(
            f"GOAL: fire processUploadSessionFonts via document scan. {seed}",
            *_login_block(),
            "Click data-qa-id=sidebar-scan-document-button.",
            "Upload a small PDF/image → document-scan-scan-now-button → "
            "wait document-scan-results (mutation fires on hydration).",
            audit_emit(op, "global"),
            op=op,
            touch=touch_canon,
        )
    if op == "updateSessionFiles":
        return _S(
            f"GOAL: fire updateSessionFiles. {seed}",
            *_login_block(),
            "Open document scan drawer with uploaded files → "
            "click data-qa-id^=document-scan-delete-docscan- on a row.",
            audit_emit(op, "global"),
            op=op,
            touch=touch_canon,
        )
    if op == "addStyleDocument":
        return _S(
            f"GOAL: fire addStyleDocument. {seed}",
            *_login_block(),
            "Manage → Imported fonts → Fonts → imported-fonts-fonts-import.",
            "Upload/open font detail → Documents → import-font-detail-upload-docs "
            "(or import-font-detail-docs-empty-upload).",
            audit_emit(op, "global"),
            op=op,
            touch=touch_canon,
        )
    if op == "submitIntentForProduction":
        return _S(
            f"GOAL: fire submitIntentForProduction (requires share-intent permission). {seed}",
            *_login_block(),
            _nav_search(),
            "Style/family kebab → context-menu-item-request-for-production → "
            "complete new-production-font-drawer → submit.",
            audit_emit(op, "global"),
            op=op,
            touch=touch_canon,
        )
    if op == "parseAndCreateContract":
        return _S(
            f"GOAL: fire parseAndCreateContract. {seed}",
            *_login_block(),
            "Open /manage/imported-fonts/licenses → Add → upload license file "
            "(imported-fonts-add-license-upload-entry) → Verify on the upload row.",
            audit_emit(op, "global"),
            op=op,
            touch=touch_canon,
        )
    if op == "createContract":
        return _S(
            f"GOAL: fire createContract. {seed}",
            *_login_block(),
            "Open /manage/imported-fonts/licenses → Add → "
            "imported-fonts-add-license-manual-entry → fill form → Save/Create.",
            audit_emit(op, "global"),
            op=op,
            touch=touch_canon,
        )
    if op == "cancelBatch":
        return _S(
            f"GOAL: fire cancelBatch. WEB UI GAP — no cancel control found in mtconnect-ui. {seed}",
            "If a bulk progress drawer exposes Cancel, click it and capture Network. "
            "Otherwise mark as not triggerable from web and stop (do not invent clicks).",
            audit_emit(op, ts),
            op=op,
            touch=touch_canon,
        )
    if op == "syncUnSyncVariations":
        return _S(
            f"GOAL: fire syncUnSyncVariations. WEB UI GAP — desktop Connect only. {seed}",
            "Web CasePilot: do not wander. If desktop bridge is available, use Font versions "
            "drawer toggles; else flag not triggerable from web and stop.",
            audit_emit(op, "global"),
            op=op,
            touch=touch_canon,
        )

    # ── Fallback ────────────────────────────────────────────────────
    return _S(
        f"GOAL: fire {label} only. Reuse existing UI state. {seed}",
        *_login_block(),
        f"Navigate to the UI surface for touchpoint={touch_canon}.",
        f"Perform the single control that posts GraphQL operationName={op}. "
        "Do not open unrelated family detail. Do not explore after the mutation fires.",
        audit_emit(op, ts),
        op=op,
        touch=touch_canon,
    )


def steps_for_selection(selection: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Build steps. Multi-select → numbered checklist; finish each then continue."""
    out: list[dict[str, str]] = []
    items = [s for s in selection if isinstance(s, dict)]
    n = len(items)
    for idx, s in enumerate(items, 1):
        op = str(s.get("operation") or "").strip()
        touch = str(s.get("touchpoint") or "").strip()
        label = str(s.get("label") or op).strip()
        extra = str(s.get("notes") or s.get("extra_details") or "").strip()
        if n > 1:
            out.append(
                {
                    "op": op,
                    "touchpoint": touch,
                    "step": (
                        f"=== EVENT {idx}/{n}: {label} — complete ALL steps below, emit "
                        f"AUDIT_RESULT, then immediately start event "
                        f"{idx + 1 if idx < n else 'DONE'}. Keep browser open. ==="
                    ),
                }
            )
        if extra:
            out.append({"op": op, "touchpoint": touch, "step": f"Hint: {extra}"})
        out.extend(recipe_for(op, touch, label=label))
    if n > 1:
        out.append(
            {
                "op": "",
                "touchpoint": "",
                "step": (
                    f"After all {n} AUDIT_RESULT lines are emitted, close the browser. "
                    "One AUDIT_RESULT per selected event (helpers optional)."
                ),
            }
        )
    return out


def compact_checklist(selection: list[dict[str, Any]]) -> list[str]:
    """Ultra-short checklist lines for multi-event CasePilot context."""
    lines: list[str] = []
    for i, s in enumerate([x for x in selection if isinstance(x, dict)], 1):
        op = str(s.get("operation") or "").strip()
        touch = str(s.get("touchpoint") or "").strip()
        ts = short_touch(touch)
        label = str(s.get("label") or f"{op}({ts})")
        lines.append(
            f"{i}. {label} → fire {op} → "
            f"AUDIT_RESULT|operation={op}|correlation_id=<uuid>|touchpoint={ts}"
        )
    return lines


def testrail_steps_separated(op: str, touch: str, *, label: str = "") -> list[dict[str, str]]:
    """TestRail custom_steps_separated payload from recipe."""
    rows = recipe_for(op, touch, label=label)
    expected = (
        f"GraphQL {op} fires; response has correlation-id; "
        f"AUDIT_RESULT emitted with real UUID; raw+enrich visible in Generation Status."
    )
    out: list[dict[str, str]] = []
    for r in rows:
        out.append({"content": r["step"], "expected": expected if "AUDIT_RESULT" in r["step"] else ""})
    if out and "AUDIT_RESULT" not in out[-1]["content"]:
        out.append({"content": audit_emit(op, short_touch(touch)), "expected": expected})
    return out


def testrail_steps_text(op: str, touch: str, *, label: str = "") -> str:
    rows = recipe_for(op, touch, label=label)
    lines = [f"{i}. {r['step']}" for i, r in enumerate(rows, 1)]
    lines.append(
        "Expected: Mutation fires; AUDIT_RESULT with real correlation-id; "
        "raw+enrich visible in Generation Status."
    )
    return "\n".join(lines)
