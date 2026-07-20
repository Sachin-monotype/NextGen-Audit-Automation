"""CasePilot UI recipes — short event triggers for an anonymous AI runner.

Goal: fire the GraphQL mutation + emit AUDIT_RESULT. Prefer existing UI state.
Do NOT invent long create-project → create-list → add-family journeys unless the
scenario cannot fire without that scope.

Selectors follow MTConnectAutomation pages (data-qa-id=toggle-btn, etc.).
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
        f"Network → filter operationName={op} → copy response header correlation-id "
        f"(NOT x-correlation-id) → emit "
        f"AUDIT_RESULT|operation={op}|correlation_id=<uuid>|touchpoint={touch_short}"
    )


def _seed() -> str:
    env = (
        os.getenv("SEED_FAMILY_ID", "").strip()
        or os.getenv("TOUCHPOINT_FAMILY_ID", "").strip()
        or ""
    )
    if env:
        return f"Search for family id {env} if needed."
    return "Use any family already visible on screen (prefer deactivated)."


def _S(*lines: str, op: str, touch: str) -> list[dict[str, str]]:
    return [{"op": op, "touchpoint": touch, "step": x} for x in lines if x]


def recipe_for(op: str, touch: str, *, label: str = "") -> list[dict[str, str]]:
    """Minimal steps: get to the control → click → AUDIT_RESULT → next."""
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
    seed = _seed()

    # ── Family activate / deactivate ────────────────────────────────
    if op == "activateFamily" and ts == "global":
        return _S(
            f"GOAL: fire activateFamily (global). {seed}",
            "Go to Search (/search). Stay on font-family cards. Do NOT open /family/… detail.",
            "On first card: if toggle-btn is ON, click once to deactivate; wait snackbar.",
            "Click data-qa-id=toggle-btn to Activate. Wait success snackbar.",
            audit_emit(op, "global"),
            op=op,
            touch=touch_canon,
        )
    if op == "activateFamily" and ts == "favourite":
        return _S(
            f"GOAL: fire activateFamily with listType=Favorite. {seed}",
            "On Search card heart (icon-favorite) if not favourited → open /library/favourites/fonts.",
            "On Favourites grid click toggle-btn to Activate (do not open family detail).",
            audit_emit(op, "favourite"),
            op=op,
            touch=touch_canon,
        )
    if op == "activateFamily" and ts == "list":
        return _S(
            "GOAL: fire activateFamily with listType=Fontlist + listIds (no projectId).",
            "Reuse an existing My Library FontList if present (/library). Else create one FontList and add any family once.",
            "Open the list grid → click family toggle-btn to Activate.",
            audit_emit(op, "list"),
            op=op,
            touch=touch_canon,
        )
    if op == "activateFamily" and ts == "project":
        return _S(
            "GOAL: fire activateFamily with listType=Fontproject + projectId.",
            "Reuse a recent project (/projects) if it already has fonts. Else create project and add one family.",
            "On /projects/library/{id}/fonts click Activate fonts or family toggle-btn.",
            audit_emit(op, "project"),
            op=op,
            touch=touch_canon,
        )
    if op == "activateFamily" and ts == "project_list":
        return _S(
            "GOAL: fire activateFamily with BOTH projectId AND listIds.",
            "Reuse existing project that already has a FontList with fonts. Only create project+list+add family if none exist.",
            "Open that project list grid → Activate family via toggle-btn (not global Search).",
            audit_emit(op, "project_list"),
            op=op,
            touch=touch_canon,
        )
    if op == "deactivateFamilies":
        where = {
            "global": "Search card toggle-btn",
            "favourite": "Favourites grid toggle-btn",
            "list": "My Library list grid toggle-btn",
            "project": "Project fonts grid",
            "project_list": "Project list grid",
        }.get(ts, "matching scope grid")
        return _S(
            f"GOAL: fire deactivateFamilies ({ts}). Ensure family is ON in this scope first, then turn OFF.",
            f"Use {where}. Do not open family detail.",
            audit_emit(op, ts),
            op=op,
            touch=touch_canon,
        )

    # ── Style / variation (family detail required) ──────────────────
    if op in {"activateStyle", "deactivateStyle"}:
        act = "Activate" if op == "activateStyle" else "Deactivate"
        return _S(
            f"GOAL: fire {op} ({ts}) — ONE style, not whole family.",
            f"{seed} Open family card kebab → Activate styles submenu OR open /family/{{slug}} Styles tab.",
            f"{act} one style row. Scope must match {ts}.",
            audit_emit(op, ts),
            op=op,
            touch=touch_canon,
        )
    if op in {"activateVariation", "deactivateVariation"}:
        act = "Activate" if op == "activateVariation" else "Deactivate"
        return _S(
            f"GOAL: fire {op} ({ts}).",
            "Open family → style → Font versions drawer → "
            f"{act} a non-default variation.",
            audit_emit(op, ts),
            op=op,
            touch=touch_canon,
        )

    # ── List / project activate ─────────────────────────────────────
    if op in {"activateList", "deActivateList"}:
        kebab = (
            "context-menu-item-activate-all-fonts"
            if op == "activateList"
            else "context-menu-item-deactivate-all-fonts"
        )
        where = (
            "project FontList kebab"
            if ts == "project_list"
            else "My Library / Search lists&folders FontList kebab"
        )
        return _S(
            f"GOAL: fire {op} ({ts}) — list-wide, NOT activateFamily.",
            f"Open an existing FontList ({where}) → kebab → {kebab}.",
            audit_emit(op, "project_list" if ts == "project_list" else "list"),
            op=op,
            touch=touch_canon,
        )
    if op in {"activateFontProject", "deActivateFontProject"}:
        return _S(
            f"GOAL: fire {op}. Prefer existing project with fonts.",
            "Open project fonts page → use Activate/Deactivate project fonts control that posts this mutation.",
            audit_emit(op, "project"),
            op=op,
            touch=touch_canon,
        )

    # ── Pin / assets ────────────────────────────────────────────────
    if op == "pinAsset":
        return _S(
            "GOAL: fire pinAsset.",
            "Open /library or Search→lists&folders → kebab on Folder/FontList → pin-folder or pin-list.",
            audit_emit(op, "global"),
            op=op,
            touch=touch_canon,
        )
    if op == "unpinAsset":
        return _S(
            "GOAL: fire unpinAsset.",
            "On a pinned Folder/FontList → kebab → Unpin.",
            audit_emit(op, "global"),
            op=op,
            touch=touch_canon,
        )
    if op == "updateAssets":
        return _S(
            "GOAL: fire updateAssets.",
            "Library/Project → Webkits tab → Take offline or Take online on one webkit.",
            audit_emit(op, "global"),
            op=op,
            touch=touch_canon,
        )

    # ── Favourites helpers ──────────────────────────────────────────
    if op.startswith("addFavorite") or op.startswith("removeFavorite") or "Favourites" in op:
        return _S(
            f"GOAL: fire {op}.",
            "Search card heart (icon-favorite) or /library/favourites/fonts — one click that posts this mutation.",
            audit_emit(op, ts),
            op=op,
            touch=touch_canon,
        )

    # ── Create / duplicate (single action) ───────────────────────────
    if op == "createAsset":
        return _S(
            f"GOAL: fire createAsset ({ts}).",
            "Create one FontList/Folder in the correct place (My Library vs inside a project).",
            audit_emit(op, ts),
            op=op,
            touch=touch_canon,
        )
    if op == "createProject":
        return _S(
            "GOAL: fire createProject.",
            "Go to /projects → Create project → save.",
            audit_emit(op, ts if ts != "global" else "global"),
            op=op,
            touch=touch_canon,
        )
    if op == "duplicateProject":
        return _S(
            "GOAL: fire duplicateProject.",
            "Open an existing project → Duplicate.",
            audit_emit(op, ts),
            op=op,
            touch=touch_canon,
        )

    # ── Notifications / settings / admin ────────────────────────────
    if op in {"dismissNotification", "markNotificationRead"}:
        return _S(
            f"GOAL: fire {op}.",
            "Open Notifications → dismiss or mark one item read.",
            audit_emit(op, ts),
            op=op,
            touch=touch_canon,
        )
    if op in {
        "setLanguagePreference",
        "updateCustomerSettings",
        "getCustomerSettings",
        "bulkUpdateProfiles",
        "createUserInvitations",
        "deleteRoles",
        "deleteTeams",
        "createServiceAccount",
    }:
        return _S(
            f"GOAL: fire {op} at {touch_canon}.",
            "Open the matching settings/admin page → perform the single action that posts this mutation.",
            audit_emit(op, ts),
            op=op,
            touch=touch_canon,
        )

    # ── Everything else: one-liner trigger ──────────────────────────
    return _S(
        f"GOAL: fire {label} only. Reuse existing project/list/asset if present — do not rebuild setup.",
        f"Navigate to the UI surface for touchpoint={touch_canon}, click the control that posts {op}.",
        "Do not open unrelated family detail tabs. Do not explore.",
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
                        f"=== EVENT {idx}/{n}: {label} — fire mutation, emit AUDIT_RESULT, "
                        f"then immediately continue to event {idx + 1 if idx < n else 'DONE'}. "
                        "Do not close the browser until all events are done. ==="
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
                    "You must produce one AUDIT_RESULT per selected event (helpers optional)."
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


def testrail_steps_text(op: str, touch: str, *, label: str = "") -> str:
    rows = recipe_for(op, touch, label=label)
    lines = [f"{i}. {r['step']}" for i, r in enumerate(rows, 1)]
    lines.append(
        "Expected: Mutation fires; AUDIT_RESULT with real correlation-id; "
        "raw+enrich visible in Generation Status."
    )
    return "\n".join(lines)
