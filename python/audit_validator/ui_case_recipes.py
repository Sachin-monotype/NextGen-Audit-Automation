"""Deterministic CasePilot UI recipes (mtconnect-ui routes + qa-ids).

CasePilot is driven primarily by these steps (``prefer_steps=context_over_testrail``),
not by TestRail prose. Keep recipes short, scoped, and free of "wander the UI".
"""

from __future__ import annotations

import os
from typing import Any


def _seed_hint() -> str:
    """Dynamic seed guidance — prefer env, else any searchable family (no hard name)."""
    env = (
        os.getenv("SEED_FAMILY_ID", "").strip()
        or os.getenv("TOUCHPOINT_FAMILY_ID", "").strip()
        or os.getenv("CASEPILOT_SEED_FAMILY_ID", "").strip()
    )
    if env:
        return (
            f"In Search, look up family id {env}. If not found, pick any other "
            "deactivated family from Search results."
        )
    try:
        from audit_validator.live_seeds import KNOWN_FAMILY_POOL

        examples = ", ".join(KNOWN_FAMILY_POOL[:4])
    except Exception:  # noqa: BLE001
        examples = "910042901, 910052505"
    return (
        "In Search (/search), pick ANY deactivated family visible in results "
        f"(optional examples if search is empty: {examples}). "
        "Do not require a specific family name — reuse whatever is already on screen when possible."
    )


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
        f"DevTools → Network → filter operationName={op} (ignore search/browse queries). "
        f"Copy response header correlation-id (NOT x-correlation-id). Emit exactly: "
        f"AUDIT_RESULT|operation={op}|correlation_id=<real-uuid>|touchpoint={touch_short}"
    )


def _close() -> str:
    return (
        "Stop. Close the browser. Do not open family detail, new tabs, or unrelated menus "
        "after AUDIT_RESULT."
    )


def _nav_search() -> str:
    return (
        "Sidebar → Search → URL /search. Ensure sort is font families / fonts "
        "(NOT lists & folders)."
    )


def _no_family_detail() -> str:
    return (
        "Stay on the search/grid card. Do NOT open family detail (/family/…). "
        "Do NOT use 'Open in new tab'."
    )


def _steps(*texts: str, op: str, touch: str) -> list[dict[str, str]]:
    return [{"op": op, "touchpoint": touch, "step": t} for t in texts if t]


