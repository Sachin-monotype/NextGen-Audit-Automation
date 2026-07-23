"""CasePilot / TestRail UI steps — plain-English actions for AI execution.

Each recipe describes what to do in natural language (like C73306718 / C73306719),
then a final step to capture GraphQL correlation-id and emit AUDIT_RESULT.

Login is handled via TestRail preconditions — recipes focus on the user action only.
"""

from __future__ import annotations

from typing import Any

from .export_ui_catalog import export_spec, export_touchpoint


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


def _capture(op: str, touch_short: str) -> dict[str, str]:
    return _row(audit_emit(op, touch_short), audit_expected(op))


def _where(ts: str, mapping: dict[str, str], default: str = "the UI") -> str:
    return mapping.get(ts, default)


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

    # ── activateFamily — plain English (matches C73306719 / C73306718) ──
    if op == "activateFamily" and ts == "global":
        return _S(
            _row(
                "Perform activate family from search page for a family which is not activated. "
                "Capture the GraphQL ActivateFamily mutation from network and provide family id "
                "from the request variables.",
                "Family should be activated and GraphQL call captured with family ids.",
            ),
            _row(audit_emit(op, "global"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )

    if op == "activateFamily" and ts == "favourite":
        return _S(
            _row(
                "Search for a style and mark favourite for a non-activated family.",
                "Non-activated family should be marked as favourite.",
            ),
            _row(
                "Go to favourites and activate the same style. Capture family id from "
                "ActivateFamily network mutation and response header correlation-id.",
                "Family should be activated; family id and correlation-id captured.",
            ),
            _row(audit_emit(op, "favourite"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )

    if op == "activateFamily" and ts == "list":
        return _S(
            _row(
                "Open My Library and open a font list (create a list and add a family if needed).",
                "Font list is open with at least one family.",
            ),
            _row(
                "Activate a non-activated family in that list. Capture ActivateFamily GraphQL "
                "mutation with family id and correlation-id header.",
                "Family activated in list; mutation and correlation-id captured.",
            ),
            _row(audit_emit(op, "list"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )

    if op == "activateFamily" and ts == "project":
        return _S(
            _row(
                "Open a project fonts page (/projects/library/{projectId}/fonts). "
                "Reuse an existing project or create one and add fonts if empty.",
                "Project fonts grid is visible with at least one family.",
            ),
            _row(
                "Activate a non-activated family on the project fonts grid. Capture "
                "ActivateFamily GraphQL mutation with family id and correlation-id header.",
                "Family activated in project; mutation and correlation-id captured.",
            ),
            _row(audit_emit(op, "project"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )

    if op == "activateFamily" and ts == "project_list":
        return _S(
            _row(
                "Open a project, go to Project Library, and open a project font list "
                "(create list and add a family if needed).",
                "Project font list grid is open with at least one family.",
            ),
            _row(
                "Activate a non-activated family in that project list. Capture ActivateFamily "
                "GraphQL mutation with family id and correlation-id header.",
                "Family activated in project list; mutation and correlation-id captured.",
            ),
            _row(audit_emit(op, "project_list"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )

    if op == "deactivateFamilies":
        where = {
            "global": "from the search page",
            "favourite": "from favourites",
            "list": "from a font list in My Library",
            "project": "from a project fonts page",
            "project_list": "from a project font list",
        }.get(ts, "from the UI")
        return _S(
            _row(
                f"Deactivate an activated family {where}. Capture DeactivateFamilies GraphQL "
                "mutation with family id(s) and correlation-id header.",
                "Family deactivated; DeactivateFamilies mutation and correlation-id captured.",
            ),
            _row(audit_emit(op, ts), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )

    if op in {"activateStyle", "deactivateStyle"}:
        act = "Activate" if op == "activateStyle" else "Deactivate"
        where = {
            "global": "search page",
            "favourite": "favourites (mark favourite first if needed)",
            "list": "a font list",
            "project": "a project fonts page",
            "project_list": "a project font list",
        }.get(ts, "the UI")
        return _S(
            _row(
                f"{act} a single font style from {where}. Capture {op} GraphQL mutation "
                "with style id and correlation-id header.",
                f"Style {act.lower()}d; {op} mutation and correlation-id captured.",
            ),
            _row(audit_emit(op, ts), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )

    if op in {"activateVariation", "deactivateVariation"}:
        act = "Activate" if op == "activateVariation" else "Deactivate"
        where = {
            "global": "search page (open font versions drawer from card kebab)",
            "favourite": "favourites",
            "list": "a font list",
            "project": "a project fonts page",
            "project_list": "a project font list",
        }.get(ts, "the UI")
        return _S(
            _row(
                f"{act} a non-default font variation from {where}. Capture {op} GraphQL "
                "mutation with variation/style id and correlation-id header.",
                f"Variation {act.lower()}d; {op} mutation and correlation-id captured.",
            ),
            _row(audit_emit(op, ts), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )

    if op in {"activateList", "deActivateList"}:
        act = "Activate" if op == "activateList" else "Deactivate"
        where = "a project font list" if ts == "project_list" else "a font list in My Library"
        return _S(
            _row(
                f"Open {where}, ensure fonts in the list are active, then use the list toolbar "
                f"'{act} list' action (kebab/menu on the list itself — NOT bulk deactivate fonts "
                f"which fires deactivateFamilies). Capture {op} GraphQL mutation and correlation-id.",
                f"List {act.lower()}d via toolbar; {op} mutation and correlation-id captured.",
            ),
            _row(
                audit_emit(op, "project_list" if ts == "project_list" else "list"),
                audit_expected(op),
            ),
            op=op,
            touch=touch_canon,
        )

    if op in {"activateFontProject", "deActivateFontProject"}:
        act = "Activate" if op == "activateFontProject" else "Deactivate"
        return _S(
            _row(
                f"On a project fonts page select all families and {act.lower()} them using "
                f"bulk {act.lower()}. Capture {op} GraphQL mutation (or activateFamily if that "
                "fires instead) with family ids and correlation-id header.",
                f"Project fonts {act.lower()}d; mutation and correlation-id captured.",
            ),
            _row(audit_emit(op, "project"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )

    if op in {"bulkActivateStyles", "bulkDeactivateStyles"}:
        act = "Activate" if op == "bulkActivateStyles" else "Deactivate"
        where = "a project fonts page" if ts == "project" else "search page"
        return _S(
            _row(
                f"On {where} select multiple font cards and bulk {act.lower()} styles. "
                f"Capture {op} GraphQL mutation and correlation-id header.",
                f"Bulk style {act.lower()} completed; mutation and correlation-id captured.",
            ),
            _row(audit_emit(op, ts if ts in {"global", "project"} else "global"), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )

    if op in {"bulkActivateLists", "bulkDeactivateLists"}:
        act = "Activate" if op == "bulkActivateLists" else "Deactivate"
        where = (
            "Project Library assets"
            if ts in {"project", "project_list"}
            else "My Library assets table"
        )
        return _S(
            _row(
                f"On {where} select multiple FontList rows and bulk {act.lower()} them. "
                f"Capture {op} GraphQL mutation and correlation-id header.",
                f"Bulk list {act.lower()} completed; mutation and correlation-id captured.",
            ),
            _row(audit_emit(op, "list" if ts == "list" else ts), audit_expected(op)),
            op=op,
            touch=touch_canon,
        )

    if op == "pinAsset":
        return _S(
            _row(
                "Open My Library or Search lists view and pin an unpinned folder or font list "
                "to the sidebar.",
                "Folder or list is pinned and visible in the sidebar.",
            ),
            _capture(op, "global"),
            op=op,
            touch=touch_canon,
        )
    if op == "unpinAsset":
        return _S(
            _row(
                "Open a pinned folder or font list and unpin it from the sidebar or library view.",
                "Asset is unpinned successfully.",
            ),
            _capture(op, "global"),
            op=op,
            touch=touch_canon,
        )
    if op == "updateAssets":
        return _S(
            _row(
                "From search or Webkits inventory, locate font assets with online/offline toggle "
                "(My Library / Project Library Webkits tab).",
                "Asset row with Webkits status control is visible.",
            ),
            _row(
                "Toggle one or more assets online/offline (bulk update if multi-select available).",
                "Assets updated; updateAssets mutation captured.",
            ),
            _capture(op, "global"),
            op=op,
            touch=touch_canon,
        )

    if op == "addFavoriteFamilies":
        emit = "favourite" if ts == "favourite" else "global"
        return _S(
            _row(
                "From search, add a font family to favourites using the heart icon or "
                "card context menu.",
                "Family appears in favourites; addFavoriteFamilies mutation captured.",
            ),
            _capture(op, emit),
            op=op,
            touch=touch_canon,
        )
    if op == "removeFavoriteFamilies":
        return _S(
            _row(
                "Go to favourites and remove a favourited family using the heart icon.",
                "Family removed from favourites; removeFavoriteFamilies mutation captured.",
            ),
            _capture(op, "favourite"),
            op=op,
            touch=touch_canon,
        )
    if op == "addFavoriteStyles":
        return _S(
            _row(
                "From search or family detail, favourite a single font style.",
                "Style added to favourites; addFavoriteStyles mutation captured.",
            ),
            _capture(op, ts),
            op=op,
            touch=touch_canon,
        )
    if op == "removeFavoriteStyles":
        return _S(
            _row(
                "Go to favourites (or open a favourited style) and remove favourite from one style.",
                "Style removed from favourites; removeFavoriteStyles mutation captured.",
            ),
            _capture(op, "favourite"),
            op=op,
            touch=touch_canon,
        )
    if op == "addFavoritePair":
        return _S(
            _row(
                "From search, open font pairings for a family and favourite one pairing.",
                "Pair added to favourites; addFavoritePair mutation captured.",
            ),
            _capture(op, ts),
            op=op,
            touch=touch_canon,
        )
    if op == "removeFavoritePair":
        return _S(
            _row(
                "Open favourites pairs page and remove favourite from one font pair.",
                "Pair removed from favourites; removeFavoritePair mutation captured.",
            ),
            _capture(op, "favourite"),
            op=op,
            touch=touch_canon,
        )

    if op == "addFontListFamilies":
        emit = "project_list" if ts == "project_list" else ("list" if ts == "list" else ts)
        where = _where(
            ts,
            {
                "list": "a font list in My Library",
                "project_list": "a project font list",
            },
            "a font list",
        )
        return _S(
            _row(
                f"Add a font family from search into {where}.",
                "Family added to list; addFontListFamilies mutation captured.",
            ),
            _capture(op, emit if emit in {"list", "project_list"} else "list"),
            op=op,
            touch=touch_canon,
        )
    if op == "addFontListStyles":
        emit = "project_list" if ts == "project_list" else "list"
        if ts == "project_list":
            return _S(
                _row(
                    "Open a project, go to Project Library, and open a project font list "
                    "(create project + list and add a family if needed).",
                    "Project font list is open.",
                ),
                _row(
                    "From search, add a font style into that project font list.",
                    "Style added to project list; addFontListStyles mutation captured.",
                ),
                _capture(op, emit),
                op=op,
                touch=touch_canon,
            )
        where = _where(
            ts,
            {"list": "a font list in My Library"},
            "a font list in My Library",
        )
        return _S(
            _row(
                f"Open My Library and open {where} (create a list and add a family if needed).",
                "Font list is open with at least one family.",
            ),
            _row(
                "From search, add a font style into that list.",
                "Style added to list; addFontListStyles mutation captured.",
            ),
            _capture(op, emit),
            op=op,
            touch=touch_canon,
        )
    if op == "addFontProjectFamilies":
        return _S(
            _row(
                "Open a project fonts page (/projects/library/{projectId}/fonts). "
                "Reuse an existing project or create one if needed.",
                "Project fonts grid is visible.",
            ),
            _row(
                "From search or project browse inventory, add a font family to the project.",
                "Family added to project; addFontProjectFamilies mutation captured.",
            ),
            _capture(op, "project"),
            op=op,
            touch=touch_canon,
        )
    if op == "addFontProjectStyles":
        return _S(
            _row(
                "Add a font style to an existing project (from project browse inventory or search).",
                "Style added to project; addFontProjectStyles mutation captured.",
            ),
            _capture(op, "project"),
            op=op,
            touch=touch_canon,
        )
    if op == "removeFontProjectStyles":
        return _S(
            _row(
                "Open a project fonts page with at least one style "
                "(reuse project or addFontProjectStyles first if empty).",
                "Project fonts grid shows at least one style.",
            ),
            _row(
                "Remove one style from the project (kebab menu or bulk remove on grid).",
                "Style removed from project; removeFontProjectStyles mutation captured.",
            ),
            _capture(op, "project"),
            op=op,
            touch=touch_canon,
        )

    if op == "addStylesToWebProject":
        return _S(
            _row(
                "Open or create a project, then open its web/embed fonts flow "
                "(My Library embed-all-fonts drawer or project web fonts UI).",
                "Web project fonts panel is open.",
            ),
            _row(
                "From search, add one or more font styles to the web project.",
                "Styles added to web project; addStylesToWebProject mutation captured.",
            ),
            _capture(op, "project"),
            op=op,
            touch=touch_canon,
        )
    if op == "removeStylesFromWebProject":
        return _S(
            _row(
                "Open a project web/embed fonts view with styles already added.",
                "Web project shows at least one style.",
            ),
            _row(
                "Select and remove one or more styles from the web project.",
                "Styles removed; removeStylesFromWebProject mutation captured.",
            ),
            _capture(op, "project"),
            op=op,
            touch=touch_canon,
        )
    if op == "createWebProject":
        return _S(
            _row(
                "Create a new project using the project creation wizard (or reuse an empty project).",
                "Project exists and is open.",
            ),
            _row(
                "From My Library, open the embed-all-fonts / web project drawer and create a web project.",
                "Web project created; createWebProject mutation captured.",
            ),
            _capture(op, "project" if ts == "project" else "global"),
            op=op,
            touch=touch_canon,
        )
    if op == "deleteProject":
        return _S(
            _row(
                "Create a disposable project or open an existing test project you may delete.",
                "Project detail page is open.",
            ),
            _row(
                "Delete the project from the project menu / settings.",
                "Project deleted; deleteProject mutation captured.",
            ),
            _capture(op, "project" if ts == "project" else "global"),
            op=op,
            touch=touch_canon,
        )
    if op == "publishProject":
        return _S(
            _row(
                "Open a project that can be published (create one if needed).",
                "Project detail is open.",
            ),
            _row(
                "Open the Publish drawer and publish the project.",
                "Project published; publishProject mutation captured.",
            ),
            _capture(op, "project"),
            op=op,
            touch=touch_canon,
        )
    if op == "linkDocumentToProject":
        return _S(
            _row(
                "Open a project (create one if needed) and open the document linking side panel.",
                "Project linking UI is visible.",
            ),
            _row(
                "Link an existing document to the project and save.",
                "Document linked; linkDocumentToProject mutation captured.",
            ),
            _capture(op, "project"),
            op=op,
            touch=touch_canon,
        )
    if op == "removeFontListFamilies":
        emit = "list" if ts in {"list", "library_assets"} else ts
        return _S(
            _row(
                "Open My Library and a font list that already has families "
                "(create list + addFontListFamilies first if empty).",
                "Font list grid shows at least one family.",
            ),
            _row(
                "Remove one font family from that list.",
                "Family removed from list; removeFontListFamilies mutation captured.",
            ),
            _capture(op, emit if emit in {"list", "global"} else "list"),
            op=op,
            touch=touch_canon,
        )
    if op == "removeFontListStyles":
        emit = "list" if ts in {"list", "library_assets"} else ts
        return _S(
            _row(
                "Open My Library and a font list that already has styles "
                "(create list + addFontListStyles first if empty).",
                "Font list grid shows at least one style.",
            ),
            _row(
                "Remove one font style from that list.",
                "Style removed from list; removeFontListStyles mutation captured.",
            ),
            _capture(op, emit if emit in {"list", "global"} else "list"),
            op=op,
            touch=touch_canon,
        )
    if op == "removeFontProjectFamilies":
        return _S(
            _row(
                "Open a project fonts page with families "
                "(create project + addFontProjectFamilies first if empty).",
                "Project fonts grid shows at least one family.",
            ),
            _row(
                "Remove one font family from the project.",
                "Family removed from project; removeFontProjectFamilies mutation captured.",
            ),
            _capture(op, "project"),
            op=op,
            touch=touch_canon,
        )
    if op == "deleteAssets":
        return _S(
            _row(
                "Open My Library and ensure at least one asset exists "
                "(createAsset first if the library is empty).",
                "At least one deletable asset is visible.",
            ),
            _row(
                "Select the asset and delete it (confirm in the delete dialog).",
                "Asset deleted; deleteAssets mutation captured.",
            ),
            _capture(op, "global"),
            op=op,
            touch=touch_canon,
        )
    if op == "updateAsset":
        emit = "library_assets" if ts == "library_assets" else ("list" if ts == "list" else "global")
        return _S(
            _row(
                "Open My Library and select an existing asset (createAsset first if needed).",
                "Asset row or detail is open.",
            ),
            _row(
                "Edit asset details (rename or Edit Details) and save.",
                "Asset updated; updateAsset mutation captured.",
            ),
            _capture(op, emit),
            op=op,
            touch=touch_canon,
        )
    if op == "updateAssetSharing":
        return _S(
            _row(
                "Open My Library, open a shareable asset or list, and open the Share panel.",
                "Share / access panel is visible.",
            ),
            _row(
                "Grant or revoke sharing for a user/team and save.",
                "Sharing updated; updateAssetSharing mutation captured.",
            ),
            _capture(op, "global"),
            op=op,
            touch=touch_canon,
        )

    if op == "bulkAddStylesToList":
        return _S(
            _row(
                "From search, multi-select several font cards (checkboxes on card grid).",
                "Multiple styles are selected.",
            ),
            _row(
                "Add the selected styles to a font list (pick list from Add to list menu).",
                "Styles added to list; bulkAddStylesToList mutation captured.",
            ),
            _capture(op, "list"),
            op=op,
            touch=touch_canon,
        )
    if op == "bulkRemoveStylesFromList":
        return _S(
            _row(
                "Open a font list that already contains styles "
                "(bulkAddStylesToList or addFontListStyles first if empty).",
                "List grid shows multiple styles.",
            ),
            _row(
                "Multi-select styles in the list and remove them from that list.",
                "Styles removed from list; bulkRemoveStylesFromList mutation captured.",
            ),
            _capture(op, "list" if ts != "library_assets" else "library_assets"),
            op=op,
            touch=touch_canon,
        )
    if op == "bulkRemoveStylesFromFavourites":
        return _S(
            _row(
                "From favourites, select multiple items and bulk remove them from favourites.",
                "Items removed from favourites; bulkRemoveStylesFromFavourites mutation captured.",
            ),
            _capture(op, "favourite"),
            op=op,
            touch=touch_canon,
        )
    if op == "bulkCopyAssets":
        emit = ts if ts in {"list", "project", "library_assets", "global"} else "library_assets"
        where = _where(
            ts,
            {
                "project": "Project Library",
                "list": "My Library",
                "library_assets": "My Library assets",
            },
            "My Library",
        )
        return _S(
            _row(
                f"In {where}, ensure assets exist (createAsset / create folder if needed).",
                "At least one asset is visible.",
            ),
            _row(
                "Select one or more assets and copy them to another folder or list.",
                "Assets copied; bulkCopyAssets mutation captured.",
            ),
            _capture(op, emit),
            op=op,
            touch=touch_canon,
        )
    if op == "bulkMoveAssets":
        emit = ts if ts in {"list", "project", "library_assets", "global"} else "library_assets"
        where = _where(
            ts,
            {
                "project": "Project Library",
                "list": "My Library",
                "library_assets": "My Library assets",
            },
            "My Library",
        )
        return _S(
            _row(
                f"In {where}, ensure assets exist (createAsset / create folder if needed).",
                "At least one asset is visible.",
            ),
            _row(
                "Select one or more assets and move them to another folder or list.",
                "Assets moved; bulkMoveAssets mutation captured.",
            ),
            _capture(op, emit),
            op=op,
            touch=touch_canon,
        )
    if op == "bulkTagStyles":
        return _S(
            _row(
                "From search, select multiple font cards and tag them with an existing or new tag.",
                "Styles tagged; bulkTagStyles mutation captured.",
            ),
            _capture(op, ts if ts == "manage_tags" else "global"),
            op=op,
            touch=touch_canon,
        )
    if op == "bulkUntagStyles":
        return _S(
            _row(
                "Open Manage Tags, select a tag, and untag one or more fonts from it.",
                "Styles untagged; bulkUntagStyles mutation captured.",
            ),
            _capture(op, "manage_tags"),
            op=op,
            touch=touch_canon,
        )
    if op == "updatePrivateTag":
        return _S(
            _row(
                "As company admin, open Manage Tags, edit an existing private tag name "
                "(append a short suffix to keep it unique), and save.",
                "Tag renamed; updatePrivateTag mutation captured.",
            ),
            _capture(op, "manage_tags"),
            op=op,
            touch=touch_canon,
        )
    if op == "updatePrivateTagAssociations":
        return _S(
            _row(
                "Open Manage Tags, configure a tag, and add or remove font associations, then apply.",
                "Tag associations updated; updatePrivateTagAssociations mutation captured.",
            ),
            _capture(op, "manage_tags"),
            op=op,
            touch=touch_canon,
        )

    if op == "createAsset":
        if ts in {"project", "project_list"}:
            return _S(
                _row(
                    "Open a project library and create a new font list or folder.",
                    "Project asset created; createAsset mutation captured.",
                ),
                _capture(op, ts),
                op=op,
                touch=touch_canon,
            )
        where = "My Library" if ts == "list" else "library"
        return _S(
            _row(
                f"Open {where} and create a new font list or folder.",
                "Asset created; createAsset mutation captured.",
            ),
            _capture(op, "list" if ts == "list" else ts),
            op=op,
            touch=touch_canon,
        )
    if op == "createProject":
        return _S(
            _row(
                "Create a new project using the project creation wizard.",
                "Project created; createProject mutation captured.",
            ),
            _capture(op, "project" if ts == "project" else "global"),
            op=op,
            touch=touch_canon,
        )
    if op == "duplicateProject":
        return _S(
            _row(
                "Open an existing project and duplicate it from the project menu.",
                "Project duplicated; capture the actual GraphQL operation from network.",
            ),
            _capture(op, "project"),
            op=op,
            touch=touch_canon,
        )

    if op == "dismissNotification":
        return _S(
            _row(
                "Open notifications and dismiss one notification.",
                "Notification dismissed; dismissNotification mutation captured.",
            ),
            _capture(op, "notifications"),
            op=op,
            touch=touch_canon,
        )
    if op == "markNotificationRead":
        return _S(
            _row(
                "Open notifications and mark one notification as read (or mark all read).",
                "Notification marked read; markNotificationRead mutation captured.",
            ),
            _capture(op, "notifications"),
            op=op,
            touch=touch_canon,
        )

    if op == "setLanguagePreference":
        return _S(
            _row(
                "Open user preferences and change the display language to a different language.",
                "Language preference saved; setLanguagePreference mutation captured.",
            ),
            _capture(op, "preferences"),
            op=op,
            touch=touch_canon,
        )
    if op == "updateCustomerSettings":
        return _S(
            _row(
                "Open company settings, change a setting, and save.",
                "Customer settings updated; updateCustomerSettings mutation captured.",
            ),
            _capture(op, "account"),
            op=op,
            touch=touch_canon,
        )
    if op == "getCustomerSettings":
        return _S(
            _row(
                "Navigate to company settings page and capture the getCustomerSettings query "
                "that loads on page open.",
                "getCustomerSettings query captured with correlation-id.",
            ),
            _capture(op, "account"),
            op=op,
            touch=touch_canon,
        )
    if op == "createUserInvitations":
        return _S(
            _row(
                "Open Manage Users & Teams, invite a new user by email, and send the invitation.",
                "Invitation sent; createUserInvitations mutation captured.",
            ),
            _capture(op, "user_access"),
            op=op,
            touch=touch_canon,
        )
    if op == "createRole":
        return _S(
            _row(
                "As company admin, open Manage Users & Teams Roles tab and create a new role "
                "with a unique name (default permissions, no users assigned).",
                "Role created; createRole mutation captured.",
            ),
            _capture(op, "user_access"),
            op=op,
            touch=touch_canon,
        )
    if op == "createPrivateTags":
        emit_ts = "global" if ts in {"", "global"} else ts
        return _S(
            _row(
                "As company admin, open Manage Tags and create one new private tag with a unique name.",
                "Tag created; createPrivateTags mutation captured.",
            ),
            _capture(op, emit_ts),
            op=op,
            touch=touch_canon,
        )
    if op == "deleteRoles":
        return _S(
            _row(
                "Open Manage Users & Teams Roles and delete an existing custom role.",
                "Role deleted; deleteRoles mutation captured.",
            ),
            _capture(op, "user_access"),
            op=op,
            touch=touch_canon,
        )
    if op == "deleteTeams":
        return _S(
            _row(
                "Open Manage Users & Teams Teams tab and delete an existing team.",
                "Team deleted; deleteTeams mutation captured.",
            ),
            _capture(op, "user_access"),
            op=op,
            touch=touch_canon,
        )
    if op == "createServiceAccount":
        return _S(
            _row(
                "Open Manage Servers and create a new service account.",
                "Service account created; createServiceAccount mutation captured.",
            ),
            _capture(op, "user_access"),
            op=op,
            touch=touch_canon,
        )
    if op == "bulkUpdateProfiles":
        return _S(
            _row(
                "Open Manage Users, select multiple users, and bulk update their roles or profiles.",
                "Profiles updated; bulkUpdateProfiles mutation captured.",
            ),
            _capture(op, "user_access"),
            op=op,
            touch=touch_canon,
        )

    if op == "processUploadSessionFonts":
        return _S(
            _row(
                "Use document scan to upload a font file and process the upload session.",
                "Fonts processed; processUploadSessionFonts mutation captured.",
            ),
            _capture(op, "global"),
            op=op,
            touch=touch_canon,
        )
    if op == "updateSessionFiles":
        return _S(
            _row(
                "Open document scan with uploaded files and remove one file from the session.",
                "Session file updated; updateSessionFiles mutation captured.",
            ),
            _capture(op, "global"),
            op=op,
            touch=touch_canon,
        )
    if op == "addStyleDocument":
        return _S(
            _row(
                "Open Manage Imported Fonts, select a font, and upload a document for that style.",
                "Document uploaded; addStyleDocument mutation captured.",
            ),
            _capture(op, "global"),
            op=op,
            touch=touch_canon,
        )
    if op == "submitIntentForProduction":
        return _S(
            _row(
                "From search, request a font for production using the production request flow.",
                "Production intent submitted; submitIntentForProduction mutation captured.",
            ),
            _capture(op, "global"),
            op=op,
            touch=touch_canon,
        )
    if op == "parseAndCreateContract":
        return _S(
            _row(
                "Open Manage Imported Fonts Licenses and upload a license file to parse and create contract.",
                "Contract parsed and created; parseAndCreateContract mutation captured.",
            ),
            _capture(op, "global"),
            op=op,
            touch=touch_canon,
        )
    if op == "createContract":
        return _S(
            _row(
                "Open Manage Imported Fonts Licenses and manually create a new license contract.",
                "Contract created; createContract mutation captured.",
            ),
            _capture(op, "global"),
            op=op,
            touch=touch_canon,
        )
    if op == "cancelBatch":
        return _S(
            _row(
                "Start a bulk operation that shows a cancel option, then cancel the batch. "
                "If no cancel control exists in web UI, stop and report WEB GAP.",
                "Batch cancelled or WEB GAP documented; cancelBatch mutation captured if fired.",
            ),
            _capture(op, ts),
            op=op,
            touch=touch_canon,
        )
    if op == "syncUnSyncVariations":
        return _S(
            _row(
                "Sync or unsync font variations. Note: primary flow may be desktop Connect only; "
                "use web font-versions UI if available, else stop and report WEB GAP.",
                "Variation sync state changed or WEB GAP documented.",
            ),
            _capture(op, "global"),
            op=op,
            touch=touch_canon,
        )
    if op == "exportFontTemplate":
        return _S(
            _row(
                "On Imported Fonts page open export/template flow and export a font template CSV. "
                "Capture exportFontTemplate GraphQL mutation and correlation-id header.",
                "Template export completed; exportFontTemplate mutation and correlation-id captured.",
            ),
            _capture(op, "global"),
            op=op,
            touch=touch_canon,
        )
    if op == "exportUnassignedImportedFontsTemplate":
        return _S(
            _row(
                "On Imported Fonts page export unassigned fonts template. "
                "Capture exportUnassignedImportedFontsTemplate mutation and correlation-id.",
                "Unassigned template export completed; mutation and correlation-id captured.",
            ),
            _capture(op, "global"),
            op=op,
            touch=touch_canon,
        )

    export_meta = export_spec(op)
    if export_meta:
        touch_canon = "Discovery/Browse (global)"
        ts = "global"
        button = str(export_meta.get("button") or "Export")
        ui_steps = export_meta.get("steps") or []
        rows: list[dict[str, str]] = []
        for step in ui_steps[:3]:
            rows.append(_row(str(step), f"Page loaded; {button} is visible when applicable."))
        op_name = op[0].upper() + op[1:] if op else op
        rows.append(
            _row(
                f"Network filter operationName={op_name} → copy response header "
                f"correlation-id (NOT x-correlation-id) → emit AUDIT_RESULT.",
                audit_expected(op),
            )
        )
        return _S(*rows, op=op, touch=touch_canon)

    return _S(
        _row(
            f"Perform {op} from {_where(ts, {'global': 'search', 'favourite': 'favourites', 'list': 'a font list', 'project': 'a project', 'project_list': 'a project list'}, touch_canon)}. "
            f"Capture the GraphQL {op} mutation and correlation-id header.",
            f"{op} completed; mutation and correlation-id captured.",
        ),
        _capture(op, ts),
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
                        f"=== EVENT {idx}/{n}: {label} — perform the steps below, capture "
                        f"correlation-id and emit AUDIT_RESULT, then continue to event "
                        f"{idx + 1 if idx < n else 'DONE'}. NO RETRIES. ==="
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
