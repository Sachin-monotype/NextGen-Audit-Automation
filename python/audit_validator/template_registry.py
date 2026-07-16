"""
Operation → validation template mapping.

Templates are grouped per ENRICHED_EVENTS_VALIDATION_GUIDE.md so paired ops
(activate/deactivate, add/remove) share one validator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DomainTemplate:
    id: str
    macro_family: str
    subject_type: str
    subject_snap_keys: frozenset[str]
    subject_extra_keys: frozenset[str] | None = None  # None = any extras allowed
    metadata_result_keys: frozenset[str] | None = None
    metadata_input_keys: frozenset[str] | None = None
    # Touchpoint-scoped keys present on raw metadata.input for some paths (not required)
    optional_metadata_input_keys: frozenset[str] | None = None
    requires_font_details: bool = False
    requires_asset_sharing: bool = False
    requires_batch_result: bool = False
    requires_actor_enrichment: bool = True
    requires_subject_enrichment: bool = True
    outcome: str = "success"  # success | failure


# fmt: off
TEMPLATES: dict[str, DomainTemplate] = {
    "fontActivation-family": DomainTemplate(
        id="fontActivation-family",
        macro_family="M10",
        subject_type="fontFamily",
        subject_snap_keys=frozenset({"fontDetails", "source"}),
        subject_extra_keys=frozenset({"counts", "enrichedSnapshot", "styles"}),
        metadata_result_keys=frozenset({"errors", "families"}),
        metadata_input_keys=frozenset({"familyIds"}),
        optional_metadata_input_keys=frozenset(
            {"listIds", "listType", "projectId", "activationType", "deactivationType"}
        ),
        requires_font_details=True,
    ),
    "fontActivation-style": DomainTemplate(
        id="fontActivation-style",
        macro_family="M11",
        subject_type="fontStyle",
        subject_snap_keys=frozenset({"fontDetails", "source"}),
        subject_extra_keys=frozenset({"counts", "enrichedSnapshot", "variations"}),
        metadata_result_keys=frozenset({"errors", "styles", "success"}),
        metadata_input_keys=frozenset({"styleIds"}),
        requires_font_details=True,
    ),
    "fontActivation-variation-activate": DomainTemplate(
        id="fontActivation-variation-activate",
        macro_family="M12",
        subject_type="fontVariation",
        subject_snap_keys=frozenset({"fontDetails", "source"}),
        subject_extra_keys=frozenset({"activationState", "enrichedSnapshot", "md5", "styleId"}),
        metadata_result_keys=frozenset({"errors", "styles", "success"}),
        metadata_input_keys=frozenset({"variations"}),
        requires_font_details=True,
    ),
    "fontActivation-variation-deactivate": DomainTemplate(
        id="fontActivation-variation-deactivate",
        macro_family="M12",
        subject_type="fontVariation",
        subject_snap_keys=frozenset({"fontDetails", "source"}),
        subject_extra_keys=frozenset({"activationState", "enrichedSnapshot", "md5", "styleId"}),
        metadata_result_keys=frozenset({"errors", "styles", "success"}),
        metadata_input_keys=frozenset({"md5s"}),
        requires_font_details=True,
    ),
    "bulk-fontActivation": DomainTemplate(
        id="bulk-fontActivation",
        macro_family="M15",
        subject_type="fontStyle",
        subject_snap_keys=frozenset({"batchId", "fontDetails", "source", "styleIds"}),
        subject_extra_keys=frozenset({"batchId", "enrichedSnapshot", "styleIds"}),
        requires_font_details=True,
        requires_batch_result=True,
    ),
    "favorites-family": DomainTemplate(
        id="favorites-family",
        macro_family="M13",
        subject_type="fontFamily",
        subject_snap_keys=frozenset({"familyIds", "fontDetails", "source"}),
        subject_extra_keys=frozenset({"enrichedSnapshot"}),
        metadata_result_keys=frozenset({"errors", "families", "success"}),
        metadata_input_keys=frozenset({"familyIds"}),
        requires_font_details=True,
    ),
    "favorites-pair": DomainTemplate(
        id="favorites-pair",
        macro_family="M14",
        subject_type="fontPair",
        subject_snap_keys=frozenset({"pairs", "source"}),
        subject_extra_keys=frozenset({"enrichedSnapshot"}),
        metadata_result_keys=frozenset({"errors", "pairs", "success"}),
        metadata_input_keys=frozenset({"pairs"}),
    ),
    "favorites-style": DomainTemplate(
        id="favorites-style",
        macro_family="M5",
        subject_type="fontStyle",
        subject_snap_keys=frozenset({"fontDetails", "source", "styleIds"}),
        subject_extra_keys=frozenset({"enrichedSnapshot"}),
        metadata_result_keys=frozenset({"errors", "styles", "success"}),
        metadata_input_keys=frozenset({"styleIds"}),
        requires_font_details=True,
    ),
    "favorites-bulk-add": DomainTemplate(
        id="favorites-bulk-add",
        macro_family="M5",
        subject_type="fontStyle",
        subject_snap_keys=frozenset({"fontDetails", "source", "styleIds"}),
        subject_extra_keys=frozenset({"batchId", "enrichedSnapshot"}),
        requires_font_details=True,
        requires_batch_result=True,
    ),
    "fontList-families": DomainTemplate(
        id="fontList-families",
        macro_family="M1",
        subject_type="fontList",
        subject_snap_keys=frozenset({"asset", "fontDetails", "sharingInfo", "source"}),
        subject_extra_keys=frozenset({"enrichedSnapshot", "familyIds", "styleIds"}),
        metadata_result_keys=frozenset({"errors", "families", "success"}),
        metadata_input_keys=frozenset({"families", "fontListId"}),
        requires_font_details=True,
        requires_asset_sharing=True,
    ),
    "fontList-styles": DomainTemplate(
        id="fontList-styles",
        macro_family="M1",
        subject_type="fontList",
        subject_snap_keys=frozenset({"asset", "fontDetails", "sharingInfo", "source"}),
        subject_extra_keys=frozenset({"enrichedSnapshot", "styleIds"}),
        metadata_result_keys=frozenset({"errors", "styles", "success"}),
        metadata_input_keys=frozenset({"fontListId", "styles"}),
        requires_font_details=True,
        requires_asset_sharing=True,
    ),
    "fontList-activate": DomainTemplate(
        id="fontList-activate",
        macro_family="M1",
        subject_type="fontList",
        subject_snap_keys=frozenset({"asset", "fontDetails", "sharingInfo", "source"}),
        subject_extra_keys=frozenset({"enrichedSnapshot", "listId"}),
        metadata_result_keys=frozenset({"errors", "listId", "success"}),
        metadata_input_keys=frozenset({"activationType", "listId", "listType"}),
        requires_font_details=True,
        requires_asset_sharing=True,
    ),
    "fontList-deactivate": DomainTemplate(
        id="fontList-deactivate",
        macro_family="M1",
        subject_type="fontList",
        subject_snap_keys=frozenset({"asset", "fontDetails", "sharingInfo", "source"}),
        subject_extra_keys=frozenset({"enrichedSnapshot", "listId"}),
        metadata_result_keys=frozenset({"errors", "listId", "success"}),
        metadata_input_keys=frozenset({"deactivationType", "listId", "listType"}),
        requires_font_details=True,
        requires_asset_sharing=True,
    ),
    "fontList-bulk": DomainTemplate(
        id="fontList-bulk",
        macro_family="M16",
        subject_type="fontStyle",
        subject_snap_keys=frozenset({"asset", "fontDetails", "sharingInfo", "source"}),
        subject_extra_keys=frozenset({"batchId", "enrichedSnapshot", "styleIds"}),
        requires_font_details=True,
        requires_asset_sharing=True,
        requires_batch_result=True,
    ),
    "fontProject-families": DomainTemplate(
        id="fontProject-families",
        macro_family="M2",
        subject_type="project",
        subject_snap_keys=frozenset({"asset", "fontDetails", "sharingInfo"}),
        subject_extra_keys=frozenset({"enrichedSnapshot", "familyIds", "projectId"}),
        metadata_result_keys=frozenset({"errors", "families", "success"}),
        metadata_input_keys=frozenset({"families", "fontProjectId"}),
        requires_font_details=True,
        requires_asset_sharing=True,
    ),
    "fontProject-styles": DomainTemplate(
        id="fontProject-styles",
        macro_family="M2",
        subject_type="project",
        subject_snap_keys=frozenset({"asset", "fontDetails", "sharingInfo"}),
        subject_extra_keys=frozenset({"enrichedSnapshot", "projectId", "styleIds"}),
        metadata_result_keys=frozenset({"errors", "styles", "success"}),
        metadata_input_keys=frozenset({"fontProjectId", "styles"}),
        requires_font_details=True,
        requires_asset_sharing=True,
    ),
    "fontProject-create": DomainTemplate(
        id="fontProject-create",
        macro_family="M25",
        subject_type="project",
        subject_snap_keys=frozenset({"asset", "fontDetails", "sharingInfo", "source"}),
        subject_extra_keys=frozenset({"enrichedSnapshot", "familyIds", "projectId", "styleIds"}),
        requires_font_details=False,
        requires_asset_sharing=True,
    ),
    "fontProject-publish": DomainTemplate(
        id="fontProject-publish",
        macro_family="M2",
        subject_type="project",
        subject_snap_keys=frozenset({"asset", "fontDetails", "sharingInfo"}),
        subject_extra_keys=frozenset({"enrichedSnapshot", "projectId"}),
    ),
    "fontProject-delete": DomainTemplate(
        id="fontProject-delete",
        macro_family="M27",
        subject_type="project",
        subject_snap_keys=frozenset({"projectId"}),
        subject_extra_keys=frozenset({"enrichedSnapshot", "projectId"}),
    ),
    "webProject-styles": DomainTemplate(
        id="webProject-styles",
        macro_family="M6",
        subject_type="webProject",
        subject_snap_keys=frozenset({"asset", "fontDetails", "sharingInfo", "source"}),
        subject_extra_keys=frozenset({"enrichedSnapshot", "styles"}),
        requires_font_details=True,
        requires_asset_sharing=True,
    ),
    "webProject-create": DomainTemplate(
        id="webProject-create",
        macro_family="M6",
        subject_type="webProject",
        subject_snap_keys=frozenset({"asset", "fontDetails", "sharingInfo", "source"}),
        subject_extra_keys=frozenset({"domains", "enrichedSnapshot", "styleIds", "styles"}),
        requires_font_details=True,
        requires_asset_sharing=True,
    ),
    "webProject-download": DomainTemplate(
        id="webProject-download",
        macro_family="M30",
        subject_type="webProject",
        subject_snap_keys=frozenset({"asset", "download", "sharingInfo", "source"}),
        subject_extra_keys=frozenset({"download", "enrichedSnapshot"}),
        requires_asset_sharing=True,
    ),
    "bulk-tag": DomainTemplate(
        id="bulk-tag",
        macro_family="M17",
        subject_type="fontStyle",
        subject_snap_keys=frozenset({"batchId", "fontDetails", "tag"}),
        subject_extra_keys=frozenset({"batchId", "enrichedSnapshot", "styleIds"}),
        requires_font_details=True,
        requires_batch_result=True,
    ),
    "asset-create": DomainTemplate(
        id="asset-create",
        macro_family="M4",
        subject_type="asset",
        subject_snap_keys=frozenset({"asset", "sharingInfo"}),
        subject_extra_keys=frozenset({"assetType", "enrichedSnapshot", "familyIds", "fontDetails", "styleIds"}),
        requires_asset_sharing=True,
    ),
    "asset-update": DomainTemplate(
        id="asset-update",
        macro_family="M4",
        subject_type="asset",
        subject_snap_keys=frozenset({"asset", "sharingInfo"}),
        subject_extra_keys=frozenset({"assetType", "enrichedSnapshot", "fontDetails"}),
        requires_asset_sharing=True,
    ),
    "asset-sharing": DomainTemplate(
        id="asset-sharing",
        macro_family="M4",
        subject_type="asset",
        subject_snap_keys=frozenset({"asset", "sharingInfo"}),
        subject_extra_keys=frozenset({"assetType", "enrichedSnapshot", "fontDetails"}),
        requires_asset_sharing=True,
    ),
    "asset-bulk-copy": DomainTemplate(
        id="asset-bulk-copy",
        macro_family="M7",
        subject_type="asset",
        subject_snap_keys=frozenset({"asset", "sharingInfo"}),
        subject_extra_keys=frozenset({"assetType", "enrichedSnapshot"}),
        requires_asset_sharing=True,
    ),
    "asset-bulk-move": DomainTemplate(
        id="asset-bulk-move",
        macro_family="M4",
        subject_type="asset",
        subject_snap_keys=frozenset({"asset", "sharingInfo"}),
        subject_extra_keys=frozenset({"enrichedSnapshot", "fontDetails"}),
        requires_asset_sharing=True,
    ),
    "asset-bulk-update": DomainTemplate(
        id="asset-bulk-update",
        macro_family="M7",
        subject_type="asset",
        subject_snap_keys=frozenset({"asset", "sharingInfo"}),
        subject_extra_keys=frozenset({"assetType", "enrichedSnapshot"}),
        requires_asset_sharing=True,
    ),
    "asset-pin": DomainTemplate(
        id="asset-pin",
        macro_family="M7",
        subject_type="asset",
        subject_snap_keys=frozenset({"asset", "sharingInfo"}),
        subject_extra_keys=frozenset({"enrichedSnapshot"}),
        requires_asset_sharing=True,
    ),
    "asset-delete": DomainTemplate(
        id="asset-delete",
        macro_family="M26",
        subject_type="asset",
        subject_snap_keys=frozenset({"asset"}),
        subject_extra_keys=frozenset({"assetType", "enrichedSnapshot"}),
    ),
    "privateTag": DomainTemplate(
        id="privateTag",
        macro_family="M3",
        subject_type="privateTag",
        subject_snap_keys=frozenset({"tags"}),
        subject_extra_keys=frozenset({"enrichedSnapshot"}),
    ),
    "role-mutate": DomainTemplate(
        id="role-mutate",
        macro_family="M18",
        subject_type="role",
        subject_snap_keys=frozenset({"role"}),
        subject_extra_keys=frozenset({"enrichedSnapshot"}),
    ),
    "role-delete": DomainTemplate(
        id="role-delete",
        macro_family="M28",
        subject_type="role",
        subject_snap_keys=frozenset({"roles"}),
        subject_extra_keys=frozenset({"enrichedSnapshot"}),
    ),
    "team-mutate": DomainTemplate(
        id="team-mutate",
        macro_family="M19",
        subject_type="team",
        subject_snap_keys=frozenset({"team"}),
        subject_extra_keys=frozenset({"enrichedSnapshot"}),
    ),
    "team-delete": DomainTemplate(
        id="team-delete",
        macro_family="M29",
        subject_type="team",
        subject_snap_keys=frozenset({"teams"}),
        subject_extra_keys=frozenset({"enrichedSnapshot"}),
    ),
    "user-profile": DomainTemplate(
        id="user-profile",
        macro_family="M8",
        subject_type="user",
        subject_snap_keys=frozenset({"user"}),
        subject_extra_keys=frozenset({"enrichedSnapshot", "isActive"}),
    ),
    "user-bulk": DomainTemplate(
        id="user-bulk",
        macro_family="M8",
        subject_type="user",
        subject_snap_keys=frozenset({"user"}),
        subject_extra_keys=frozenset({"enrichedSnapshot"}),
    ),
    "user-reset": DomainTemplate(
        id="user-reset",
        macro_family="M8",
        subject_type="user",
        subject_snap_keys=frozenset({"user"}),
        subject_extra_keys=frozenset({"enrichedSnapshot"}),
    ),
    "invitation-create": DomainTemplate(
        id="invitation-create",
        macro_family="M20",
        subject_type="invitation",
        subject_snap_keys=frozenset({"invitations"}),
        subject_extra_keys=frozenset({"email", "enrichedSnapshot"}),
    ),
    "invitation-update": DomainTemplate(
        id="invitation-update",
        macro_family="M20",
        subject_type="invitation",
        subject_snap_keys=frozenset({"invitations"}),
        subject_extra_keys=frozenset({"enrichedSnapshot"}),
    ),
    "customer-update": DomainTemplate(
        id="customer-update",
        macro_family="M33",
        subject_type="customer",
        subject_snap_keys=frozenset({"customer"}),
        subject_extra_keys=frozenset({"enrichedSnapshot"}),
    ),
    "uploadSession": DomainTemplate(
        id="uploadSession",
        macro_family="M9",
        subject_type="uploadSession",
        subject_snap_keys=frozenset({"sessions", "source"}),
        subject_extra_keys=frozenset({"enrichedSnapshot", "sessionId"}),
    ),
    "productionFont": DomainTemplate(
        id="productionFont",
        macro_family="M21",
        subject_type="productionFont",
        subject_snap_keys=frozenset({"fontDetails", "source"}),
        subject_extra_keys=frozenset({"enrichedSnapshot"}),
        requires_font_details=True,
    ),
    "getPackageId": DomainTemplate(
        id="getPackageId",
        macro_family="M31",
        subject_type="fontDownloadPackage",
        subject_snap_keys=frozenset({"fontDetails", "packageId", "source"}),
        subject_extra_keys=frozenset({"enrichedSnapshot", "listId", "listType", "packageId", "styles", "variations"}),
        requires_font_details=True,
    ),
    "failureMinimal": DomainTemplate(
        id="failureMinimal",
        macro_family="M22-M32",
        subject_type="*",  # any
        subject_snap_keys=frozenset(),
        subject_extra_keys=None,
        requires_actor_enrichment=False,
        requires_subject_enrichment=False,
        outcome="failure",
    ),
    "cron-scheduler": DomainTemplate(
        id="cron-scheduler",
        macro_family="Scheduled",
        subject_type="*",
        subject_snap_keys=frozenset(),
        subject_extra_keys=None,
        requires_actor_enrichment=False,
        requires_subject_enrichment=False,
        outcome="success",
    ),
    "cron-no-enricher": DomainTemplate(
        id="cron-no-enricher",
        macro_family="Scheduled",
        subject_type="*",
        subject_snap_keys=frozenset(),
        subject_extra_keys=None,
        requires_actor_enrichment=False,
        requires_subject_enrichment=False,
        outcome="success",
    ),
    "byof-licence-expiry": DomainTemplate(
        id="byof-licence-expiry",
        macro_family="BYOF",
        subject_type="licence",
        subject_snap_keys=frozenset({"source", "contract", "fontDetails"}),
        subject_extra_keys=None,
        requires_font_details=True,
        requires_actor_enrichment=False,
        requires_subject_enrichment=True,
    ),
    "fontAccess-approve": DomainTemplate(
        id="fontAccess-approve",
        macro_family="M33",
        subject_type="fontAccess",
        subject_snap_keys=frozenset({"fontDetails", "source"}),
        subject_extra_keys=frozenset({"enrichedSnapshot"}),
        metadata_result_keys=frozenset({"errors", "results", "success", "successCount", "failureCount"}),
        requires_font_details=True,
    ),
    "styleComment-add": DomainTemplate(
        id="styleComment-add",
        macro_family="M34",
        subject_type="styleComment",
        subject_snap_keys=frozenset(),
        subject_extra_keys=frozenset({"enrichedSnapshot"}),
        metadata_result_keys=frozenset({"errors", "success"}),
        metadata_input_keys=frozenset({"comment", "styleId"}),
        requires_subject_enrichment=False,
    ),
    "styleDocument-actor": DomainTemplate(
        id="styleDocument-actor",
        macro_family="M35",
        subject_type="styleDocument",
        subject_snap_keys=frozenset(),
        subject_extra_keys=frozenset({"enrichedSnapshot"}),
        requires_subject_enrichment=False,
    ),
    "notification-actor": DomainTemplate(
        id="notification-actor",
        macro_family="M36",
        subject_type="notification",
        subject_snap_keys=frozenset(),
        subject_extra_keys=frozenset({"enrichedSnapshot"}),
        requires_subject_enrichment=False,
    ),
    "assetAttachment-actor": DomainTemplate(
        id="assetAttachment-actor",
        macro_family="M37",
        subject_type="assetAttachment",
        subject_snap_keys=frozenset(),
        subject_extra_keys=frozenset({"enrichedSnapshot"}),
        requires_subject_enrichment=False,
    ),
    "companyLogo-actor": DomainTemplate(
        id="companyLogo-actor",
        macro_family="M38",
        subject_type="companyLogo",
        subject_snap_keys=frozenset(),
        subject_extra_keys=frozenset({"enrichedSnapshot"}),
        requires_subject_enrichment=False,
    ),
    "fontTemplate-actor": DomainTemplate(
        id="fontTemplate-actor",
        macro_family="M39",
        subject_type="fontTemplate",
        subject_snap_keys=frozenset(),
        subject_extra_keys=frozenset({"enrichedSnapshot"}),
        requires_subject_enrichment=False,
    ),
    "byof-batch": DomainTemplate(
        id="byof-batch",
        macro_family="BYOF",
        subject_type="byofBatch",
        subject_snap_keys=frozenset({"batchId", "source"}),
        subject_extra_keys=frozenset({"enrichedSnapshot", "isImportedFont"}),
        requires_font_details=False,
    ),
    "byof-import-delete": DomainTemplate(
        id="byof-import-delete",
        macro_family="BYOF",
        subject_type="byofBatch",
        subject_snap_keys=frozenset({"fontDetails", "isImportedFont", "source"}),
        subject_extra_keys=frozenset({"enrichedSnapshot"}),
        requires_font_details=False,
    ),
    "user-delete-profiles": DomainTemplate(
        id="user-delete-profiles",
        macro_family="M8",
        subject_type="user",
        # Resolver now enriches deletedProfiles[{profileId,idpUserId,user{…}}]
        # via UMS getUserByIdpUserId (mt-audit-log-resolver PR #50 / mtconnect-api #1005).
        subject_snap_keys=frozenset({"deletedProfiles", "source"}),
        subject_extra_keys=frozenset({"enrichedSnapshot"}),
        requires_subject_enrichment=True,
    ),
    "byof-contract-delete": DomainTemplate(
        id="byof-contract-delete",
        macro_family="BYOF",
        subject_type="licence",
        subject_snap_keys=frozenset({"contract", "source"}),
        subject_extra_keys=frozenset({"enrichedSnapshot"}),
        requires_font_details=False,
    ),
    "serviceAccount-actor": DomainTemplate(
        id="serviceAccount-actor",
        macro_family="M40",
        subject_type="serviceAccount",
        subject_snap_keys=frozenset(),
        subject_extra_keys=frozenset({"enrichedSnapshot"}),
        requires_subject_enrichment=False,
    ),
    "font-sync-variation": DomainTemplate(
        id="font-sync-variation",
        macro_family="M12",
        subject_type="fontVariation",
        subject_snap_keys=frozenset({"fontDetails", "source"}),
        subject_extra_keys=frozenset({"enrichedSnapshot"}),
        requires_font_details=True,
    ),
    "font-addon-publish": DomainTemplate(
        id="font-addon-publish",
        macro_family="M41",
        subject_type="font",
        subject_snap_keys=frozenset({"fontDetails", "source"}),
        subject_extra_keys=frozenset({"enrichedSnapshot"}),
        requires_font_details=True,
    ),
}
# fmt: on

OPERATION_TEMPLATE_MAP: dict[str, str] = {
    # Font activation pairs
    "activateFamily": "fontActivation-family",
    "deactivateFamilies": "fontActivation-family",
    "activateStyle": "fontActivation-style",
    "deactivateStyle": "fontActivation-style",
    "activateVariation": "fontActivation-variation-activate",
    "deactivateVariation": "fontActivation-variation-deactivate",
    "bulkActivateStyles": "bulk-fontActivation",
    "bulkDeactivateStyles": "bulk-fontActivation",
    "syncUnSyncVariations": "font-sync-variation",
    "getSyncedVariations": "font-sync-variation",
    # Favorites pairs
    "addFavoriteFamilies": "favorites-family",
    "removeFavoriteFamilies": "favorites-family",
    "addFavoritePair": "favorites-pair",
    "removeFavoritePair": "favorites-pair",
    "addFavoriteStyles": "favorites-style",
    "removeFavoriteStyles": "favorites-style",
    "bulkAddStylesToFavourites": "favorites-bulk-add",
    "bulkRemoveStylesFromFavourites": "failureMinimal",
    # Font list
    "addFontListFamilies": "fontList-families",
    "removeFontListFamilies": "fontList-families",
    "addFontListStyles": "fontList-styles",
    "removeFontListStyles": "fontList-styles",
    "activateList": "fontList-activate",
    "deActivateList": "fontList-deactivate",
    "bulkActivateLists": "fontList-activate",
    "bulkDeactivateLists": "fontList-deactivate",
    "bulkAddStylesToList": "fontList-bulk",
    "bulkRemoveStylesFromList": "fontList-bulk",
    # Font project
    "addFontProjectFamilies": "fontProject-families",
    "removeFontProjectFamilies": "fontProject-families",
    "addFontProjectStyles": "fontProject-styles",
    "removeFontProjectStyles": "fontProject-styles",
    "createProject": "fontProject-create",
    "duplicateProject": "fontProject-create",
    "publishProject": "fontProject-publish",
    "deleteProject": "fontProject-delete",
    "linkDocumentToProject": "fontProject-publish",
    "unlinkDocumentFromProject": "fontProject-publish",
    # Web project
    "addStylesToWebProject": "webProject-styles",
    "removeStylesFromWebProject": "webProject-styles",
    "createWebProject": "webProject-create",
    "editWebProject": "webProject-create",
    "downloadWebProject": "webProject-download",
    # Bulk tag
    "bulkTagStyles": "bulk-tag",
    "bulkUntagStyles": "bulk-tag",
    # Assets
    "createAsset": "asset-create",
    "updateAsset": "asset-update",
    "updateAssetSharing": "asset-sharing",
    "bulkCopyAssets": "asset-bulk-copy",
    "bulkMoveAssets": "asset-bulk-move",
    "updateAssets": "asset-bulk-update",
    "pinAsset": "asset-pin",
    "unpinAsset": "failureMinimal",
    "deleteAssets": "asset-delete",
    # Tags
    "createPrivateTags": "privateTag",
    "updatePrivateTag": "privateTag",
    "updatePrivateTagAssociations": "privateTag",
    "deletePrivateTags": "privateTag",
    "deleteAllPrivateTags": "privateTag",
    # Roles / teams / users
    "createRole": "role-mutate",
    "updateRole": "role-mutate",
    "deleteRoles": "role-delete",
    "createTeam": "team-mutate",
    "updateTeam": "team-mutate",
    "deleteTeams": "team-delete",
    "updateProfile": "user-profile",
    "bulkUpdateProfiles": "user-bulk",
    "resetPassword": "user-reset",
    # Invitations
    "createUserInvitations": "invitation-create",
    "updateUserInvitations": "invitation-update",
    # Customer
    "createCustomer": "failureMinimal",
    "updateCustomer": "customer-update",
    # Upload session
    "createUploadSession": "uploadSession",
    "markSessionFileUploaded": "uploadSession",
    "updateSessionFiles": "uploadSession",
    "completeUploadSession": "failureMinimal",
    # Production font / query
    "markProductionFonts": "productionFont",
    "updateProductionFont": "productionFont",
    "getPackageId": "getPackageId",
    "approveFontAccess": "fontAccess-approve",
    "addStyleComment": "styleComment-add",
    "addStyleDocument": "styleDocument-actor",
    "deleteStyleDocument": "styleDocument-actor",
    "createStyleDocumentsUploadUrl": "styleDocument-actor",
    "bulkNotificationAction": "notification-actor",
    "bulkUpdatePreferences": "notification-actor",
    "bulkMarkAsProductionFont": "productionFont",
    "createAssetAttachmentUpload": "assetAttachment-actor",
    "deleteAssetAttachment": "assetAttachment-actor",
    "finalizeAssetAttachments": "assetAttachment-actor",
    "deleteStyleComment": "styleComment-add",
    "dismissNotification": "notification-actor",
    "createCompanyLogoUploadUrl": "companyLogo-actor",
    "deleteCompanyLogo": "companyLogo-actor",
    "exportFontTemplate": "fontTemplate-actor",
    "createBYOFBatchAndCheckDuplicates": "byof-batch",
    "deleteImportedFonts": "byof-import-delete",
    "deleteProfiles": "user-delete-profiles",
    "deleteContracts": "byof-contract-delete",
    "createContract": "byof-contract-delete",
    "updateContract": "byof-contract-delete",
    "linkContractToStyles": "byof-licence-expiry",
    "unlinkStyleFromContract": "byof-licence-expiry",
    "publishAddOn": "font-addon-publish",
    "createServiceAccount": "serviceAccount-actor",
    "deleteServiceAccount": "serviceAccount-actor",
    "suspendServiceAccount": "serviceAccount-actor",
    # Cron / scheduler (passthrough + BYOF)
    "weekly_account_expiry": "cron-scheduler",
    "weekly_account_expiry_digest": "cron-scheduler",
    "font_leaving_catalogue": "cron-scheduler",
    "tokenExpiring": "cron-scheduler",
    "tokenExpiringSuspended": "cron-scheduler",
    "projectArchivalWarningAdmin": "cron-scheduler",
    "projectArchivalWarningMember": "cron-scheduler",
    "notifyByofLicenceExpiry": "byof-licence-expiry",
    "quarterlyReportNotification": "cron-no-enricher",
    "subscriptionExpiryNotification": "cron-no-enricher",
}


# ---------------------------------------------------------------------------
# Auto-generated coverage from the resolver enricher inventory.
#
# Source of truth: mt-audit-log-resolver-service enrichers. Every operation the
# resolver enriches gets a *lenient* validation template so we validate the
# structural contract (subject.type + which enrichedSnapshot(s) must exist)
# without brittle field-level FAILs. Three enrichment kinds:
#   * subject+actor  -> subject.enrichedSnapshot AND actor.enrichedSnapshot
#   * actor-only     -> only actor.enrichedSnapshot required
#   * passthrough    -> resolver has no enricher; event passes through unchanged
# Curated entries in TEMPLATES / OPERATION_TEMPLATE_MAP above always win.
# ---------------------------------------------------------------------------

# operation -> subject.type, for resolver subject+actor enrichers
_SUBJECT_ACTOR_OPS: dict[str, str] = {
    "activateFontProject": "project",
    "deActivateFontProject": "project",
    "updateFontProjectStyles": "project",
    "updateAssetsSharingInfo": "asset",
    "updateCustomerSettings": "customer",
    "linkDocumentsToContract": "contract",
    "unlinkDocumentsFromContract": "contract",
    "parseAndCreateContract": "contract",
    "requestFontAccess": "fontAccess",
    "rejectFontAccess": "fontAccess",
}

# operation -> subject.type, for resolver actor-only enrichers
_ACTOR_ONLY_OPS: dict[str, str] = {
    "applyUnsubscribe": "notificationPreference",
    "globalEmailOptOut": "notificationPreference",
    "resetPreferences": "notificationPreference",
    "updatePreference": "notificationPreference",
    "updateSubToggle": "notificationPreference",
    "bulkAddPairsToFavorite": "fontPair",
    "bulkRemovePairsFromFavorite": "fontPair",
    "bulkUnmarkProductionFont": "productionFont",
    "keepInProduction": "productionFont",
    "cancelBatch": "byofBatch",
    "createSsoMapping": "ssoMapping",
    "deleteSsoMappings": "ssoMapping",
    "reorderSsoMappings": "ssoMapping",
    "updateCompanySsoStatus": "ssoMapping",
    "updateSsoMapping": "ssoMapping",
    "getSsoMappings": "ssoMapping",
    "downloadAssetAttachmentSignedUrl": "assetAttachment",
    "getAssetAttachments": "assetAttachment",
    "exportUnassignedImportedFontsTemplate": "fontTemplate",
    "importFontTemplate": "fontTemplate",
    "getActiveBatches": "batch",
    "getBatchProgress": "batch",
    "getAllAccessRequests": "fontAccess",
    "getCategorizedGlyphs": "glyph",
    "getCustomerSettings": "customer",
    "markOnboardingCompleted": "customer",
    "getDocumentMetadata": "documentMetadata",
    "saveDocumentMetadata": "document",
    "getEligiblePrimaryContacts": "user",
    "setLanguagePreference": "user",
    "getFamiliesOfAllFontLists": "asset",
    "getStylesOfAllFontLists": "asset",
    "sharingInfoForAssets": "asset",
    "getImportedFonts": "fontImport",
    "getProjectByDocumentId": "project",
    "getStyleComments": "styleComment",
    "updateStyleComment": "styleComment",
    "getStyleDocuments": "styleDocument",
    "getStyleDocumentsDownloadUrls": "styleDocument",
    "getUploadSessionFonts": "uploadSession",
    "markAllNotificationsRead": "notification",
    "markNotificationRead": "notification",
    "markCompanyLogoUploadSuccess": "companyLogo",
    "regenerateToken": "serviceAccount",
    "updateServiceAccount": "serviceAccount",
    "submitFontUsageReport": "fontUsageReport",
    "submitIntentForProduction": "fontUsageReport",
    "updateFontsForReview": "fontUsageReport",
}

# Read/query operations the resolver does NOT enrich (event passes through).
_PASSTHROUGH_OPS: frozenset[str] = frozenset({
    "getAsset", "getAssets", "getAssetsFolderSummary", "getAssetsSharings",
    "getCompanyAssets", "getCustomerById", "getCustomers", "getFavorites",
    "getPinnedAssets", "getPrivateTags", "getProfiles", "getRoles", "getTeams",
    "getUserInvitations", "getWebProjectSize", "markAsProductionFont",
    "getAddOnFonts", "getAddOnVariations", "getAllFoundriesForContracts",
    "getAllPermissions", "getAllVendorsForContracts", "getAvailableContracts",
    "getCustomer", "getEntitlement", "getFamilies", "getFavoritePairs",
    "getIndividualStyleCountForFontLists", "getInventories",
    "getMultipartUploadInfoForFile", "getPackageStatus", "getPairStatus",
    "getProfile", "getStyles", "getUploadSession", "getVariations",
})


def _register_generated_templates() -> None:
    def _add(template_id: str, subject_type: str, *, subject: bool, actor: bool) -> None:
        TEMPLATES.setdefault(
            template_id,
            DomainTemplate(
                id=template_id,
                macro_family="AUTO",
                subject_type=subject_type,
                subject_snap_keys=frozenset(),
                subject_extra_keys=None,
                requires_subject_enrichment=subject,
                requires_actor_enrichment=actor,
            ),
        )

    for op, st in _SUBJECT_ACTOR_OPS.items():
        tid = f"gen-subject-actor:{st}"
        _add(tid, st, subject=True, actor=True)
        OPERATION_TEMPLATE_MAP.setdefault(op, tid)

    for op, st in _ACTOR_ONLY_OPS.items():
        tid = f"gen-actor-only:{st}"
        _add(tid, st, subject=False, actor=True)
        OPERATION_TEMPLATE_MAP.setdefault(op, tid)

    _add("gen-passthrough", "*", subject=False, actor=False)
    for op in _PASSTHROUGH_OPS:
        OPERATION_TEMPLATE_MAP.setdefault(op, "gen-passthrough")


_register_generated_templates()


def get_template(operation: str) -> DomainTemplate | None:
    template_id = OPERATION_TEMPLATE_MAP.get(operation)
    if not template_id:
        return None
    return TEMPLATES[template_id]


def parse_filename(filename: str) -> tuple[str, str] | None:
    """Parse `{operation}-{service}.json` or raw `{operation}-{service}-{cid8}.json`."""
    if not filename.endswith(".json"):
        return None
    stem = filename[: -len(".json")]
    if stem.endswith("-mtconnect-api"):
        operation = stem[: -len("-mtconnect-api")]
        return operation, "mtconnect-api"
    corr = re.match(r"^(.+)-mtconnect-api-[0-9a-f]{8}$", stem)
    if corr:
        return corr.group(1), "mtconnect-api"
    if "-" not in stem:
        return None
    operation, service = stem.rsplit("-", 1)
    return operation, service