def recipe_for(op: str, touch: str, *, label: str = "") -> list[dict[str, str]]:
    """Return ordered UI steps for one Generate catalog scenario."""
    op = (op or "").strip()
    touch = (touch or "").strip()
    touch_short = short_touch(touch)
    touch_canon = touch or {
        "global": "Discovery/Browse (global)",
        "list": "List (FONTLIST)",
        "favourite": "Favourite",
        "project": "Project",
        "project_list": "Project > List",
        "user_access": "User & Access",
        "account": "Account & workspace",
        "preferences": "Preferences",
        "notifications": "Notifications",
        "manage_tags": "Manage>Tags",
        "library_assets": "Mylibrary>Assets",
    }.get(touch_short, touch)
    seed = _seed_hint()
    label = label or f"{op}({touch_short})"

    # ── activateFamily ──────────────────────────────────────────────
    if op == "activateFamily" and touch_short == "global":
        return _steps(
            f"{_nav_search()} {seed}",
            f"{_no_family_detail()} If card toggle is ON, click data-qa-id=toggle-btn to deactivate; wait for success.",
            "Click toggle-btn to Activate (global — mutation must omit listType/listIds/projectId).",
            audit_emit(op, "global"),
            _close(),
            op=op,
            touch=touch_canon,
        )
    if op == "activateFamily" and touch_short == "favourite":
        return _steps(
            f"{_nav_search()} {seed} Add to Favourites (heart) if missing.",
            "Sidebar → MY LIBRARY → Favourites (/library/favourites/fonts). Stay on favourites grid.",
            "Deactivate via toggle-btn if needed, then Activate (listType=Favorite, no listIds).",
            audit_emit(op, "favourite")
            + " Also emit AUDIT_RESULT for addFavoriteFamilies if it ran.",
            _close(),
            op=op,
            touch=touch_canon,
        )
    if op == "activateFamily" and touch_short == "list":
        return _steps(
            "Create or open a My Library FontList only (/library or Search → lists & folders). Not inside a project.",
            f"{seed} Add that family to the list (addFontListFamilies). Open /library/FontList/{{assetId}}.",
            "From the LIST grid, deactivate if needed, then Activate family "
            "(listType=Fontlist + listIds — no projectId).",
            "Emit AUDIT_RESULT for createAsset/addFontListFamilies if run, then "
            + audit_emit(op, "list"),
            _close(),
            op=op,
            touch=touch_canon,
        )
    if op == "activateFamily" and touch_short == "project":
        return _steps(
            "Go to /projects → create or open a project → /projects/library/{projectId}/fonts.",
            f"{seed} Add family to the project (addFontProjectFamilies).",
            "On Project fonts grid, deactivate if needed, then Activate "
            "(listType=Fontproject + projectId). Do not use global Search toggle.",
            "Emit helper AUDIT_RESULT lines if run, then " + audit_emit(op, "project"),
            _close(),
            op=op,
            touch=touch_canon,
        )
    if op == "activateFamily" and touch_short == "project_list":
        return _steps(
            "CRITICAL: Project > List ≠ List alone. Create/open PROJECT first.",
            f"Add family to project, then create a FontList INSIDE that project and add the same family.",
            "Open /projects/library/FontList/{listAssetId}. Activate from THIS grid so mutation has BOTH projectId AND listIds.",
            "Emit helpers (createProject, addFontProjectFamilies, createAsset, addFontListFamilies), then "
            + audit_emit(op, "project_list"),
            "Close browser. Never activate from global Search for this scenario.",
            op=op,
            touch=touch_canon,
        )

    # ── deactivateFamilies (mirror activateFamily scopes) ───────────
    if op == "deactivateFamilies":
        if touch_short == "global":
            return _steps(
                f"{_nav_search()} {seed} {_no_family_detail()}",
                "If deactivated, Activate once first, then Deactivate via toggle-btn.",
                audit_emit(op, "global"),
                _close(),
                op=op,
                touch=touch_canon,
            )
        return _steps(
            f"Navigate to {touch_canon} surface (same path as activateFamily for this touchpoint). {seed}",
            "Ensure family is activated in THIS scope, then Deactivate from that same scope.",
            audit_emit(op, touch_short),
            _close(),
            op=op,
            touch=touch_canon,
        )

    # ── activateStyle / activateVariation ───────────────────────────
    if op in {"activateStyle", "deactivateStyle"}:
        return _steps(
            f"{_nav_search()} {seed}",
            "Open family detail (/family/{slug}) OR style row via card right-click — required for style ops.",
            (
                "Activate ONE style (not whole family) from Styles tab / Activate style menu."
                if op == "activateStyle"
                else "Deactivate ONE style that is currently active."
            )
            + f" Scope must match touchpoint={touch_short}.",
            audit_emit(op, touch_short),
            _close(),
            op=op,
            touch=touch_canon,
        )
    if op in {"activateVariation", "deactivateVariation"}:
        return _steps(
            f"{_nav_search()} {seed}",
            "Open family detail → select a style → More actions → Font versions drawer.",
            (
                "Activate a non-default variation row."
                if op == "activateVariation"
                else "Deactivate an active non-default variation."
            )
            + f" Scope touchpoint={touch_short}.",
            audit_emit(op, touch_short),
            _close(),
            op=op,
            touch=touch_canon,
        )

    # ── Lists / projects activation ─────────────────────────────────
    if op in {"activateList", "deActivateList"}:
        if touch_short == "project_list":
            return _steps(
                "Create/open project → create FontList inside project → add ≥1 family.",
                "Open project list. Kebab → context-menu-item-activate-all-fonts "
                "(or deactivate-all for deActivateList). This is activateList, NOT activateFamily.",
                audit_emit(op, "project_list"),
                _close(),
                op=op,
                touch=touch_canon,
            )
        return _steps(
            "Open My Library list (/library/FontList/{id}) or Search → lists & folders.",
            "Kebab on list → Activate all fonts (context-menu-item-activate-all-fonts) "
            "or Deactivate all fonts. Do not open family detail.",
            audit_emit(op, "list"),
            _close(),
            op=op,
            touch=touch_canon,
        )
    if op in {"activateFontProject", "deActivateFontProject"}:
        return _steps(
            "NOTE: Project toolbar 'Activate fonts' usually fires activateFamily(Fontproject), "
            "not activateFontProject. Prefer Project fonts page after adding fonts.",
            "Create/open project → add families → use project Activate / Deactivate project fonts control "
            "that maps to this mutation if available; otherwise trigger via UI path that posts activateFontProject.",
            audit_emit(op, "project"),
            _close(),
            op=op,
            touch=touch_canon,
        )

    # ── pin / unpin / updateAssets ───────────────────────────────────
    if op == "pinAsset":
        return _steps(
            "Go to /library (Show all assets) OR /search → lists & folders.",
            "Create a Folder or FontList if none exist (createAsset). Kebab → Pin folder/list "
            "(context-menu-item-pin-folder / pin-list). Do not open family detail.",
            "Emit createAsset AUDIT_RESULT if run, then " + audit_emit(op, "global"),
            _close(),
            op=op,
            touch=touch_canon,
        )
    if op == "unpinAsset":
        return _steps(
            "Open /library. Find an already-pinned folder/list (pin one first if needed).",
            "Kebab → Unpin. Do not open family detail.",
            audit_emit(op, "global"),
            _close(),
            op=op,
            touch=touch_canon,
        )
    if op == "updateAssets":
        return _steps(
            "Open /library or /projects/library/{id} → Webkits tab.",
            "Take a webkit offline or online "
            "(webkit-actions-take-offline-* / take-online-*). Avoid family Search.",
            audit_emit(op, "global"),
            _close(),
            op=op,
            touch=touch_canon,
        )

    # ── Favourites helpers ──────────────────────────────────────────
    if op in {
        "addFavoriteFamilies",
        "removeFavoriteFamilies",
        "addFavoriteStyles",
        "removeFavoriteStyles",
        "addFavoritePair",
        "removeFavoritePair",
        "bulkRemoveStylesFromFavourites",
    }:
        return _steps(
            f"{_nav_search()} {seed}" if "add" in op.lower() or "remove" in op.lower() else "",
            "Use Favourites heart / Favourites page (/library/favourites/fonts) for this mutation. "
            "Do not wander into projects unless required.",
            f"Perform {label} with the minimal clicks that fire {op}.",
            audit_emit(op, touch_short),
            _close(),
            op=op,
            touch=touch_canon,
        )

    # ── createAsset / createProject / duplicate ─────────────────────
    if op == "createAsset":
        if touch_short == "project_list":
            return _steps(
                "Open a project → create FontList asset inside that project only.",
                audit_emit(op, "project_list"),
                _close(),
                op=op,
                touch=touch_canon,
            )
        if touch_short == "list":
            return _steps(
                "My Library → create FontList (not project-scoped).",
                audit_emit(op, "list"),
                _close(),
                op=op,
                touch=touch_canon,
            )
        return _steps(
            "My Library or Search → create Folder/FontList asset.",
            audit_emit(op, "global"),
            _close(),
            op=op,
            touch=touch_canon,
        )
    if op == "createProject":
        return _steps(
            "Go to /projects → Create project. Stay on projects flow.",
            audit_emit(op, touch_short if touch_short != "global" else "global"),
            _close(),
            op=op,
            touch=touch_canon,
        )
    if op == "duplicateProject":
        return _steps(
            "Open /projects → open an existing project → Duplicate.",
            audit_emit(op, touch_short),
            _close(),
            op=op,
            touch=touch_canon,
        )

    # ── Notifications / settings / admin ────────────────────────────
    if op in {"dismissNotification", "markNotificationRead"}:
        return _steps(
            "Open Notifications panel/page. Pick one notification.",
            f"Perform {op} (dismiss or mark read).",
            audit_emit(op, touch_short),
            _close(),
            op=op,
            touch=touch_canon,
        )
    if op in {"setLanguagePreference", "updateCustomerSettings", "getCustomerSettings"}:
        return _steps(
            "Open Account / Preferences / workspace settings for this touchpoint.",
            f"Perform {label} with minimal clicks.",
            audit_emit(op, touch_short),
            _close(),
            op=op,
            touch=touch_canon,
        )
    if op in {
        "bulkUpdateProfiles",
        "createUserInvitations",
        "deleteRoles",
        "deleteTeams",
        "createServiceAccount",
    }:
        return _steps(
            "Open User & Access (or Admin) area matching the touchpoint.",
            f"Perform {label}. Avoid font Search/family detail.",
            audit_emit(op, touch_short),
            _close(),
            op=op,
            touch=touch_canon,
        )

    # ── Library bulk / tags / documents (shortest path) ─────────────
    if op.startswith("bulk") or op in {
        "addFontListFamilies",
        "addFontListStyles",
        "addFontProjectFamilies",
        "addFontProjectStyles",
        "removeFontProjectStyles",
        "bulkTagStyles",
        "bulkUntagStyles",
        "updatePrivateTag",
        "updatePrivateTagAssociations",
        "addStyleDocument",
        "updateSessionFiles",
        "processUploadSessionFonts",
        "createContract",
        "parseAndCreateContract",
        "submitIntentForProduction",
        "cancelBatch",
        "syncUnSyncVariations",
    }:
        return _steps(
            f"Use the shortest NextGen UI path for {label} at touchpoint={touch_canon}.",
            "Reuse existing project/list/asset when possible — do not recreate setup if already present.",
            "Do not open unrelated family detail tabs. Do not switch environments.",
            audit_emit(op, touch_short),
            _close(),
            op=op,
            touch=touch_canon,
        )

    # Generic fallback — still scoped
    return _steps(
        f"Perform ONLY {label} at touchpoint={touch_canon}. Shortest path. "
        "Do not open family detail unless this mutation requires a style/variation.",
        f"If fonts are needed: {seed}",
        audit_emit(op, touch_short),
        _close(),
        op=op,
        touch=touch_canon,
    )


