"""UI navigation hints, GraphQL export mapping, and cURL templates per operation."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from pathlib import Path

from typing import Any

from dotenv import dotenv_values

from .operation_graphql import get_export_for_operation, is_nextgen_ui_operation, operation_graphql_export_map
from ..simulation.client import apollo_operation_name
from ..simulation.flow_catalog import audit_operation
from ..simulation.graphql_loader import get_document
from ..simulation.postman_payloads import SIMULATION_PAYLOAD_OVERRIDES

# Built dynamically from data/graphql_documents.json (+ routing-map aliases).
OPERATION_GRAPHQL_EXPORT: dict[str, str] = operation_graphql_export_map()

_PKG_DATA = Path(__file__).resolve().parent.parent / "data"


def _load_api_operation_variables() -> dict[str, dict]:
    """Seed payloads from mtconnect-api resolver / subjectExtractor tests."""
    path = _PKG_DATA / "api_operation_variables.json"
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not str(k).startswith("_")}


def merged_operation_variables_template() -> dict[str, dict]:
    """API test seeds first; hand-tuned OPERATION_VARIABLES_TEMPLATE overrides."""
    merged = copy.deepcopy(_load_api_operation_variables())
    merged.update(OPERATION_VARIABLES_TEMPLATE)
    return merged


# Example variables JSON (placeholders) — matches automation flows
OPERATION_VARIABLES_TEMPLATE: dict[str, dict] = {
    "activateFamily": {"input": {"familyIds": ["$SEED_FAMILY_ID"]}},
    "deactivateFamilies": {
        "input": {
            "familyIds": ["$SEED_DEACTIVATE_FAMILY_ID"],
            "deactivationType": "PERMANENT",
        }
    },
    "activateStyle": {"input": {"styleIds": ["$SEED_STYLE_ID"]}},
    "deactivateStyle": {"input": {"styleIds": ["$SEED_STYLE_ID"]}},
    "activateVariation": {
        "input": {
            "variations": [{"styleId": "$SEED_STYLE_ID", "md5": "$SEED_VARIATION_MD5"}]
        }
    },
    "deactivateVariation": {"input": {"md5s": ["$SEED_VARIATION_MD5"]}},
    "bulkActivateStyles": {
        "input": {
            "styles": [
                {"id": "920373381", "metadata": {"styleName": "Light"}},
                {"id": "920373382", "metadata": {"styleName": "Medium"}},
            ],
            "activationType": "PERMANENT",
        }
    },
    "bulkDeactivateStyles": {
        "input": {
            "styles": [
                {"id": "920373381", "metadata": {"styleName": "Light"}},
                {"id": "920373382", "metadata": {"styleName": "Medium"}},
            ],
            "deactivationType": "PERMANENT",
        }
    },
    "addFavoriteFamilies": {"input": {"familyIds": ["$SEED_FAMILY_ID"]}},
    "removeFavoriteFamilies": {"input": {"familyIds": ["$SEED_FAMILY_ID"]}},
    "addFavoritePair": {
        "input": {
            "pairs": [
                {
                    "headline": {"id": "$SEED_HEADLINE_STYLE_ID"},
                    "body": {"id": "$SEED_BODY_STYLE_ID"},
                }
            ]
        }
    },
    "removeFavoritePair": {
        "input": {
            "pairs": [
                {
                    "headline": {"id": "$SEED_HEADLINE_STYLE_ID"},
                    "body": {"id": "$SEED_BODY_STYLE_ID"},
                }
            ]
        }
    },
    "addFavoriteStyles": {"input": {"styleIds": ["$SEED_STYLE_ID"]}},
    "removeFavoriteStyles": {"input": {"styleIds": ["$SEED_STYLE_ID"]}},
    "bulkAddStylesToFavourites": {
        "input": {
            "styles": [
                {"id": "920373384", "metadata": {"styleName": "Bold"}},
                {"id": "920373386", "metadata": {"styleName": "Medium Italic"}},
            ]
        }
    },
    "bulkRemoveStylesFromFavourites": {"input": {"styles": [{"id": "$SEED_STYLE_ID"}]}},
    "addFontListFamilies": {
        "input": {
            "fontListId": "$FONT_LIST_ID",
            "families": {"familyIds": ["$SEED_FAMILY_ID"]},
        },
        "styleFilterInput": {"pagination": {"skip": 0, "limit": 10}},
    },
    "removeFontListFamilies": {
        "input": {
            "fontListId": "$FONT_LIST_ID",
            "families": {"familyIds": ["$SEED_FAMILY_ID"]},
        },
        "styleFilterInput": {"pagination": {"skip": 0, "limit": 10}},
    },
    "addFontListStyles": {
        "input": {"fontListId": "$FONT_LIST_ID", "styles": [{"styleId": "$SEED_STYLE_ID"}]}
    },
    "removeFontListStyles": {
        "input": {
            "fontListId": "$FONT_LIST_ID",
            "styles": {"styleIds": ["$SEED_STYLE_ID"]},
        },
        "styleFilterInput": {"pagination": {"skip": 0, "limit": 10}},
    },
    "activateList": {
        "input": {
            "listId": "$FONT_LIST_ID",
            "listType": "FONTLIST",
            "activationType": "PERMANENT",
        }
    },
    "deActivateList": {
        "input": {
            "listId": "$FONT_LIST_ID",
            "listType": "FONTLIST",
            "deactivationType": "PERMANENT",
        }
    },
    "bulkAddStylesToList": {
        "input": {"listId": "$FONT_LIST_ID", "styles": [{"id": "$SEED_STYLE_ID"}]}
    },
    "bulkRemoveStylesFromList": {
        "input": {"listId": "$FONT_LIST_ID", "styles": [{"id": "$SEED_STYLE_ID"}]}
    },
    "addFontProjectFamilies": {
        "input": {
            "fontProjectId": "$PROJECT_ID",
            "families": {"familyIds": ["$SEED_FAMILY_ID"]},
        }
    },
    "removeFontProjectFamilies": {
        "input": {
            "fontProjectId": "$PROJECT_ID",
            "families": {"familyIds": ["$SEED_REMOVE_PROJECT_FAMILY_ID"]},
        }
    },
    "addFontProjectStyles": {
        "input": {
            "fontProjectId": "$PROJECT_ID",
            "styles": {"styleIds": ["$SEED_STYLE_ID"]},
        }
    },
    "removeFontProjectStyles": {
        "input": {
            "fontProjectId": "$PROJECT_ID",
            "styles": {"styleIds": ["$SEED_STYLE_ID"]},
        }
    },
    "activateFontProject": {"input": {"projectId": "$PROJECT_ID"}},
    "deActivateFontProject": {"input": {"projectId": "$PROJECT_ID"}},
    "publishProject": {"input": {"projectId": "$PROJECT_ID"}},
    "createProject": {
        "input": {
            "name": "automation-project",
            "description": "Automation test project",
            "allowFontAdditionsByCollaborators": False,
            "allowFontDownloadsByCollaborators": False,
            "allowFontImportsByCollaborators": False,
            "enableProjectLevelImportedFonts": False,
            "autoActivateFontsForMembers": False,
        }
    },
    "publishProject": {"input": {"projectId": "$PROJECT_ID"}},
    "deleteProject": {"input": {"projectId": "$PROJECT_ID"}},
    "addStylesToWebProject": {
        "input": {
            "webProjectId": "$WEB_PROJECT_ID",
            "styles": [{"id": "$SEED_STYLE_ID", "isEmbedded": False}],
        }
    },
    "removeStylesFromWebProject": {
        "input": {"webProjectId": "$WEB_PROJECT_ID", "styles": ["$SEED_STYLE_ID"]}
    },
    "createWebProject": {
        "input": {"styles": [{"id": "$SEED_STYLE_ID", "isEmbedded": True}]}
    },
    "downloadWebProject": {"input": {"id": "$WEB_PROJECT_ID"}},
    "bulkTagStyles": {
        "input": {"tagId": "$TAG_ID", "styles": [{"id": "$SEED_STYLE_ID"}]}
    },
    "bulkUntagStyles": {
        "input": {"tagId": "$TAG_ID", "styles": [{"id": "$SEED_STYLE_ID"}]}
    },
    "createAsset": {
        "input": {
            "name": "automation-folder",
            "assetType": "Folder",
            "accessRight": "FullAccess",
        }
    },
    "updateAsset": {
        "input": {
            "assetType": "Folder",
            "assetId": "$ASSET_ID",
            "name": "automation-folder-renamed",
        }
    },
    "updateAssetSharing": {
        "input": {
            "assetId": "$ASSET_ID",
            "assetType": "Folder",
            "notify": False,
            "data": [
                {
                    "action": "GRANT",
                    "payload": [
                        {
                            "shareeType": "User",
                            "accessIdMap": [
                                {"accessId": 27, "shareeId": "$SEED_SHARING_SHAREE_ID"}
                            ],
                        }
                    ],
                }
            ],
        }
    },
    "bulkCopyAssets": {
        "input": {
            "items": [
                {
                    "source": {"assetId": "$ASSET_ID", "assetType": "Folder"},
                    "target": {"assetId": "root"},
                }
            ]
        }
    },
    "bulkMoveAssets": {
        "input": {"sourceAssetIds": ["$COPIED_ASSET_ID"], "targetAssetId": "$ASSET_ID"}
    },
    "updateAssets": {
        "input": {
            "items": [
                {
                    "assetType": "Folder",
                    "assetId": "$ASSET_ID",
                    "description": "automation description",
                }
            ]
        }
    },
    "pinAsset": {"input": {"assetId": "$ASSET_ID"}},
    "unpinAsset": {"input": {"assetId": "$ASSET_ID"}},
    "deleteAssets": {
        "input": {"assets": [{"assetType": "Folder", "assetIds": ["$ASSET_ID"]}]}
    },
    "createPrivateTags": {
        "input": {"customerId": "$CUSTOMER_ID", "tags": [{"name": "automation-tag"}]}
    },
    "updatePrivateTag": {
        "input": {
            "tagId": "$TAG_ID",
            "customerId": "$CUSTOMER_ID",
            "name": "automation-tag-updated",
        }
    },
    "updatePrivateTagAssociations": {
        "input": {
            "customerId": "$CUSTOMER_ID",
            "tags": [{"id": "$TAG_ID", "associate": [{"styleId": "$SEED_STYLE_ID"}]}],
        }
    },
    "deletePrivateTags": {
        "input": {"customerId": "$CUSTOMER_ID", "tagIds": ["$TAG_ID"]}
    },
    "deleteAllPrivateTags": {"input": {"customerId": "$CUSTOMER_ID"}},
    "createRole": {
        "input": {
            "customerId": "$CUSTOMER_ID",
            "name": "automation-role",
            "description": "",
            "addProfileIds": [],
            "permissionGroups": [
                "SEARCH_AND_DISCOVER_FONTS",
                "ACTIVATE_PROJECT_FONTS",
                "ACTIVATE_LIST_FONTS",
                "INTENT_FOR_PRODUCTION",
                "CREATE_PROJECTS",
                "DOWNLOAD_FONTS",
                "APPLY_PRIVATE_TAGS",
                "CREATE_WEBKITS",
                "DOWNLOAD_WEB_SHKS",
            ],
        }
    },
    "updateRole": {
        "input": {
            "customerId": "$CUSTOMER_ID",
            "id": "$ROLE_ID",
            "name": "automation-role-updated",
            "description": None,
        }
    },
    "deleteRoles": {"input": {"customerId": "$CUSTOMER_ID", "ids": ["$ROLE_ID"]}},
    "createTeam": {"input": {"name": "automation-team", "description": ""}},
    "updateTeam": {
        "input": {
            "id": "$TEAM_ID",
            "name": "automation-team-updated",
            "description": "",
            "addProfiles": ["$PROFILE_ID"],
        }
    },
    "deleteTeams": {"input": {"ids": ["$TEAM_ID"]}},
    "updateProfile": {
        "input": {
            "id": "$PROFILE_ID",
            "firstName": "Automation",
            "lastName": "User",
            "roleId": "$ROLE_ID",
            "tempUserExpiryDate": None,
        }
    },
    "bulkUpdateProfiles": {
        "input": {
            "targetProfileIds": ["$PROFILE_ID"],
            "action": "CHANGE_TEAMS",
            "operation": {"teamIds": []},
        }
    },
    "resetPassword": {"input": {"profileId": "$PROFILE_ID"}},
    "createUserInvitations": {
        "input": {
            "customerId": "$CUSTOMER_ID",
            "data": [
                {
                    "emails": ["automation-test-invite@automation.com"],
                    "status": 1,
                    "roleId": "$ROLE_ID",
                    "teamIds": [],
                    "tempUserExpiryDate": None,
                    "emailLocale": "EN",
                }
            ],
        }
    },
    "updateUserInvitations": {
        "input": {
            "customerId": "$CUSTOMER_ID",
            "data": [{"invitationId": "$INVITATION_ID", "status": 0}],
        }
    },
    "createCustomer": {
        "input": {
            "name": "Everest_Test_Customer",
            "invitedPrimaryContactEmail": "everest-test-primary@everest-test.com",
            "isTrial": True,
            "productType": "ENTERPRISE",
        }
    },
    "updateCustomer": {
        "input": {"id": "$CUSTOMER_ID", "isTrial": True, "productType": "AGENCY"}
    },
    "createUploadSession": {
        "files": [{"fileName": "test.pdf", "contentType": "application/pdf", "size": 1024}]
    },
    "markSessionFileUploaded": {"fileId": "$FILE_ID", "sessionId": "$SESSION_ID"},
    "updateSessionFiles": {"files": [], "sessionId": "$SESSION_ID"},
    "completeUploadSession": {"sessionId": "$SESSION_ID"},
    "markProductionFonts": {
        "input": {
            "companyId": "$CUSTOMER_ID",
            "styleIds": ["$SEED_STYLE_ID"],
            "sourceContext": "LIBRARY",
        }
    },
    "updateProductionFont": {
        "input": {
            "companyId": "$CUSTOMER_ID",
            "updateProductionFonts": [
                {"styleId": "$SEED_STYLE_ID", "inProduction": True}
            ],
            "sourceContext": "LIBRARY",
        }
    },
    "getPackageId": {
        "input": {
            "styleIds": ["$SEED_STYLE_ID"],
            "md5s": ["$SEED_VARIATION_MD5"],
        }
    },
    "getFamiliesOfAllFontLists": {
        "input": {}
    },
    "getStylesOfAllFontLists": {
        "input": {}
    },
    "getProjectByDocumentId": {"documentId": "$DOCUMENT_ID"},
    "sharingInfoForAssets": {
        "assets": [{"id": "$ASSET_ID", "assetType": "Folder"}]
    },
    "getImportedFonts": {
        "input": {"pagination": {"skip": 0, "limit": 10}}
    },
    "getCategorizedGlyphs": {
        "input": {"styleId": "$SEED_STYLE_ID", "md5": "$SEED_VARIATION_MD5"}
    },
    "getSsoMappings": {
        "filter": {},
        "pagination": {"skip": 0, "limit": 10},
    },
    "createBYOFBatchAndCheckDuplicates": {
        "input": {
            "fontMD5s": [
                {
                    "md5": "$SEED_VARIATION_MD5",
                    "fileName": "automation-test.ttf",
                    "extension": "ttf",
                    "sizeBytes": 100,
                }
            ]
        }
    },
    "createContract": {
        "input": {
            "styleIds": [],
            "isFreeToUse": False,
            "licenceType": "DESKTOP",
            "isReviewed": False,
            "linkedImportedFontScope": "GLOBAL",
            "licenceName": "automation-licence",
        }
    },
    "updateContract": {
        "input": {
            "contractId": "$CONTRACT_ID",
            "changedFields": {"licenceName": "automation-updated", "isReviewed": True},
        }
    },
    "linkContractToStyles": {
        "input": {"contractId": "$CONTRACT_ID", "styleIds": ["$SEED_STYLE_ID"]}
    },
    "unlinkStyleFromContract": {
        "input": {"contractId": "$CONTRACT_ID", "styleIds": ["$SEED_STYLE_ID"]}
    },
}

UI_NAVIGATION: dict[str, str] = {
    "activateFamily": "Dashboard > Search > Select font family > Family details > Activate",
    "deactivateFamilies": "Dashboard > Search > Select font family > Family details > Deactivate",
    "activateStyle": "Dashboard > Search > Select style > Activate",
    "deactivateStyle": "Dashboard > Activated fonts > Select style > Deactivate",
    "activateVariation": "Dashboard > Font details > Variations > Activate variation",
    "deactivateVariation": "Dashboard > Font details > Variations > Deactivate variation",
    "bulkActivateStyles": "Dashboard > Multi-select styles > Activate",
    "bulkDeactivateStyles": "Dashboard > Multi-select styles > Deactivate",
    "addFavoriteFamilies": "Dashboard > Search > Family card > Add to favorites",
    "removeFavoriteFamilies": "Dashboard > Favorites > Remove family",
    "addFavoritePair": "Dashboard > Pairing view > Add pair to favorites",
    "removeFavoritePair": "Dashboard > Favorites > Remove pair",
    "addFavoriteStyles": "Dashboard > Search > Style > Add to favorites",
    "removeFavoriteStyles": "Dashboard > Favorites > Remove style",
    "bulkAddStylesToFavourites": "Dashboard > Multi-select styles > Add to favorites",
    "bulkRemoveStylesFromFavourites": "Dashboard > Favorites > Multi-select > Remove",
    "addFontListFamilies": "Dashboard > Assets > Font list > Add families",
    "removeFontListFamilies": "Dashboard > Assets > Font list > Remove families",
    "addFontListStyles": "Dashboard > Assets > Font list > Add styles",
    "removeFontListStyles": "Dashboard > Assets > Font list > Remove styles",
    "activateList": "Dashboard > Assets > Font list > Activate list",
    "deActivateList": "Dashboard > Assets > Font list > Deactivate list",
    "bulkAddStylesToList": "Dashboard > Multi-select styles > Add to list",
    "bulkRemoveStylesFromList": "Dashboard > Assets > Font list > Remove styles (bulk)",
    "addFontProjectFamilies": "Projects > Open project > Add families",
    "removeFontProjectFamilies": "Projects > Open project > Remove families",
    "addFontProjectStyles": "Projects > Open project > Add styles",
    "removeFontProjectStyles": "Projects > Open project > Remove styles",
    "createProject": "Projects > Create font project",
    "publishProject": "Projects > Open project > Publish",
    "deleteProject": "Projects > Open project > Delete",
    "addStylesToWebProject": "Web projects > Open project > Add fonts",
    "removeStylesFromWebProject": "Web projects > Open project > Remove fonts",
    "createWebProject": "Web projects > Create web project",
    "downloadWebProject": "Web projects > Open project > Download kit",
    "bulkTagStyles": "Dashboard > Tags > Apply tag to selected styles",
    "bulkUntagStyles": "Dashboard > Tags > Remove tag from selected styles",
    "createAsset": "Dashboard > Assets > Create folder or font list",
    "updateAsset": "Dashboard > Assets > Folder > Rename",
    "updateAssetSharing": "Dashboard > Assets > Share > Grant or revoke access",
    "bulkCopyAssets": "Dashboard > Assets > Multi-select > Copy",
    "bulkMoveAssets": "Dashboard > Assets > Multi-select > Move",
    "updateAssets": "Dashboard > Assets > Bulk edit description",
    "pinAsset": "Dashboard > Assets > Pin folder",
    "unpinAsset": "Dashboard > Assets > Unpin folder",
    "deleteAssets": "Dashboard > Assets > Delete",
    "createPrivateTags": "Dashboard > Tags > Create private tag",
    "updatePrivateTag": "Dashboard > Tags > Edit tag name",
    "updatePrivateTagAssociations": "Dashboard > Tags > Associate or disassociate styles",
    "deletePrivateTags": "Dashboard > Tags > Delete tag",
    "deleteAllPrivateTags": "Dashboard > Tags > Delete all tags (admin)",
    "createRole": "Settings > Manage > Users & Teams > Roles > Add role",
    "updateRole": "Settings > Manage > Users & Teams > Roles > Edit role",
    "deleteRoles": "Settings > Manage > Users & Teams > Roles > Delete role",
    "createTeam": "Settings > Manage > Users & Teams > Teams > Create team",
    "updateTeam": "Settings > Manage > Users & Teams > Teams > Edit team",
    "deleteTeams": "Settings > Manage > Users & Teams > Teams > Delete team",
    "updateProfile": "Settings > Manage > Users > Edit user profile",
    "bulkUpdateProfiles": "Settings > Manage > Users > Bulk update profiles",
    "resetPassword": "Settings > Manage > Users > Reset password",
    "createUserInvitations": "Settings > Manage > Users > Invite users",
    "updateUserInvitations": "Settings > Manage > Users > Invitations > Cancel or update",
    "createCustomer": "Admin > Customers > Create customer",
    "updateCustomer": "Admin > Customers > Edit customer",
    "createUploadSession": "Document scanning > Start upload session",
    "markSessionFileUploaded": "Document scanning > Mark file uploaded",
    "updateSessionFiles": "Document scanning > Update session files",
    "completeUploadSession": "Document scanning > Complete session",
    "markProductionFonts": "Dashboard > Style > Mark as production font",
    "updateProductionFont": "Dashboard > Production fonts > Update status",
    "getPackageId": "Dashboard > Style > Download font package",
}


def build_curl(operation: str, project_root: Path) -> str:
    """Resolved cURL (BFF /graph for NextGen ops) with seed IDs from .env."""
    return build_curl_resolved(operation, project_root)


def ui_navigation(operation: str) -> str:
    return UI_NAVIGATION.get(operation, "")


_PLACEHOLDER = re.compile(r"^\$[A-Z0-9_]+$")


@dataclass(frozen=True)
class CurlContext:
    endpoint: str
    nextgen_endpoint: str
    nextgen_origin: str
    nextgen_referer: str
    bearer_token: str
    profile_id: str
    customer_id: str
    seed_family_id: str
    seed_deactivate_family_id: str
    seed_style_id: str
    seed_variation_md5: str


def load_curl_context(project_root: Path) -> CurlContext:
    """Load .env values and bootstrap profile/customer for resolved cURLs."""
    raw = {k: v for k, v in dotenv_values(project_root / ".env").items() if v}
    endpoint = raw.get("GRAPHQL_ENDPOINT", "").strip()
    nextgen_ui = raw.get("NEXTGEN_UI_URL", "https://nextgen-everest.monotype-dev.com").rstrip("/")
    nextgen_endpoint = raw.get("NEXTGEN_GRAPHQL_ENDPOINT", f"{nextgen_ui}/graph").strip()
    if not endpoint:
        endpoint = nextgen_endpoint
    token = raw.get("BEARER_TOKEN", "")
    profile_id = raw.get("PROFILE_ID", "")
    customer_id = raw.get("CUSTOMER_ID", "")

    if token and (not profile_id or not customer_id):
        try:
            import requests

            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "accept-language": raw.get("ACCEPT_LANGUAGE", "en"),
            }
            profile_query = "query GetProfile { getProfile { id customer { id } } }"
            if endpoint.rstrip("/").endswith("/graph"):
                headers.update(
                    {
                        "accept": "application/graphql-response+json,application/json;q=0.9",
                        "origin": raw.get("NEXTGEN_ORIGIN", nextgen_ui),
                        "referer": raw.get(
                            "NEXTGEN_REFERER", f"{nextgen_ui}/discover-fonts/all"
                        ),
                    }
                )
                payload = {
                    "operationName": "GetProfile",
                    "variables": {},
                    "extensions": {
                        "clientLibrary": {"name": "@apollo/client", "version": "4.0.9"},
                    },
                    "query": profile_query,
                }
            else:
                payload = {"query": profile_query}
            resp = requests.post(endpoint, headers=headers, json=payload, timeout=60)
            resp.raise_for_status()
            body = resp.json()
            profile = (body.get("data") or {}).get("getProfile") or body.get("getProfile") or {}
            profile_id = profile_id or (profile.get("id") or "")
            customer_id = customer_id or ((profile.get("customer") or {}).get("id") or "")
        except Exception:
            pass

    nextgen_origin = raw.get("NEXTGEN_ORIGIN", nextgen_ui).rstrip("/")
    nextgen_referer = raw.get(
        "NEXTGEN_REFERER", f"{nextgen_origin}/discover-fonts/all"
    ).strip()

    return CurlContext(
        endpoint=endpoint,
        nextgen_endpoint=nextgen_endpoint,
        nextgen_origin=nextgen_origin,
        nextgen_referer=nextgen_referer,
        bearer_token=token,
        profile_id=profile_id,
        customer_id=customer_id,
        seed_family_id=raw.get("SEED_FAMILY_ID", ""),
        seed_deactivate_family_id=raw.get("SEED_DEACTIVATE_FAMILY_ID", ""),
        seed_style_id=raw.get("SEED_STYLE_ID", ""),
        seed_variation_md5=raw.get("SEED_VARIATION_MD5", ""),
    )


def _placeholder_map(ctx: CurlContext, project_root: Path | None = None) -> dict[str, str]:
    raw: dict[str, str] = {}
    if project_root is not None:
        raw = {k: v for k, v in dotenv_values(project_root / ".env").items() if v}
    return {
        "SEED_FAMILY_ID": ctx.seed_family_id,
        "SEED_DEACTIVATE_FAMILY_ID": ctx.seed_deactivate_family_id or ctx.seed_family_id,
        "SEED_REMOVE_PROJECT_FAMILY_ID": raw.get(
            "SEED_REMOVE_PROJECT_FAMILY_ID",
            raw.get("SEED_DEACTIVATE_FAMILY_ID", ctx.seed_family_id),
        ),
        "SEED_STYLE_ID": ctx.seed_style_id,
        "SEED_VARIATION_MD5": ctx.seed_variation_md5,
        "SEED_VARIATION_ID": raw.get("SEED_VARIATION_ID", ""),
        "SEED_HEADLINE_STYLE_ID": raw.get("SEED_HEADLINE_STYLE_ID", ""),
        "SEED_BODY_STYLE_ID": raw.get("SEED_BODY_STYLE_ID", ""),
        "SEED_SHARING_SHAREE_ID": raw.get("SEED_SHARING_SHAREE_ID", ""),
        "PROFILE_ID": ctx.profile_id,
        "OTHER_PROFILE_ID": raw.get("OTHER_PROFILE_ID", ""),
        "CUSTOMER_ID": ctx.customer_id,
        "FONT_LIST_ID": raw.get("FONT_LIST_ID", ""),
        "PROJECT_ID": raw.get("PROJECT_ID", ""),
        "SECONDARY_PROJECT_ID": raw.get("SECONDARY_PROJECT_ID", ""),
        "DUPLICATE_PROJECT_ID": raw.get("DUPLICATE_PROJECT_ID", ""),
        "SECONDARY_FOLDER_ID": raw.get("SECONDARY_FOLDER_ID", ""),
        "WEB_PROJECT_ID": raw.get("WEB_PROJECT_ID", ""),
        "ASSET_ID": raw.get("ASSET_ID", ""),
        "COPIED_ASSET_ID": raw.get("COPIED_ASSET_ID", ""),
        "TAG_ID": raw.get("TAG_ID", ""),
        "ROLE_ID": raw.get("ROLE_ID", ""),
        "TEAM_ID": raw.get("TEAM_ID", ""),
        "INVITATION_ID": raw.get("INVITATION_ID", ""),
        "SESSION_ID": raw.get("SEED_UPLOAD_SESSION_ID", raw.get("SESSION_ID", "")),
        "FILE_ID": raw.get("FILE_ID", ""),
        "DOCUMENT_ID": raw.get("DOCUMENT_ID", "doc-1"),
        "CONTRACT_ID": raw.get("CONTRACT_ID", ""),
        "BATCH_ID": raw.get("SEED_BYOF_BATCH_ID", raw.get("BATCH_ID", "")),
        "SERVICE_ACCOUNT_ID": raw.get("SERVICE_ACCOUNT_ID", ""),
        "SSO_MAPPING_ID": raw.get("SSO_MAPPING_ID", ""),
        "ATTACHMENT_ID": raw.get("ATTACHMENT_ID", ""),
        "ATTACHMENT_DRAFT_ID": raw.get("ATTACHMENT_DRAFT_ID", ""),
        "STYLE_COMMENT_ID": raw.get("STYLE_COMMENT_ID", ""),
        "SEED_IMPORTED_STYLE_ID": raw.get(
            "SEED_IMPORTED_STYLE_ID", raw.get("IMPORTED_STYLE_ID", "")
        ),
        "FONTPROJECT_CONTRIBUTOR_ACCESS_ID": raw.get(
            "SEED_FONTPROJECT_CONTRIBUTOR_ACCESS_ID", "34"
        ),
    }


def resolve_variables(operation: str, ctx: CurlContext, project_root: Path | None = None) -> dict:
    template = copy.deepcopy(merged_operation_variables_template().get(operation, {}))
    mapping = _placeholder_map(ctx, project_root)

    def walk(obj):
        if isinstance(obj, str) and _PLACEHOLDER.match(obj):
            key = obj[1:]
            return mapping.get(key, obj)
        if isinstance(obj, list):
            return [walk(x) for x in obj]
        if isinstance(obj, dict):
            return {k: walk(v) for k, v in obj.items()}
        return obj

    return walk(template)


def resolve_simulation_variables(
    label: str,
    ctx: CurlContext,
    project_root: Path,
) -> dict[str, Any]:
    """Variables for a Postman / flow simulation label (uses payload overrides)."""
    graphql_op = audit_operation(label)
    if label in SIMULATION_PAYLOAD_OVERRIDES:
        template = copy.deepcopy(SIMULATION_PAYLOAD_OVERRIDES[label])
    else:
        template = copy.deepcopy(merged_operation_variables_template().get(graphql_op, {}))

    mapping = _placeholder_map(ctx, project_root)

    def walk(obj: Any) -> Any:
        if isinstance(obj, str) and _PLACEHOLDER.match(obj):
            key = obj[1:]
            resolved = mapping.get(key, obj)
            if key == "FONTPROJECT_CONTRIBUTOR_ACCESS_ID" and str(resolved).isdigit():
                return int(resolved)
            return resolved
        if isinstance(obj, list):
            return [walk(x) for x in obj]
        if isinstance(obj, dict):
            return {k: walk(v) for k, v in obj.items()}
        return obj

    return walk(template)


def build_curl_resolved(operation: str, project_root: Path, ctx: CurlContext | None = None) -> str:
    """cURL with real endpoint, bearer token, and seed IDs from .env."""
    export = get_export_for_operation(operation)
    if not export:
        return ""
    query = get_document(project_root, export)
    if not query:
        return ""
    curl_ctx = ctx or load_curl_context(project_root)
    variables = resolve_variables(operation, curl_ctx, project_root)
    return _format_curl(curl_ctx, query, variables, graphql_op=operation)


def build_simulation_curl(
    label: str,
    project_root: Path,
    ctx: CurlContext | None = None,
    *,
    bearer_token: str | None = None,
) -> str:
    """cURL for a flow/Postman simulation label (correct variables + GraphQL document)."""
    graphql_op = audit_operation(label)
    export = get_export_for_operation(graphql_op)
    if not export:
        return ""
    query = get_document(project_root, export)
    if not query:
        return ""
    curl_ctx = ctx or load_curl_context(project_root)
    if bearer_token:
        curl_ctx = CurlContext(
            endpoint=curl_ctx.endpoint,
            nextgen_endpoint=curl_ctx.nextgen_endpoint,
            nextgen_origin=curl_ctx.nextgen_origin,
            nextgen_referer=curl_ctx.nextgen_referer,
            bearer_token=bearer_token,
            profile_id=curl_ctx.profile_id,
            customer_id=curl_ctx.customer_id,
            seed_family_id=curl_ctx.seed_family_id,
            seed_deactivate_family_id=curl_ctx.seed_deactivate_family_id,
            seed_style_id=curl_ctx.seed_style_id,
            seed_variation_md5=curl_ctx.seed_variation_md5,
        )
    variables = resolve_simulation_variables(label, curl_ctx, project_root)
    return _format_curl(curl_ctx, query, variables, graphql_op=graphql_op)


def _format_curl(ctx: CurlContext, query: str, variables: dict, *, graphql_op: str = "") -> str:
    if is_nextgen_ui_operation(graphql_op):
        export = get_export_for_operation(graphql_op) or graphql_op
        op_name = apollo_operation_name(export)
        payload = json.dumps(
            {
                "operationName": op_name,
                "variables": variables,
                "extensions": {"clientLibrary": {"name": "@apollo/client", "version": "4.0.9"}},
                "query": query,
            },
            separators=(",", ":"),
        )
        endpoint = ctx.nextgen_endpoint
        extra_headers = (
            f'-H "origin: {ctx.nextgen_origin}" '
            f'-H "referer: {ctx.nextgen_referer}" '
            '-H "accept: application/graphql-response+json,application/json;q=0.9" '
        )
    else:
        payload = json.dumps({"query": query, "variables": variables}, separators=(",", ":"))
        endpoint = ctx.endpoint
        extra_headers = ""
    payload_escaped = payload.replace("'", "'\\''")
    return (
        f'curl -sS -X POST "{endpoint}" '
        f'-H "Authorization: Bearer {ctx.bearer_token}" '
        '-H "Content-Type: application/json" '
        '-H "accept-language: en" '
        f"{extra_headers}"
        f"-d '{payload_escaped}'"
    )


def execute_operation_preview(
    operation: str,
    project_root: Path,
    ctx: CurlContext | None = None,
) -> tuple[str, str]:
    """Run GraphQL call and return (status, short result summary)."""
    export = get_export_for_operation(operation)
    if not export:
        return "SKIP", "unknown operation"
    query = get_document(project_root, export)
    if not query:
        return "SKIP", "missing graphql document"

    curl_ctx = ctx or load_curl_context(project_root)
    if not curl_ctx.bearer_token:
        return "SKIP", "BEARER_TOKEN not set in .env"

    variables = resolve_variables(operation, curl_ctx, project_root)
    return _execute_graphql(
        graphql_op=operation,
        query=query,
        variables=variables,
        curl_ctx=curl_ctx,
        project_root=project_root,
    )


def execute_simulation_preview(
    label: str,
    project_root: Path,
    ctx: CurlContext | None = None,
    *,
    bearer_token: str | None = None,
) -> tuple[str, str]:
    """Run a flow/Postman simulation label and return (status, summary)."""
    graphql_op = audit_operation(label)
    export = get_export_for_operation(graphql_op)
    if not export:
        return "SKIP", "unknown operation"
    query = get_document(project_root, export)
    if not query:
        return "SKIP", "missing graphql document"

    curl_ctx = ctx or load_curl_context(project_root)
    if bearer_token:
        curl_ctx = CurlContext(
            endpoint=curl_ctx.endpoint,
            nextgen_endpoint=curl_ctx.nextgen_endpoint,
            nextgen_origin=curl_ctx.nextgen_origin,
            nextgen_referer=curl_ctx.nextgen_referer,
            bearer_token=bearer_token,
            profile_id=curl_ctx.profile_id,
            customer_id=curl_ctx.customer_id,
            seed_family_id=curl_ctx.seed_family_id,
            seed_deactivate_family_id=curl_ctx.seed_deactivate_family_id,
            seed_style_id=curl_ctx.seed_style_id,
            seed_variation_md5=curl_ctx.seed_variation_md5,
        )
    if not curl_ctx.bearer_token:
        return "SKIP", "BEARER_TOKEN not set in .env"

    variables = resolve_simulation_variables(label, curl_ctx, project_root)
    return _execute_graphql(
        graphql_op=graphql_op,
        query=query,
        variables=variables,
        curl_ctx=curl_ctx,
        project_root=project_root,
    )


def _execute_graphql(
    *,
    graphql_op: str,
    query: str,
    variables: dict,
    curl_ctx: CurlContext,
    project_root: Path,
) -> tuple[str, str]:
    try:
        import requests

        raw = {k: v for k, v in dotenv_values(project_root / ".env").items() if v}
        use_ctx = raw.get("GRAPHQL_USE_CUSTOMER_CONTEXT", "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        headers = {
            "Authorization": f"Bearer {curl_ctx.bearer_token}",
            "Content-Type": "application/json",
            "accept-language": raw.get("ACCEPT_LANGUAGE", "en"),
        }
        if use_ctx and curl_ctx.customer_id:
            headers["x-context-customerid"] = curl_ctx.customer_id

        if is_nextgen_ui_operation(graphql_op):
            export = get_export_for_operation(graphql_op) or graphql_op
            headers.update(
                {
                    "accept": "application/graphql-response+json,application/json;q=0.9",
                    "origin": curl_ctx.nextgen_origin,
                    "referer": curl_ctx.nextgen_referer,
                    "user-agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
                    ),
                }
            )
            endpoint = curl_ctx.nextgen_endpoint
            payload = {
                "operationName": apollo_operation_name(export),
                "variables": variables,
                "extensions": {"clientLibrary": {"name": "@apollo/client", "version": "4.0.9"}},
                "query": query,
            }
        else:
            endpoint = curl_ctx.endpoint
            payload = {"query": query, "variables": variables}

        resp = requests.post(
            endpoint,
            headers=headers,
            json=payload,
            timeout=120,
        )
        try:
            body = resp.json()
        except Exception:
            return f"HTTP {resp.status_code}", (resp.text or "")[:500]

        if body.get("errors"):
            err = body["errors"][0].get("message", str(body["errors"]))[:300]
            return f"HTTP {resp.status_code}", f"ERROR: {err}"

        data = body.get("data") or {}
        field = data.get(graphql_op)
        if field is None and data:
            field = next(iter(data.values()), None)

        summary = json.dumps(field, separators=(",", ":"))[:500]
        return f"HTTP {resp.status_code}", summary or "OK (empty data)"
    except Exception as exc:
        return "FAIL", str(exc)[:300]
