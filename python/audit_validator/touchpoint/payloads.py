"""Schema-correct GraphQL variables for TouchPoint curls / Postman.

Source of truth: mtf-graphql-schema (AddFontListFamiliesInput, ActivateFamilyInput, …)
and mtconnect-api flow call-sites. Never invent field names (e.g. listId ≠ fontListId).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from audit_validator.export_ui_catalog import export_flow_defs


@dataclass
class SeedIds:
    family_id: str
    style_id: str
    md5: str
    list_id: str = ""
    project_id: str = ""
    list_name: str = "QA_TouchPoint_List"
    project_name: str = "QA_TouchPoint_Project"
    document_id: str = ""
    session_id: str = ""
    file_id: str = ""
    customer_id: str = ""
    tag_id: str = ""
    team_id: str = ""
    role_id: str = ""
    profile_id: str = ""
    notification_id: str = ""
    contract_id: str = ""
    batch_id: str = ""
    sharee_id: str = ""
    invitation_id: str = ""
    service_account_id: str = ""
    attachment_id: str = ""
    headline_style_id: str = ""
    body_style_id: str = ""
    access_request_id: str = ""
    folder_id: str = ""
    # Populated by getCustomerSettings precondition for updateCustomerSettings
    customer_display_name: str = ""
    customer_supported_language: str = ""
    customer_primary_contact: str = ""
    customer_settings_flags: dict[str, Any] | None = None


def style_filter() -> dict[str, Any]:
    return {"pagination": {"skip": 0, "limit": 10}}


def create_font_list(seed: SeedIds, *, name: str | None = None) -> dict[str, Any]:
    inp: dict[str, Any] = {
        "name": (name or seed.list_name)[:50],
        "assetType": "FontList",
    }
    if seed.project_id:
        # List under a project (UI Navigation: project > list)
        inp["parentId"] = seed.project_id
    else:
        inp["accessRight"] = "FullAccess"
    return {"input": inp}


def create_project(seed: SeedIds, *, name: str | None = None) -> dict[str, Any]:
    return {
        "input": {
            "name": (name or seed.project_name)[:50],
            "description": "QA TouchPoint sheet seed project",
            "allowFontAdditionsByCollaborators": False,
            "allowFontDownloadsByCollaborators": False,
            "allowFontImportsByCollaborators": False,
            "enableProjectLevelImportedFonts": False,
            "autoActivateFontsForMembers": False,
        }
    }


def add_font_list_families(
    seed: SeedIds,
    *,
    font_list_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """AddFontListFamiliesInput — fontListId + families.familyIds (NOT listId)."""
    inp: dict[str, Any] = {
        "fontListId": font_list_id or seed.list_id,
        "families": {"familyIds": [seed.family_id]},
    }
    pid = project_id if project_id is not None else seed.project_id
    if pid:
        inp["projectId"] = pid
    return {"input": inp, "styleFilterInput": style_filter()}


def add_font_list_styles(
    seed: SeedIds,
    *,
    font_list_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    inp: dict[str, Any] = {
        "fontListId": font_list_id or seed.list_id,
        "styles": [{"styleId": seed.style_id}],
    }
    pid = project_id if project_id is not None else ""
    if pid:
        inp["projectId"] = pid
    return {"input": inp}


def add_font_project_families(
    seed: SeedIds, *, font_project_id: str | None = None
) -> dict[str, Any]:
    return {
        "input": {
            "fontProjectId": font_project_id or seed.project_id,
            "families": {"familyIds": [seed.family_id]},
        }
    }


def add_font_project_styles(
    seed: SeedIds, *, font_project_id: str | None = None
) -> dict[str, Any]:
    return {
        "input": {
            "fontProjectId": font_project_id or seed.project_id,
            "styles": [{"styleId": seed.style_id}],
        }
    }


def add_favorite_families(seed: SeedIds) -> dict[str, Any]:
    return {"input": {"familyIds": [seed.family_id]}}


def add_favorite_styles(seed: SeedIds) -> dict[str, Any]:
    return {"input": {"styleIds": [seed.style_id]}}


def activate_family(
    seed: SeedIds,
    *,
    touch: str,
) -> dict[str, Any]:
    """ActivateFamilyInput shapes by touchpoint (schema + mtconnect-api routing)."""
    inp: dict[str, Any] = {
        "familyIds": [seed.family_id],
        "activationType": "PERMANENT",
    }
    t = touch.lower()
    if "favourite" in t or "favorite" in t:
        inp["listType"] = "FAVORITE"
    elif "project > list" in t or "project>list" in t:
        # FONTLIST under a project — bare list UUID + projectId (UI / TouchPoint golden)
        inp["listIds"] = [seed.list_id]
        inp["listType"] = "FONTLIST"
        if seed.project_id:
            inp["projectId"] = seed.project_id
    elif touch.startswith("List") or "fontlist" in t:
        inp["listIds"] = [seed.list_id]
        inp["listType"] = "FONTLIST"
    elif touch == "Project" or t.strip().lower() == "project":
        inp["listIds"] = [f"project_{seed.project_id}"]
        inp["listType"] = "FONTPROJECT"
        inp["projectId"] = seed.project_id
    # Discovery/Browse → familyIds only
    return {"input": inp}


def deactivate_families(seed: SeedIds, *, touch: str) -> dict[str, Any]:
    base = activate_family(seed, touch=touch)["input"]
    base.pop("activationType", None)
    base["deactivationType"] = "PERMANENT"
    return {"input": base}


def activate_style(seed: SeedIds, *, touch: str) -> dict[str, Any]:
    fam = activate_family(seed, touch=touch)["input"]
    inp = {k: v for k, v in fam.items() if k != "familyIds"}
    inp["styleIds"] = [seed.style_id]
    return {"input": inp}


def deactivate_style(seed: SeedIds, *, touch: str) -> dict[str, Any]:
    base = activate_style(seed, touch=touch)["input"]
    base.pop("activationType", None)
    base["deactivationType"] = "PERMANENT"
    return {"input": base}


def activate_variation(seed: SeedIds, *, touch: str) -> dict[str, Any]:
    fam = activate_family(seed, touch=touch)["input"]
    inp = {k: v for k, v in fam.items() if k != "familyIds"}
    inp["variations"] = [{"styleId": seed.style_id, "md5": seed.md5}]
    return {"input": inp}


def deactivate_variation(seed: SeedIds, *, touch: str) -> dict[str, Any]:
    fam = activate_family(seed, touch=touch)["input"]
    inp = {k: v for k, v in fam.items() if k not in {"familyIds", "activationType"}}
    inp["md5s"] = [seed.md5]
    inp["deactivationType"] = "PERMANENT"
    return {"input": inp}


def activate_list(seed: SeedIds, *, touch: str) -> dict[str, Any]:
    t = touch.lower()
    if "favourite" in t or "favorite" in t:
        return {
            "input": {
                "listType": "FAVORITE",
                "activationType": "PERMANENT",
            }
        }
    inp: dict[str, Any] = {
        "listId": seed.list_id,
        "listType": "FONTLIST",
        "activationType": "PERMANENT",
    }
    if "project" in t and seed.project_id:
        inp["projectId"] = seed.project_id
    return {"input": inp}


def deactivate_list(seed: SeedIds, *, touch: str) -> dict[str, Any]:
    base = activate_list(seed, touch=touch)["input"]
    base.pop("activationType", None)
    base["deactivationType"] = "PERMANENT"
    return {"input": base}


def activate_font_project(seed: SeedIds) -> dict[str, Any]:
    return {
        "input": {
            "projectId": seed.project_id,
            "activationType": "PERMANENT",
        }
    }


def deactivate_font_project(seed: SeedIds) -> dict[str, Any]:
    return {
        "input": {
            "projectId": seed.project_id,
            "deactivationType": "PERMANENT",
        }
    }


def bulk_activate_styles(seed: SeedIds, *, with_project: bool) -> dict[str, Any]:
    inp: dict[str, Any] = {
        "styles": [{"id": seed.style_id}],
        "activationType": "PERMANENT",
    }
    if with_project and seed.project_id:
        inp["projectId"] = seed.project_id
    return {"input": inp}


def bulk_deactivate_styles(seed: SeedIds, *, with_project: bool) -> dict[str, Any]:
    inp: dict[str, Any] = {
        "styles": [{"id": seed.style_id}],
        "deactivationType": "PERMANENT",
    }
    if with_project and seed.project_id:
        inp["projectId"] = seed.project_id
    return {"input": inp}


def bulk_activate_lists(seed: SeedIds, *, with_project: bool) -> dict[str, Any]:
    inp: dict[str, Any] = {
        "lists": [{"id": seed.list_id}],
        "activationType": "PERMANENT",
    }
    if with_project and seed.project_id:
        inp["projectId"] = seed.project_id
    return {"input": inp}


def bulk_deactivate_lists(seed: SeedIds, *, with_project: bool) -> dict[str, Any]:
    inp: dict[str, Any] = {
        "lists": [{"id": seed.list_id}],
        "deactivationType": "PERMANENT",
    }
    if with_project and seed.project_id:
        inp["projectId"] = seed.project_id
    return {"input": inp}


# Operations that need styleFilterInput as a top-level variable (sibling of input)
STYLE_FILTER_OPS = frozenset({"addFontListFamilies", "removeFontListFamilies"})


def _update_customer_settings_vars(seed: SeedIds) -> dict[str, Any]:
    """Build UpdateCustomerSettings from getCustomerSettings snapshot (small delta)."""
    flags = seed.customer_settings_flags or {}
    display = (seed.customer_display_name or "Everest Company").strip()
    # Tiny rename so the mutation actually changes something
    if display.endswith(" QA"):
        display = display[: -3].rstrip() or "Everest Company"
    else:
        display = f"{display} QA"[:80]
    lang = (seed.customer_supported_language or "EN").strip() or "EN"
    # Flip EN ↔ DE when we have a current value so locale changes are visible
    if lang.upper() == "EN":
        next_lang = "DE"
    else:
        next_lang = "EN"
    primary = (seed.customer_primary_contact or "").strip()
    customer_settings = {
        "enableDownload": bool(flags.get("enableDownload", True)),
        "enableImportedFonts": bool(flags.get("enableImportedFonts", False)),
        "enableFontFormatSelection": bool(flags.get("enableFontFormatSelection", True)),
        "enableWebFontAccess": bool(flags.get("enableWebFontAccess", False)),
        "enableSelfHostingKit": bool(flags.get("enableSelfHostingKit", True)),
        "shareIntentForProduction": bool(flags.get("shareIntentForProduction", False)),
        "markUnmarkFontsAsProduction": bool(flags.get("markUnmarkFontsAsProduction", False)),
    }
    inp: dict[str, Any] = {
        "displayName": display,
        "supportedLanguage": next_lang,
        "customerSettings": customer_settings,
    }
    if primary:
        inp["primaryContact"] = primary
    return {"input": inp}


def export_csv_only() -> dict[str, Any]:
    return {"input": {"format": "CSV"}}


def export_font_scoped(seed: SeedIds) -> dict[str, Any]:
    return {
        "input": {
            "format": "CSV",
            "fontId": seed.family_id or seed.style_id or "",
            "fontName": seed.family_name or "QA Export Font",
        }
    }


def export_user_scoped(seed: SeedIds) -> dict[str, Any]:
    return {
        "input": {
            "format": "CSV",
            "subjectUserId": seed.profile_id or "",
            "subjectUserName": "QA Export User",
        }
    }


def export_webkit_scoped(seed: SeedIds) -> dict[str, Any]:
    return {
        "input": {
            "format": "CSV",
            "webkitId": seed.project_id or seed.list_id or "",
            "webkitName": "QA Export Webkit",
        }
    }


def export_roles(_seed: SeedIds) -> dict[str, Any]:
    return {"input": {"format": "CSV"}}


def export_users(_seed: SeedIds) -> dict[str, Any]:
    return {"input": {"format": "CSV"}}


def export_notifications(_seed: SeedIds) -> dict[str, Any]:
    return {"input": {"format": "CSV"}}


def variables_for(operation: str, seed: SeedIds, *, touch: str = "") -> dict[str, Any]:
    """Full GraphQL ``variables`` object for ``operation`` at ``touch``."""
    op = operation
    t = touch or "Discovery/Browse (global)"
    with_project = "project" in t.lower() and "list" not in t.lower()

    builders = {
        "createAsset": lambda: create_font_list(seed),
        "createFolder": lambda: {
            "input": {
                "name": f"QA_Gen_Folder_{seed.list_name[-10:] or 'tmp'}"[:50],
                "assetType": "Folder",
                "accessRight": "FullAccess",
            }
        },
        "createProject": lambda: create_project(seed),
        "addFontListFamilies": lambda: add_font_list_families(
            seed,
            project_id=seed.project_id if "project" in t.lower() else "",
        ),
        "addFontListStyles": lambda: add_font_list_styles(
            seed,
            project_id=seed.project_id if "project" in t.lower() else "",
        ),
        "addFontProjectFamilies": lambda: add_font_project_families(seed),
        "addFontProjectStyles": lambda: add_font_project_styles(seed),
        "addFavoriteFamilies": lambda: add_favorite_families(seed),
        "addFavoriteStyles": lambda: add_favorite_styles(seed),
        "activateFamily": lambda: activate_family(seed, touch=t),
        "deactivateFamilies": lambda: deactivate_families(seed, touch=t),
        "activateStyle": lambda: activate_style(seed, touch=t),
        "deactivateStyle": lambda: deactivate_style(seed, touch=t),
        "activateVariation": lambda: activate_variation(seed, touch=t),
        "deactivateVariation": lambda: deactivate_variation(seed, touch=t),
        "activateList": lambda: activate_list(seed, touch=t),
        "deActivateList": lambda: deactivate_list(seed, touch=t),
        "activateFontProject": lambda: activate_font_project(seed),
        "deActivateFontProject": lambda: deactivate_font_project(seed),
        "bulkActivateStyles": lambda: bulk_activate_styles(seed, with_project=with_project),
        "bulkDeactivateStyles": lambda: bulk_deactivate_styles(seed, with_project=with_project),
        "bulkActivateLists": lambda: bulk_activate_lists(seed, with_project=with_project),
        "bulkDeactivateLists": lambda: bulk_deactivate_lists(seed, with_project=with_project),
        "removeFontListFamilies": lambda: {
            "input": {
                "fontListId": seed.list_id,
                "families": {"familyIds": [seed.family_id]},
            },
            "styleFilterInput": style_filter(),
        },
        "removeFontListStyles": lambda: {
            "input": {
                "fontListId": seed.list_id,
                "styles": {"styleIds": [seed.style_id]},
            }
        },
        # Favourites extended — CreateFavoritePairsInput uses pairs[].headline/body.id
        "removeFavoriteFamilies": lambda: {"input": {"familyIds": [seed.family_id]}},
        "removeFavoriteStyles": lambda: {"input": {"styleIds": [seed.style_id]}},
        "addFavoritePair": lambda: {
            "input": {
                "pairs": [
                    {
                        "headline": {"id": seed.headline_style_id or seed.style_id},
                        "body": {"id": seed.body_style_id or seed.style_id},
                    }
                ]
            }
        },
        "removeFavoritePair": lambda: {
            "input": {
                "pairs": [
                    {
                        "headline": {"id": seed.headline_style_id or seed.style_id},
                        "body": {"id": seed.body_style_id or seed.style_id},
                    }
                ]
            }
        },
        "bulkAddPairsToFavorite": lambda: {
            "input": {
                "pairs": [
                    {
                        "headline": {"id": seed.headline_style_id or seed.style_id},
                        "body": {"id": seed.body_style_id or seed.style_id},
                    }
                ]
            }
        },
        "bulkRemovePairsFromFavorite": lambda: {
            "input": {
                "pairs": [
                    {
                        "headline": {"id": seed.headline_style_id or seed.style_id},
                        "body": {"id": seed.body_style_id or seed.style_id},
                    }
                ]
            }
        },
        "bulkAddStylesToFavourites": lambda: {
            "input": {"styles": [{"id": seed.style_id}]}
        },
        "bulkRemoveStylesFromFavourites": lambda: {
            "input": {"styles": [{"id": seed.style_id}]}
        },
        # Library / assets
        "updateAsset": lambda: {
            "input": {
                "assetId": seed.list_id,
                "assetType": "FontList",
                "name": (seed.list_name or "QA_TouchPoint_List")[:50],
            }
        },
        "updateAssets": lambda: {
            "input": {
                "items": [
                    {
                        "assetId": seed.list_id,
                        "assetType": "FontList",
                        "name": (seed.list_name or "QA_TouchPoint_List")[:50],
                    }
                ]
            }
        },
        "deleteAssets": lambda: {
            "input": {
                "assets": [{"assetType": "FontList", "assetIds": [seed.list_id]}]
            }
        },
        "bulkCopyAssets": lambda: {
            "input": {
                "items": [
                    {
                        "source": {"assetId": seed.list_id, "assetType": "FontList"},
                        "target": (
                            {"assetId": seed.folder_id, "assetType": "Folder"}
                            if seed.folder_id
                            else {"assetId": "root"}
                        ),
                    }
                ]
            }
        },
        "bulkMoveAssets": lambda: {
            "input": {
                "sourceAssetIds": [seed.list_id],
                "targetAssetId": seed.folder_id or "root",
            }
        },
        "bulkAddStylesToList": lambda: {
            "input": {
                "styles": [{"id": seed.style_id}],
                "listId": seed.list_id,
            }
        },
        "bulkRemoveStylesFromList": lambda: {
            "input": {
                "styleId": seed.style_id,
                "lists": [{"assetId": seed.list_id, "assetType": "FontList"}],
            }
        },
        "pinAsset": lambda: {
            "input": {"assetId": seed.list_id}
        },
        "unpinAsset": lambda: {
            "input": {"assetId": seed.list_id}
        },
        "updateAssetSharing": lambda: {
            "input": {
                "assetId": seed.list_id,
                "assetType": "FontList",
                "notify": False,
                "data": [
                    {
                        "action": "GRANT",
                        "payload": [
                            {
                                # Public does not require shareeId (User/Group do)
                                "shareeType": "Public"
                                if not (seed.sharee_id or seed.profile_id)
                                else "User",
                                "accessIdMap": (
                                    [{"accessId": 24}]  # Public Viewer for FontList/FontSet
                                    if not (seed.sharee_id or seed.profile_id)
                                    else [
                                        {
                                            "shareeId": seed.sharee_id or seed.profile_id,
                                            "accessId": 27,
                                        }
                                    ]
                                ),
                            }
                        ],
                    }
                ],
            }
        },
        "removeFontProjectFamilies": lambda: {
            "input": {
                "fontProjectId": seed.project_id,
                "families": {"familyIds": [seed.family_id]},
            }
        },
        "removeFontProjectStyles": lambda: {
            "input": {
                "fontProjectId": seed.project_id,
                "styles": {"styleIds": [seed.style_id]},
            }
        },
        "syncUnSyncVariations": lambda: {
            "input": {"operation": "SYNC", "md5s": [seed.md5]}
        },
        # Tags
        "createPrivateTags": lambda: {
            "input": {
                "customerId": seed.customer_id,
                "tags": [
                    {
                        "name": (
                            f"QA_{seed.list_name[-10:] or str(int(__import__('time').time()))}"[:64]
                        )
                    }
                ],
            }
        },
        "updatePrivateTag": lambda: {
            "input": {
                "customerId": seed.customer_id,
                "tagId": seed.tag_id,
                "name": f"QA_Tag_Updated_{seed.style_id[-4:] or 'tmp'}",
            }
        },
        "deletePrivateTags": lambda: {
            "input": {"customerId": seed.customer_id, "tagIds": [seed.tag_id]}
        },
        "deleteAllPrivateTags": lambda: {
            "input": {"customerId": seed.customer_id}
        },
        "bulkTagStyles": lambda: {
            "input": {
                "tagId": seed.tag_id,
                "styles": [{"id": seed.style_id}],
            }
        },
        "bulkUntagStyles": lambda: {
            "input": {
                "tagId": seed.tag_id,
                "styles": [{"id": seed.style_id}],
            }
        },
        "updatePrivateTagAssociations": lambda: {
            "input": {
                "customerId": seed.customer_id,
                "tags": [
                    {
                        "id": seed.tag_id,
                        "associate": [{"styleId": seed.style_id}],
                    }
                ],
            }
        },
        # Teams / orgs (minimal schema-valid; some need live IDs)
        "createTeam": lambda: {
            "input": {
                "name": f"QA_Team_{seed.list_name[-8:] or 'tmp'}"[:64],
                "description": "QA TouchPoint seed team",
            }
        },
        "updateTeam": lambda: {
            "input": {
                "id": seed.team_id,
                "name": f"QA_Team_Updated"[:64],
            }
        },
        "deleteTeams": lambda: {"input": {"ids": [seed.team_id] if seed.team_id else []}},
        "getProfiles": lambda: {
            "filter": {"status": ["ACTIVE"]},
            "pagination": {"limit": 20, "skip": 0},
            "sort": {"field": "fullName", "sortOrder": "DESC"},
        },
        "getTeams": lambda: {
            "pagination": {"skip": 0, "limit": 20},
            "filter": {"name": None, "profileCount": None},
        },
        "getRoles": lambda: {
            "input": {
                "customerId": seed.customer_id,
                "pagination": {"skip": 0, "limit": 20},
            }
        },
        "createUserInvitations": lambda: {
            "input": {
                "customerId": seed.customer_id,
                "data": [
                    {
                        "emails": [
                            f"qa.invite+{seed.style_id[-6:] or int(__import__('time').time())}@gmail.com"
                        ],
                        "status": 1,
                        "roleId": seed.role_id,
                        "teamIds": [],
                        "tempUserExpiryDate": None,
                        "emailLocale": "EN",
                    }
                ],
            }
        },
        "updateProfile": lambda: {
            "input": {"id": seed.profile_id, "isActive": True}
        },
        # UI uses ISO-ish codes; product accepts EN/en — prefer EN (matches company settings)
        "setLanguagePreference": lambda: {"input": {"language": "EN"}},
        "markOnboardingCompleted": lambda: {},
        "deleteCompanyLogo": lambda: {},
        "markCompanyLogoUploadSuccess": lambda: {},
        "resetPreferences": lambda: {},
        "globalEmailOptOut": lambda: {},
        # Notifications — getNotifications fills seed.notification_id first
        "getNotifications": lambda: {
            "pagination": {"limit": 20, "skip": 0},
            "filter": {},
        },
        "dismissNotification": lambda: {
            "input": {
                "id": seed.notification_id or "0",
                "action": "DISMISS",
            }
        },
        "markNotificationRead": lambda: {
            "input": {
                "id": seed.notification_id or "0",
                "action": "MARK_READ",
            }
        },
        "markAllNotificationsRead": lambda: {"input": {}},
        "updatePreference": lambda: {
            "input": {
                "preferenceCode": "FONT_ACTIVATION",
                "channel": "EMAIL",
                "enabled": True,
            }
        },
        "updateSubToggle": lambda: {
            "input": {
                "preferenceCode": "FONT_ACTIVATION",
                "channel": "EMAIL",
                "enabled": True,
            }
        },
        "bulkUpdatePreferences": lambda: {
            "input": [
                {
                    "preferenceCode": "FONT_ACTIVATION",
                    "channel": "EMAIL",
                    "enabled": True,
                }
            ]
        },
        "bulkNotificationAction": lambda: {
            "input": {
                "action": "MARK_READ",
                "ids": [seed.notification_id],
            }
        },
        "applyUnsubscribe": lambda: {"token": "QA_PLACEHOLDER_UNSUBSCRIBE_TOKEN"},
        # Imported / production
        "markProductionFonts": lambda: {
            "input": {"styleIds": [seed.style_id], "inProduction": True}
        },
        "bulkUnmarkProductionFont": lambda: {
            "input": {"styles": [{"id": seed.style_id}]}
        },
        "submitIntentForProduction": lambda: {
            "input": {"styleId": seed.style_id}
        },
        "denyIntentForProduction": lambda: {
            "input": {"styleId": seed.style_id}
        },
        "deleteImportedFonts": lambda: {
            "input": {"styleIds": [seed.style_id]}
        },
        "linkContractToStyles": lambda: {
            "input": {
                "contractId": seed.contract_id,
                "styleIds": [seed.style_id],
            }
        },
        "unlinkStyleFromContract": lambda: {
            "input": {
                "contractId": seed.contract_id,
                "styleIds": [seed.style_id],
            }
        },
        "createBYOFBatchAndCheckDuplicates": lambda: {
            "input": {
                "sessionId": seed.session_id,
                "fileIds": [seed.file_id] if seed.file_id else [],
            }
        },
        "keepInProduction": lambda: {
            "input": {"styleIds": [seed.style_id]}
        },
        "requestFontAccess": lambda: {
            "input": {"styleIds": [seed.style_id]}
        },
        "cancelBatch": lambda: {"batchId": seed.batch_id},
        "deleteAssetAttachment": lambda: {
            "id": seed.attachment_id,
            "assetId": seed.project_id or seed.list_id,
        },
        "sharingInfoForAssets": lambda: {
            "assets": [
                {"id": seed.list_id, "assetType": "FontList"}
            ]
        },
        # ── Style documents / comments (import context) ──
        "addStyleComment": lambda: {
            "input": {"styleId": seed.style_id, "text": "QA TouchPoint comment"}
        },
        "updateStyleComment": lambda: {
            "input": {
                "styleId": seed.style_id,
                "commentId": seed.tag_id or "00000000-0000-0000-0000-000000000001",
                "text": "QA TouchPoint updated comment",
            }
        },
        "deleteStyleComment": lambda: {
            "input": {
                "styleId": seed.style_id,
                "commentId": seed.tag_id or "00000000-0000-0000-0000-000000000001",
            }
        },
        "addStyleDocument": lambda: {
            "input": {
                "styleId": seed.style_id,
                "documentMd5": seed.md5,
                "fileName": "qa-touchpoint-doc.pdf",
            }
        },
        "createStyleDocumentsUploadUrl": lambda: {
            "input": {
                "files": [
                    {
                        "md5": seed.md5,
                        "fileName": "qa-touchpoint-doc.pdf",
                        "mimeType": "application/pdf",
                        "size": 100,
                    }
                ]
            }
        },
        "deleteStyleDocument": lambda: {
            "input": {
                "styleId": seed.style_id,
                "documentId": seed.document_id,
            }
        },
        # ── Web projects ──
        "createWebProject": lambda: {
            "input": {
                "styles": [{"id": seed.style_id, "isEmbedded": True}],
            }
        },
        "addStylesToWebProject": lambda: {
            "input": {
                "webProjectId": seed.project_id,
                "styles": [{"id": seed.style_id, "isEmbedded": False}],
            }
        },
        "removeStylesFromWebProject": lambda: {
            "input": {
                "webProjectId": seed.project_id,
                "styles": [seed.style_id],
            }
        },
        "editWebProject": lambda: {
            "input": {
                "id": seed.project_id,
                "name": "QA_TouchPoint_WebProject",
                "domains": ["example.com"],
            }
        },
        # ── Font access ──
        "approveFontAccess": lambda: {
            "input": {
                "request": [
                    {"requestorId": seed.profile_id, "styleId": seed.style_id}
                ],
                "reason": "QA TouchPoint approval",
            }
        },
        "rejectFontAccess": lambda: {
            "input": {
                "request": [
                    {"requestorId": seed.profile_id, "styleId": seed.style_id}
                ],
                "reason": "QA TouchPoint rejection",
            }
        },
        # ── Project lifecycle ──
        # UI "Duplicate project" uses bulkCopyAssets (BulkCopyAssetsInput), not DuplicateProjectInput
        "duplicateProject": lambda: {
            "input": {
                "items": [
                    {
                        "source": {
                            "assetId": seed.project_id,
                            "assetType": "FontProject",
                        },
                        "target": {"assetId": "root"},
                    }
                ]
            }
        },
        "publishProject": lambda: {
            "input": {"projectId": seed.project_id}
        },
        "deleteProject": lambda: {
            "input": {"projectId": seed.project_id}
        },
        "linkDocumentToProject": lambda: {
            "input": {
                "projectId": seed.project_id,
                "documentId": seed.document_id,
                "documentName": "qa-touchpoint-doc.indd",
                "app": "InDesign",
            }
        },
        "unlinkDocumentFromProject": lambda: {
            "input": {
                "projectId": seed.project_id,
                "documentId": seed.document_id,
            }
        },
        "updateFontProjectStyles": lambda: {
            "input": {
                "fontProjectId": seed.project_id,
                "resolutions": [
                    {
                        "styleId": seed.style_id,
                        "resolvedMd5": seed.md5,
                        "resolvedVariationId": seed.style_id,
                        "unresolvedMd5s": [],
                    }
                ],
            }
        },
        # ── Asset attachments ──
        "createAssetAttachmentUpload": lambda: {
            "input": {
                "uploadSessionId": seed.session_id,
                "assetType": "FontProject",
                "context": "FILE_ATTACHMENT",
                "fileName": "qa-touchpoint-attachment.pdf",
                "sizeBytes": 1024,
                "mimeType": "application/pdf",
                "md5": seed.md5,
                "assetId": seed.project_id,
            }
        },
        "finalizeAssetAttachments": lambda: {
            "input": {
                "items": [{"attachmentDraftId": seed.attachment_id}],
                "assetId": seed.project_id,
            }
        },
        # ── Contracts / BYOF (some destructive) ──
        "createContract": lambda: {
            "input": {
                "styleIds": [seed.style_id] if seed.style_id else [],
                "isFreeToUse": False,
                "licenceType": "DESKTOP",
                "isReviewed": False,
                "linkedImportedFontScope": "GLOBAL",
                "licenceName": f"QA_TouchPoint_Licence_{seed.style_id[-6:] or 'tmp'}",
            }
        },
        "updateContract": lambda: {
            "input": {
                "contractId": seed.contract_id,
                "changedFields": {"licenceName": "QA_TouchPoint_Updated", "isReviewed": True},
            }
        },
        "deleteContracts": lambda: {
            "input": {"contracts": [seed.contract_id]}
        },
        "linkDocumentsToContract": lambda: {
            "input": {
                "contractId": seed.contract_id,
                "documents": [
                    {
                        "documentMd5": seed.md5,
                        "documentRole": "PRIMARY",
                        "originalFilename": "qa-licence.pdf",
                    }
                ],
            }
        },
        "unlinkDocumentsFromContract": lambda: {
            "input": {
                "contractId": seed.contract_id,
                "documentMd5s": [seed.md5],
            }
        },
        "parseAndCreateContract": lambda: {
            "sessionId": seed.session_id,
            "fileIds": [seed.file_id] if seed.file_id else [],
        },
        "publishAddOn": lambda: {
            "input": {
                "customerId": seed.customer_id,
                "associations": [],
            }
        },
        "importFontTemplate": lambda: {
            "input": {
                "fileMd5": seed.md5,
                "fileName": "qa-template.csv",
            }
        },
        "updateAssetsSharingInfo": lambda: {
            "input": {
                "assetType": "Product",
                "notify": False,
                "assets": [
                    {
                        "assetId": seed.style_id,
                        "data": [
                            {
                                "action": "GRANT",
                                "payload": [
                                    {
                                        "shareeType": "User",
                                        "accessIdMap": [
                                            {
                                                "shareeId": seed.sharee_id or seed.profile_id,
                                                "accessId": 22,
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        },
        # ── Service accounts (admin / token ops) ──
        "createServiceAccount": lambda: {
            "input": {
                "customerId": seed.customer_id,
                "serverName": f"QA_TP_SA_{seed.list_name[-12:] or seed.style_id[-6:]}"[:64],
                "serverDescription": "QA TouchPoint seed service account",
                "serverType": "TESTING",
                "tokenExpiry": "2030-01-01T00:00:00.000Z",
            }
        },
        "updateServiceAccount": lambda: {
            "input": {
                "id": seed.service_account_id,
                "customerId": seed.customer_id,
                "serverName": "QA_TouchPoint_SA_Updated",
            }
        },
        "suspendServiceAccount": lambda: {
            "input": {
                "customerId": seed.customer_id,
                "ids": [seed.service_account_id],
            }
        },
        "deleteServiceAccount": lambda: {
            "input": {
                "customerId": seed.customer_id,
                "ids": [seed.service_account_id],
            }
        },
        "regenerateToken": lambda: {
            "input": {
                "customerId": seed.customer_id,
                "ids": [seed.service_account_id],
            }
        },
        "revokeToken": lambda: {
            "input": {
                "customerId": seed.customer_id,
                "tokenIds": [seed.service_account_id],
            }
        },
        # ── Customer / company (admin) ──
        "createCustomer": lambda: {
            "input": {
                "name": "QA_TouchPoint_Customer",
                "invitedPrimaryContactEmail": "qa.touchpoint@example.com",
                "isTrial": True,
                "isTestDemo": False,
                "productType": "ENTERPRISE",
                "entitlement": {
                    "seatsAvailable": 1,
                    "fontBridgeSeatCount": 1,
                    "webImpressionLimit": 0,
                    "pageViewLimit": 0,
                    "productionFontCount": 0,
                    "productionFontCountOverrides": [],
                    "allowDownload": False,
                    "allowImportedFonts": False,
                    "allowClickThroughEula": False,
                    "allowWebFontAccess": False,
                    "allowDigitalAccess": False,
                    "expiryDate": "2030-01-01T00:00:00.000Z",
                    "isLicenseManagementEnabled": False,
                    "allowSelfHostingKits": False,
                    "inventories": [],
                },
                "settings": {
                    "allowFontFormatSelectionInFontList": False,
                    "isAnalyticsEnabled": False,
                    "isSSOEnabled": False,
                    "isWorkspaceEnabled": False,
                    "isResearchParticipationEnabled": False,
                    "isPreDeliveryEnabled": False,
                },
                "addOnFonts": {"associations": [], "disassociations": []},
            }
        },
        "updateCustomer": lambda: {
            "input": {
                "id": seed.customer_id,
                "reason": "QA TouchPoint update",
                "isTrial": True,
                "productType": "ENTERPRISE",
                "name": "QA_TouchPoint_Customer_Updated",
            }
        },
        "getCustomerSettings": lambda: {},
        "updateCustomerSettings": lambda: _update_customer_settings_vars(seed),
        "updateCompanySsoStatus": lambda: {
            "input": {
                "companyId": seed.customer_id,
                "enableSso": False,
            }
        },
        "createCompanyLogoUploadUrl": lambda: {
            "input": {"mimeType": "image/png", "size": 1024}
        },
        # ── Upload session / doc scanning ──
        "createUploadSession": lambda: {
            "files": [
                {
                    "fileName": "qa-touchpoint-scan.pdf",
                    "size": 1024,
                    "contentType": "application/pdf",
                }
            ]
        },
        "updateSessionFiles": lambda: {
            "sessionId": seed.session_id,
            "files": [
                {
                    "status": "ADD",
                    "fileName": "qa-touchpoint-scan.pdf",
                    "size": 1024,
                    "contentType": "application/pdf",
                    **({"fileId": seed.file_id} if seed.file_id else {}),
                }
            ],
        },
        "processUploadSessionFonts": lambda: {
            "sessionId": seed.session_id,
            **({"fileId": seed.file_id} if seed.file_id else {}),
            **({"projectId": seed.project_id} if seed.project_id else {}),
        },
        # ── Batch exports (async Conductor) ──
        "exportFontAssets": lambda: export_font_scoped(seed),
        "exportFontProjects": lambda: export_font_scoped(seed),
        "exportFontUsers": lambda: export_font_scoped(seed),
        "exportFontWebkits": lambda: export_font_scoped(seed),
        "exportReportingFonts": lambda: export_csv_only(),
        "exportReportingUsers": lambda: export_csv_only(),
        "exportReportingWebkits": lambda: export_csv_only(),
        "exportUserAssets": lambda: export_user_scoped(seed),
        "exportUserFonts": lambda: export_user_scoped(seed),
        "exportUserProjects": lambda: export_user_scoped(seed),
        "exportWebkitDomains": lambda: export_webkit_scoped(seed),
        "exportWebkitFonts": lambda: export_webkit_scoped(seed),
        "exportCompanyLibrary": lambda: export_csv_only(),
        "exportMyLibrary": lambda: export_csv_only(),
        "exportImportedFonts": lambda: export_csv_only(),
        "exportLeavingSoonFonts": lambda: export_csv_only(),
        "exportNotifications": lambda: export_notifications(seed),
        "exportTags": lambda: export_csv_only(),
        "exportServiceAccount": lambda: export_csv_only(),
        "exportSsoMappings": lambda: export_csv_only(),
        "exportTeams": lambda: export_csv_only(),
        "exportRoles": lambda: export_roles(seed),
        "exportUsers": lambda: export_users(seed),
        "exportFontTemplate": lambda: {
            "input": {
                "columns": [
                    {"key": "styleName", "label": "Style"},
                    {"key": "familyName", "label": "Family"},
                ]
            }
        },
        "markSessionFileUploaded": lambda: {
            "sessionId": seed.session_id,
            "fileId": seed.file_id,
        },
        "completeUploadSession": lambda: {"sessionId": seed.session_id},
        "saveDocumentMetadata": lambda: {
            "input": {
                "fileIdentifier": seed.file_id or seed.session_id,
                "type": "application/pdf",
                "version": "1.0",
                "fontsInfo": [{"psName": "QA-TouchPoint-Regular", "md5": seed.md5}],
            }
        },
        # ── Production / licence management ──
        "submitFontUsageReport": lambda: {
            "input": {
                "companyId": seed.customer_id,
                "sourceContext": "DASHBOARD",
            }
        },
        "updateFontsForReview": lambda: {
            "input": {
                "companyId": seed.customer_id,
                "sourceContext": "DASHBOARD",
                "fontsForReview": [{"styleId": seed.style_id, "action": "DENY"}],
            }
        },
        "updateProductionFont": lambda: {
            "input": {
                "companyId": seed.customer_id,
                "sourceContext": "LIBRARY",
                "updateProductionFonts": [
                    {"styleId": seed.style_id, "inProduction": True}
                ],
            }
        },
        # ── Profiles / roles / SSO / invitations (admin) ──
        "bulkUpdateProfiles": lambda: {
            "input": {
                "targetProfileIds": [seed.profile_id] if seed.profile_id else [],
                "action": "CHANGE_TEAMS",
                "operation": {
                    "teamIds": [seed.team_id] if seed.team_id else [],
                },
            }
        },
        "resetPassword": lambda: {"input": {"profileId": seed.profile_id}},
        "deleteProfiles": lambda: {
            # Disposable profile only — set SEED_DELETE_PROFILE_ID (never the actor).
            "input": {"profileIds": [seed.profile_id] if seed.profile_id else []}
        },
        "createRole": lambda: {
            "input": {
                "customerId": seed.customer_id,
                "name": f"QA_Role_{seed.list_name[-8:] or 'tmp'}"[:64],
                "description": "QA TouchPoint seed role",
                "permissionGroups": [],
            }
        },
        "updateRole": lambda: {
            "input": {
                "customerId": seed.customer_id,
                "id": seed.role_id,
                "name": "QA_TouchPoint_Role_Updated",
            }
        },
        "deleteRoles": lambda: {
            "input": {
                "customerId": seed.customer_id,
                "ids": [seed.role_id] if seed.role_id else [],
            }
        },
        "createSsoMapping": lambda: {
            "input": {
                "matchText": "qa-touchpoint-sso-group",
                "displayName": "QA TouchPoint SSO",
                "roleId": seed.role_id,
            }
        },
        "updateSsoMapping": lambda: {
            "input": {
                "ruleId": seed.tag_id or seed.role_id,
                "matchText": "qa-touchpoint-sso-group-updated",
                "displayName": "QA TouchPoint SSO Updated",
                "roleId": seed.role_id,
            }
        },
        "deleteSsoMappings": lambda: {
            "input": {"ruleIds": [seed.tag_id or seed.role_id]}
        },
        "reorderSsoMappings": lambda: {
            "input": {"currentGroupId": seed.tag_id or seed.role_id}
        },
        "updateUserInvitations": lambda: {
            "input": {
                "customerId": seed.customer_id,
                "data": [{"invitationId": seed.invitation_id, "status": 0}],
            }
        },
    }
    fn = builders.get(op)
    if not fn:
        # Unknown mutation — return empty hint; sheet marks it for Gaps
        return {"input": {"_unsupported": op, "_touch": t}}
    return fn()


def assert_add_font_list_families_shape(variables: dict[str, Any]) -> None:
    inp = variables.get("input") or {}
    assert "fontListId" in inp, f"missing fontListId: {inp}"
    assert "listId" not in inp, f"must not use listId: {inp}"
    fams = inp.get("families") or {}
    assert isinstance(fams.get("familyIds"), list) and fams["familyIds"], fams
    assert "styleFilterInput" in variables, variables


# Multi-step sequences: create asset/project → seed → trigger (sheet + Postman)
# Project > List always: createProject → add*ToProject → createAsset(list) → add*ToList → trigger
FLOW_DEFS: dict[str, dict[str, list[str]]] = {
    "activateFamily": {
        "Discovery/Browse (global)": ["deactivateFamilies", "activateFamily"],
        "List (FONTLIST)": [
            "createAsset",
            "addFontListFamilies",
            "deactivateFamilies",
            "activateFamily",
        ],
        "Favourite": [
            "removeFavoriteFamilies",
            "addFavoriteFamilies",
            "deactivateFamilies",
            "activateFamily",
        ],
        "Project": [
            "createProject",
            "addFontProjectFamilies",
            "deactivateFamilies",
            "activateFamily",
        ],
        "Project > List": [
            "createProject",
            "addFontProjectFamilies",
            "createAsset",
            "addFontListFamilies",
            "deactivateFamilies",
            "activateFamily",
        ],
    },
    "deactivateFamilies": {
        "Discovery/Browse (global)": ["activateFamily", "deactivateFamilies"],
        "List (FONTLIST)": [
            "createAsset",
            "addFontListFamilies",
            "activateFamily",
            "deactivateFamilies",
        ],
        "Favourite": [
            "removeFavoriteFamilies",
            "addFavoriteFamilies",
            "activateFamily",
            "deactivateFamilies",
        ],
        "Project": [
            "createProject",
            "addFontProjectFamilies",
            "activateFamily",
            "deactivateFamilies",
        ],
        "Project > List": [
            "createProject",
            "addFontProjectFamilies",
            "createAsset",
            "addFontListFamilies",
            "activateFamily",
            "deactivateFamilies",
        ],
    },
    "activateStyle": {
        "Discovery/Browse (global)": ["deactivateStyle", "activateStyle"],
        "List (FONTLIST)": [
            "createAsset",
            "addFontListStyles",
            "deactivateStyle",
            "activateStyle",
        ],
        "Favourite": [
            "removeFavoriteStyles",
            "addFavoriteStyles",
            "deactivateStyle",
            "activateStyle",
        ],
        "Project": [
            "createProject",
            "addFontProjectStyles",
            "deactivateStyle",
            "activateStyle",
        ],
        "Project > List": [
            "createProject",
            "addFontProjectStyles",
            "createAsset",
            "addFontListStyles",
            "deactivateStyle",
            "activateStyle",
        ],
    },
    "deactivateStyle": {
        "Discovery/Browse (global)": ["activateStyle", "deactivateStyle"],
        "List (FONTLIST)": [
            "createAsset",
            "addFontListStyles",
            "activateStyle",
            "deactivateStyle",
        ],
        "Favourite": [
            "removeFavoriteStyles",
            "addFavoriteStyles",
            "activateStyle",
            "deactivateStyle",
        ],
        "Project": [
            "createProject",
            "addFontProjectStyles",
            "activateStyle",
            "deactivateStyle",
        ],
        "Project > List": [
            "createProject",
            "addFontProjectStyles",
            "createAsset",
            "addFontListStyles",
            "activateStyle",
            "deactivateStyle",
        ],
    },
    "activateVariation": {
        "Discovery/Browse (global)": ["deactivateVariation", "activateVariation"],
        "List (FONTLIST)": [
            "createAsset",
            "addFontListStyles",
            "deactivateVariation",
            "activateVariation",
        ],
        "Favourite": [
            "removeFavoriteStyles",
            "addFavoriteStyles",
            "deactivateVariation",
            "activateVariation",
        ],
        "Project": [
            "createProject",
            "addFontProjectStyles",
            "deactivateVariation",
            "activateVariation",
        ],
        "Project > List": [
            "createProject",
            "addFontProjectStyles",
            "createAsset",
            "addFontListStyles",
            "deactivateVariation",
            "activateVariation",
        ],
    },
    "activateList": {
        "List (FONTLIST)": ["createAsset", "addFontListFamilies", "deActivateList", "activateList"],
        "Project > List": [
            "createProject",
            "addFontProjectFamilies",
            "createAsset",
            "addFontListFamilies",
            "deActivateList",
            "activateList",
        ],
    },
    "deActivateList": {
        "List (FONTLIST)": ["createAsset", "addFontListFamilies", "activateList", "deActivateList"],
        "Project > List": [
            "createProject",
            "addFontProjectFamilies",
            "createAsset",
            "addFontListFamilies",
            "activateList",
            "deActivateList",
        ],
    },
    "activateFontProject": {
        "Project": [
            "createProject",
            "addFontProjectFamilies",
            "deActivateFontProject",
            "activateFontProject",
        ],
    },
    "deActivateFontProject": {
        "Project": [
            "createProject",
            "addFontProjectFamilies",
            "activateFontProject",
            "deActivateFontProject",
        ],
    },
    "addFontListFamilies": {
        "List (FONTLIST)": ["createAsset", "addFontListFamilies"],
        "Project > List": [
            "createProject",
            "addFontProjectFamilies",
            "createAsset",
            "addFontListFamilies",
        ],
    },
    "addFontListStyles": {
        "List (FONTLIST)": ["createAsset", "addFontListStyles"],
        "Project > List": [
            "createProject",
            "addFontProjectStyles",
            "createAsset",
            "addFontListStyles",
        ],
    },
    "addFontProjectFamilies": {
        "Project": ["createProject", "addFontProjectFamilies"],
    },
    "bulkActivateStyles": {
        "Discovery/Browse (global)": ["bulkActivateStyles"],
        "Project": ["createProject", "bulkActivateStyles"],
    },
    "bulkDeactivateStyles": {
        "Discovery/Browse (global)": ["bulkDeactivateStyles"],
        "Project": ["createProject", "bulkDeactivateStyles"],
    },
    "bulkActivateLists": {
        "List (FONTLIST)": ["createAsset", "bulkActivateLists"],
        "Project": ["createProject", "createAsset", "bulkActivateLists"],
    },
    "bulkDeactivateLists": {
        "List (FONTLIST)": ["createAsset", "bulkDeactivateLists"],
        "Project": ["createProject", "createAsset", "bulkDeactivateLists"],
    },
}

FLOW_DEFS["deactivateVariation"] = {
    "Discovery/Browse (global)": ["activateVariation", "deactivateVariation"],
    "List (FONTLIST)": [
        "createAsset",
        "addFontListStyles",
        "activateVariation",
        "deactivateVariation",
    ],
    "Favourite": [
        "removeFavoriteStyles",
        "addFavoriteStyles",
        "activateVariation",
        "deactivateVariation",
    ],
    "Project": [
        "createProject",
        "addFontProjectStyles",
        "activateVariation",
        "deactivateVariation",
    ],
    "Project > List": [
        "createProject",
        "addFontProjectStyles",
        "createAsset",
        "addFontListStyles",
        "activateVariation",
        "deactivateVariation",
    ],
}

# Single-step creates (UI Navigation Discovery/Browse) — cleanup via GENERATE_CLEANUP deletes
FLOW_DEFS.update(
    {
        "createAsset": {
            "Discovery/Browse (global)": ["createAsset"],
            "List (FONTLIST)": ["createAsset"],
            "Project > List": ["createProject", "createAsset"],
        },
        "createProject": {
            "Discovery/Browse (global)": ["createProject"],
            "Projects": ["createProject"],
        },
        "addFontProjectStyles": {
            "Project": ["createProject", "addFontProjectStyles"],
            "Discovery/Browse (global)": ["createProject", "addFontProjectStyles"],
        },
        "addFavoriteFamilies": {
            "Favourite": ["removeFavoriteFamilies", "addFavoriteFamilies"],
        },
        "addFavoriteStyles": {
            "Favourite": ["removeFavoriteStyles", "addFavoriteStyles"],
        },
        "removeFavoriteFamilies": {
            "Favourite": ["addFavoriteFamilies", "removeFavoriteFamilies"],
        },
        "removeFavoriteStyles": {
            "Favourite": ["addFavoriteStyles", "removeFavoriteStyles"],
        },
        "dismissNotification": {
            "Discovery/Browse (global)": ["getNotifications", "dismissNotification"],
            "Notifications": ["getNotifications", "dismissNotification"],
        },
        "markNotificationRead": {
            "Discovery/Browse (global)": ["getNotifications", "markNotificationRead"],
            "Notifications": ["getNotifications", "markNotificationRead"],
        },
        "updateCustomerSettings": {
            "Discovery/Browse (global)": ["getCustomerSettings", "updateCustomerSettings"],
            "Account & workspace": ["getCustomerSettings", "updateCustomerSettings"],
        },
        "getCustomerSettings": {
            "Discovery/Browse (global)": ["getCustomerSettings"],
            "Account & workspace": ["getCustomerSettings"],
        },
        "setLanguagePreference": {
            "Discovery/Browse (global)": ["setLanguagePreference"],
            "Preferences": ["setLanguagePreference"],
        },
        "addFavoritePair": {
            "Favourite": ["removeFavoritePair", "addFavoritePair"],
            "Discovery/Browse (global)": ["removeFavoritePair", "addFavoritePair"],
        },
        "removeFavoritePair": {
            "Favourite": ["addFavoritePair", "removeFavoritePair"],
            "Discovery/Browse (global)": ["addFavoritePair", "removeFavoritePair"],
        },
        "bulkUpdateProfiles": {
            "Discovery/Browse (global)": [
                "getProfiles",
                "createTeam",
                "bulkUpdateProfiles",
            ],
            "User & Access": ["getProfiles", "createTeam", "bulkUpdateProfiles"],
        },
        "createUserInvitations": {
            "Discovery/Browse (global)": ["getRoles", "createUserInvitations"],
            "User & Access": ["getRoles", "createUserInvitations"],
        },
        "deleteRoles": {
            "Discovery/Browse (global)": ["createRole", "deleteRoles"],
            "User & Access": ["createRole", "deleteRoles"],
        },
        "deleteTeams": {
            "Discovery/Browse (global)": ["createTeam", "deleteTeams"],
            "User & Access": ["createTeam", "deleteTeams"],
        },
        "duplicateProject": {
            "Project": ["createProject", "duplicateProject"],
            "Discovery/Browse (global)": ["createProject", "duplicateProject"],
        },
        # Sheet multi-step: create → seed → trigger
        "removeFontProjectStyles": {
            "Project": ["createProject", "addFontProjectStyles", "removeFontProjectStyles"],
            "Discovery/Browse (global)": [
                "createProject",
                "addFontProjectStyles",
                "removeFontProjectStyles",
            ],
        },
        "updateAssets": {
            "Discovery/Browse (global)": ["createAsset", "updateAssets"],
        },
        "pinAsset": {
            "Discovery/Browse (global)": ["createAsset", "pinAsset"],
        },
        "unpinAsset": {
            "Discovery/Browse (global)": ["createAsset", "pinAsset", "unpinAsset"],
        },
        "updatePrivateTag": {
            "Manage>Tags": ["createPrivateTags", "updatePrivateTag"],
            "Discovery/Browse (global)": ["createPrivateTags", "updatePrivateTag"],
        },
        "updatePrivateTagAssociations": {
            "Browse/Discovery/List/TagsManage": [
                "createPrivateTags",
                "updatePrivateTagAssociations",
            ],
            "Discovery/Browse (global)": [
                "createPrivateTags",
                "updatePrivateTagAssociations",
            ],
        },
        "bulkTagStyles": {
            "Discovery/Browse": ["createPrivateTags", "bulkTagStyles"],
            "Discovery/Browse (global)": ["createPrivateTags", "bulkTagStyles"],
        },
        "bulkUntagStyles": {
            "Discovery/Browse": ["createPrivateTags", "bulkTagStyles", "bulkUntagStyles"],
            "Discovery/Browse (global)": [
                "createPrivateTags",
                "bulkTagStyles",
                "bulkUntagStyles",
            ],
        },
        "bulkAddStylesToList": {
            "Browse/Discovery": ["createAsset", "bulkAddStylesToList"],
            "Discovery/Browse (global)": ["createAsset", "bulkAddStylesToList"],
        },
        "bulkRemoveStylesFromList": {
            "Mylibrary>Assets": ["createAsset", "bulkAddStylesToList", "bulkRemoveStylesFromList"],
            "Discovery/Browse (global)": [
                "createAsset",
                "bulkAddStylesToList",
                "bulkRemoveStylesFromList",
            ],
        },
        "bulkCopyAssets": {
            "Mylibrary>Assets": ["createAsset", "createFolder", "bulkCopyAssets"],
            "Discovery/Browse (global)": ["createAsset", "createFolder", "bulkCopyAssets"],
        },
        "bulkMoveAssets": {
            "Mylibrary>Assets": ["createAsset", "createFolder", "bulkMoveAssets"],
            "Discovery/Browse (global)": ["createAsset", "createFolder", "bulkMoveAssets"],
        },
        "bulkRemoveStylesFromFavourites": {
            "Favourite": ["bulkAddStylesToFavourites", "bulkRemoveStylesFromFavourites"],
            "Discovery/Browse (global)": [
                "bulkAddStylesToFavourites",
                "bulkRemoveStylesFromFavourites",
            ],
        },
        "updateSessionFiles": {
            "Discovery/Browse (global)": ["createUploadSession", "updateSessionFiles"],
        },
        "processUploadSessionFonts": {
            "Discovery/Browse (global)": [
                "createUploadSession",
                "updateSessionFiles",
                "processUploadSessionFonts",
            ],
        },
        "parseAndCreateContract": {
            "Discovery/Browse (global)": [
                "createUploadSession",
                "parseAndCreateContract",
            ],
        },
        "cancelBatch": {
            "Discovery/Browse (global)": ["bulkActivateStyles", "cancelBatch"],
        },
        "createServiceAccount": {
            "Discovery/Browse (global)": ["createServiceAccount"],
        },
        "syncUnSyncVariations": {
            "Discovery/Browse (global)": ["syncUnSyncVariations"],
        },
        "submitIntentForProduction": {
            "Discovery/Browse (global)": ["submitIntentForProduction"],
        },
        "addStyleDocument": {
            "Discovery/Browse (global)": ["addStyleDocument"],
        },
        "createContract": {
            "Discovery/Browse (global)": ["createContract"],
        },
    }
)

# "Search/ Family / Discovery" is the same path as Discovery/Browse (global).
# Do NOT add it as a second FLOW_DEFS key — that duplicated generate runs.
# Selection aliases are resolved in scenarios.expand_selection_to_scenarios.

FLOW_DEFS.update(export_flow_defs())
