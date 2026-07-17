"""Schema-correct GraphQL variables for TouchPoint curls / Postman.

Source of truth: mtf-graphql-schema (AddFontListFamiliesInput, ActivateFamilyInput, …)
and mtconnect-api flow call-sites. Never invent field names (e.g. listId ≠ fontListId).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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


def variables_for(operation: str, seed: SeedIds, *, touch: str = "") -> dict[str, Any]:
    """Full GraphQL ``variables`` object for ``operation`` at ``touch``."""
    op = operation
    t = touch or "Discovery/Browse (global)"
    with_project = "project" in t.lower() and "list" not in t.lower()

    builders = {
        "createAsset": lambda: create_font_list(seed),
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
        # Favourites extended
        "removeFavoriteFamilies": lambda: {"input": {"familyIds": [seed.family_id]}},
        "removeFavoriteStyles": lambda: {"input": {"styleIds": [seed.style_id]}},
        "addFavoritePair": lambda: {
            "input": {
                "headlineStyleId": seed.headline_style_id or seed.style_id,
                "bodyStyleId": seed.body_style_id or seed.style_id,
            }
        },
        "removeFavoritePair": lambda: {
            "input": {
                "headlineStyleId": seed.headline_style_id or seed.style_id,
                "bodyStyleId": seed.body_style_id or seed.style_id,
            }
        },
        "bulkAddPairsToFavorite": lambda: {
            "input": {
                "pairs": [
                    {
                        "headlineStyleId": seed.headline_style_id or seed.style_id,
                        "bodyStyleId": seed.body_style_id or seed.style_id,
                    }
                ]
            }
        },
        "bulkRemovePairsFromFavorite": lambda: {
            "input": {
                "pairs": [
                    {
                        "headlineStyleId": seed.headline_style_id or seed.style_id,
                        "bodyStyleId": seed.body_style_id or seed.style_id,
                    }
                ]
            }
        },
        "bulkAddStylesToFavourites": lambda: {
            "input": {"styles": [{"id": seed.style_id}]}
        },
        "bulkRemoveStylesFromFavourites": lambda: {
            "input": {"styleIds": [seed.style_id]}
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
                "assets": [
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
                        "target": {"assetId": "root"},
                    }
                ]
            }
        },
        "bulkMoveAssets": lambda: {
            "input": {
                "sourceAssetIds": [seed.list_id],
                "targetAssetId": "root",
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
                "styles": [{"styleId": seed.style_id}],
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
                "id": seed.tag_id,
                "name": f"QA_Tag_Updated_{seed.style_id[-4:] or 'tmp'}",
            }
        },
        "deletePrivateTags": lambda: {
            "input": {"customerId": seed.customer_id, "ids": [seed.tag_id]}
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
        "deleteTeams": lambda: {"input": {"ids": [seed.team_id]}},
        "createUserInvitations": lambda: {
            "input": {
                "customerId": seed.customer_id,
                "data": [
                    {
                        "email": f"qa.touchpoint+{seed.style_id[-6:] or 'x'}@example.com",
                        "roleId": seed.role_id,
                    }
                ],
            }
        },
        "updateProfile": lambda: {
            "input": {"id": seed.profile_id, "isActive": True}
        },
        "setLanguagePreference": lambda: {"input": {"language": "en"}},
        "markOnboardingCompleted": lambda: {},
        "deleteCompanyLogo": lambda: {},
        "markCompanyLogoUploadSuccess": lambda: {},
        "resetPreferences": lambda: {},
        "globalEmailOptOut": lambda: {},
        # Notifications
        "dismissNotification": lambda: {
            "input": {"notificationId": seed.notification_id}
        },
        "markNotificationRead": lambda: {
            "input": {"notificationId": seed.notification_id}
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
                "action": "READ",
                "notificationIds": [seed.notification_id],
            }
        },
        "applyUnsubscribe": lambda: {"token": "QA_PLACEHOLDER_UNSUBSCRIBE_TOKEN"},
        # Imported / production
        "markProductionFonts": lambda: {
            "input": {"styleIds": [seed.style_id], "inProduction": True}
        },
        "bulkUnmarkProductionFont": lambda: {
            "input": {"styleIds": [seed.style_id]}
        },
        "submitIntentForProduction": lambda: {
            "input": {"styleIds": [seed.style_id]}
        },
        "denyIntentForProduction": lambda: {
            "input": {"styleIds": [seed.style_id]}
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
        "duplicateProject": lambda: {
            "input": {
                "sourceProjectId": seed.project_id,
                "name": "QA_TouchPoint_Project_Copy",
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
                "styleIds": [],
                "isFreeToUse": False,
                "licenceType": "DESKTOP",
                "isReviewed": False,
                "linkedImportedFontScope": "GLOBAL",
                "licenceName": "QA_TouchPoint_Licence",
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
                "serverName": "QA_TouchPoint_SA",
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
        "updateCustomerSettings": lambda: {
            "input": {"displayName": "QA TouchPoint Company"}
        },
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
                    "fileName": "qa-touchpoint-scan.pdf",
                    "size": 1024,
                    "contentType": "application/pdf",
                    "fileId": seed.file_id or None,
                }
            ],
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
                "targetProfileIds": [seed.profile_id],
                "action": "CHANGE_TEAMS",
                "operation": {"teamIds": []},
            }
        },
        "resetPassword": lambda: {"input": {"profileId": seed.profile_id}},
        "deleteProfiles": lambda: {
            "input": {"profileIds": [seed.profile_id]}
        },
        "createRole": lambda: {
            "input": {
                "customerId": seed.customer_id,
                "name": "QA_TouchPoint_Role",
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
                "ids": [seed.role_id],
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
FLOW_DEFS: dict[str, dict[str, list[str]]] = {
    "activateFamily": {
        "Discovery/Browse (global)": ["activateFamily"],
        "List (FONTLIST)": ["createAsset", "addFontListFamilies", "activateFamily"],
        "Favourite": ["addFavoriteFamilies", "activateFamily"],
        "Project": ["createProject", "addFontProjectFamilies", "activateFamily"],
        "Project > List": [
            "createProject",
            "addFontProjectFamilies",
            "createAsset",
            "addFontListFamilies",
            "activateFamily",
        ],
    },
    "deactivateFamilies": {
        "Discovery/Browse (global)": ["deactivateFamilies"],
        "List (FONTLIST)": ["createAsset", "addFontListFamilies", "deactivateFamilies"],
        "Favourite": ["addFavoriteFamilies", "deactivateFamilies"],
        "Project": ["createProject", "addFontProjectFamilies", "deactivateFamilies"],
        "Project > List": [
            "createProject",
            "createAsset",
            "addFontListFamilies",
            "deactivateFamilies",
        ],
    },
    "activateStyle": {
        "Discovery/Browse (global)": ["activateStyle"],
        "List (FONTLIST)": ["createAsset", "addFontListStyles", "activateStyle"],
        "Favourite": ["addFavoriteStyles", "activateStyle"],
        "Project": ["createProject", "addFontProjectStyles", "activateStyle"],
        "Project > List": [
            "createProject",
            "createAsset",
            "addFontListStyles",
            "activateStyle",
        ],
    },
    "deactivateStyle": {
        "Discovery/Browse (global)": ["deactivateStyle"],
        "List (FONTLIST)": ["createAsset", "addFontListStyles", "deactivateStyle"],
        "Favourite": ["addFavoriteStyles", "deactivateStyle"],
        "Project": ["createProject", "addFontProjectStyles", "deactivateStyle"],
        "Project > List": [
            "createProject",
            "createAsset",
            "addFontListStyles",
            "deactivateStyle",
        ],
    },
    "activateVariation": {
        "Discovery/Browse (global)": ["activateVariation"],
        "List (FONTLIST)": ["createAsset", "addFontListStyles", "activateVariation"],
        "Favourite": ["addFavoriteStyles", "activateVariation"],
        "Project": ["createProject", "addFontProjectStyles", "activateVariation"],
        "Project > List": [
            "createProject",
            "createAsset",
            "addFontListStyles",
            "activateVariation",
        ],
    },
    "activateList": {
        "List (FONTLIST)": ["createAsset", "addFontListFamilies", "activateList"],
        "Favourite": ["addFavoriteFamilies", "activateList"],
        "Project > List": [
            "createProject",
            "createAsset",
            "addFontListFamilies",
            "activateList",
        ],
    },
    "deActivateList": {
        "List (FONTLIST)": ["createAsset", "addFontListFamilies", "deActivateList"],
        "Favourite": ["addFavoriteFamilies", "deActivateList"],
        "Project > List": [
            "createProject",
            "createAsset",
            "addFontListFamilies",
            "deActivateList",
        ],
    },
    "activateFontProject": {
        "Project": ["createProject", "addFontProjectFamilies", "activateFontProject"],
    },
    "deActivateFontProject": {
        "Project": ["createProject", "addFontProjectFamilies", "deActivateFontProject"],
    },
    "addFontListFamilies": {
        "List (FONTLIST)": ["createAsset", "addFontListFamilies"],
        "Project > List": ["createProject", "createAsset", "addFontListFamilies"],
    },
    "addFontListStyles": {
        "List (FONTLIST)": ["createAsset", "addFontListStyles"],
        "Project > List": ["createProject", "createAsset", "addFontListStyles"],
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
    k: [x if x != "activateVariation" else "deactivateVariation" for x in v]
    for k, v in FLOW_DEFS["activateVariation"].items()
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
            "Favourite": ["addFavoriteFamilies"],
        },
        "addFavoriteStyles": {
            "Favourite": ["addFavoriteStyles"],
        },
    }
)

# Sheet alias: Search/ Family / Discovery ≡ Discovery/Browse (global)
for _op, _touches in list(FLOW_DEFS.items()):
    if "Discovery/Browse (global)" in _touches and "Search/ Family / Discovery" not in _touches:
        FLOW_DEFS[_op]["Search/ Family / Discovery"] = list(
            _touches["Discovery/Browse (global)"]
        )