def steps_for_selection(selection: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Build steps for one or more selection rows (multi = sequential blocks)."""
    out: list[dict[str, str]] = []
    items = [s for s in selection if isinstance(s, dict)]
    for idx, s in enumerate(items, 1):
        op = str(s.get("operation") or "").strip()
        touch = str(s.get("touchpoint") or "").strip()
        label = str(s.get("label") or op).strip()
        extra = str(s.get("notes") or s.get("extra_details") or "").strip()
        if len(items) > 1:
            out.append(
                {
                    "op": op,
                    "touchpoint": touch,
                    "step": (
                        f"=== SCENARIO {idx}/{len(items)}: {label} — complete fully before the next. "
                        "Do not start the next scenario early. ==="
                    ),
                }
            )
        if extra:
            out.append(
                {
                    "op": op,
                    "touchpoint": touch,
                    "step": f"Operator hint for {label}: {extra}",
                }
            )
        out.extend(recipe_for(op, touch, label=label))
    return out


def testrail_steps_text(op: str, touch: str, *, label: str = "") -> str:
    """Plain-text steps for TestRail case body."""
    rows = recipe_for(op, touch, label=label)
    lines = [f"{i}. {r['step']}" for i, r in enumerate(rows, 1)]
    lines.append(
        "Expected: GraphQL mutation fires; AUDIT_RESULT line with real correlation-id; "
        "raw + enriched land in Generation Status."
    )
    return "\n".join(lines)
