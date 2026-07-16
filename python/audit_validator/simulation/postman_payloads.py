"""GraphQL variable payloads for simulation operations (Postman / preview)."""

from __future__ import annotations

# Per flow label overrides — mirrors python/audit_validator/simulation/flows.py
SIMULATION_PAYLOAD_OVERRIDES: dict[str, dict] = {
    "getFamiliesOfAllFontLists": {"input": {"pagination": {"skip": 0, "limit": 10}}},
    "getStylesOfAllFontLists": {"input": {"pagination": {"skip": 0, "limit": 10}}},
    "getProjectByDocumentId": {"documentId": "$DOCUMENT_ID"},
    "sharingInfoForAssets": {
        "assets": [{"id": "$ASSET_ID", "assetType": "Folder"}]
    },
    "getImportedFonts": {"input": {"pagination": {"skip": 0, "limit": 10}}},
    "getCategorizedGlyphs": {
        "input": {"styleId": "$SEED_STYLE_ID", "md5": "$SEED_VARIATION_MD5"}
    },
    "bulkActivateStyles": {
        "input": {
            "styles": [
                {"id": "$SEED_BULK_ACTIVATE_STYLE_1", "metadata": {"styleName": "Light"}},
                {"id": "$SEED_BULK_ACTIVATE_STYLE_2", "metadata": {"styleName": "Medium"}},
            ],
            "activationType": "PERMANENT",
        }
    },
    "bulkDeactivateStyles": {
        "input": {
            "styles": [
                {"id": "$SEED_BULK_ACTIVATE_STYLE_1", "metadata": {"styleName": "Light"}},
                {"id": "$SEED_BULK_ACTIVATE_STYLE_2", "metadata": {"styleName": "Medium"}},
            ],
            "deactivationType": "PERMANENT",
        }
    },
    "bulkAddStylesToFavourites": {
        "input": {
            "styles": [
                {"id": "$SEED_BULK_FAVOURITE_STYLE_1", "metadata": {"styleName": "Bold"}},
                {"id": "$SEED_BULK_FAVOURITE_STYLE_2", "metadata": {"styleName": "Medium Italic"}},
            ]
        }
    },
    "getSsoMappings": {"filter": {}, "pagination": {"skip": 0, "limit": 10}},
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
    "getProfiles": {"pagination": {"skip": 0, "limit": 10}},
    "getPrivateTags": {
        "input": {"filter": {}, "pageInfo": {"skip": 0, "limit": 10}}
    },
    "getFavorites": {"input": {"pagination": {"skip": 0, "limit": 10}}},
    "getAssets": {
        "input": {
            "parentId": "root",
            "pagination": {"skip": 0, "limit": 10},
            "filter": {"assetTypes": ["Folder", "FontList"]},
        }
    },
    "getCompanyAssets": {
        "input": {"pagination": {"skip": 0, "limit": 10}, "filter": {"assetTypes": ["Folder"]}}
    },
    "getAsset": {"input": {"assetId": "$ASSET_ID", "assetType": "Folder"}},
    "getAssetsFolderSummary": {"input": {"assetIds": ["$ASSET_ID"]}},
    "getAssetsSharings": {
        "input": {"assets": [{"assetType": "Folder", "assetIds": ["$ASSET_ID"]}]}
    },
    "getPinnedAssets": {"types": ["Folder"], "parentId": None},
    "getWebProjectSize": {
        "input": {"styles": [{"id": "$SEED_STYLE_ID", "isEmbedded": True}]}
    },
    "getCustomers": {
        "pagination": {"skip": 0, "limit": 10},
        "filter": {"search": "Everest_Test_Customer"},
    },
    "getCustomerById": {"getCustomerId": "$CUSTOMER_ID"},
    "getRoles": {
        "input": {"customerId": "$CUSTOMER_ID", "pagination": {"skip": 0, "limit": 10}}
    },
    "getRoles (for invitation)": {
        "input": {"customerId": "$CUSTOMER_ID", "pagination": {"skip": 0, "limit": 1}}
    },
    "getTeams": {"pagination": {"skip": 0, "limit": 10}},
    "getUserInvitations": {"customerId": "$CUSTOMER_ID", "page": 1, "pageSize": 10},
    "updatePrivateTagAssociations (disassociate)": {
        "input": {
            "customerId": "$CUSTOMER_ID",
            "tags": [{"id": "$TAG_ID", "disassociate": [{"styleId": "$SEED_STYLE_ID"}]}],
        }
    },
    "createAsset (FontList)": {
        "input": {
            "name": "automation-fontlist",
            "assetType": "FontList",
            "accessRight": "FullAccess",
        }
    },
    "createAsset (Folder)": {
        "input": {
            "name": "automation-folder-nested",
            "assetType": "Folder",
            "accessRight": "FullAccess",
            "parentId": "$ASSET_ID",
        }
    },
    "deleteAssets (Copied FontList)": {
        "input": {"assets": [{"assetType": "FontList", "assetIds": ["$COPIED_ASSET_ID"]}]}
    },
    "deleteAssets (Folder)": {
        "input": {"assets": [{"assetType": "Folder", "assetIds": ["$ASSET_ID"]}]}
    },
    "updateAssetSharing (GRANT)": {
        "input": {
            "assetId": "$ASSET_ID",
            "assetType": "Folder",
            "notify": True,
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
    "updateAssetSharing (REVOKE)": {
        "input": {
            "assetId": "$ASSET_ID",
            "assetType": "Folder",
            "notify": True,
            "data": [
                {
                    "action": "REVOKE",
                    "payload": [
                        {
                            "shareeType": "User",
                            "accessIdMap": [{"shareeId": "$SEED_SHARING_SHAREE_ID"}],
                        }
                    ],
                }
            ],
        }
    },
    "updateAssetSharing (GRANT project)": {
        "input": {
            "assetId": "$PROJECT_ID",
            "assetType": "FontProject",
            "notify": True,
            "data": [
                {
                    "action": "GRANT",
                    "payload": [
                        {
                            "shareeType": "User",
                            "accessIdMap": [
                                {
                                    "accessId": "$FONTPROJECT_CONTRIBUTOR_ACCESS_ID",
                                    "shareeId": "$SEED_SHARING_SHAREE_ID",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    },
    "updateAssetSharing (REVOKE project)": {
        "input": {
            "assetId": "$PROJECT_ID",
            "assetType": "FontProject",
            "notify": True,
            "data": [
                {
                    "action": "REVOKE",
                    "payload": [
                        {
                            "shareeType": "User",
                            "accessIdMap": [{"shareeId": "$SEED_SHARING_SHAREE_ID"}],
                        }
                    ],
                }
            ],
        }
    },
    "duplicateProject": {
        "input": {
            "items": [
                {
                    "source": {"assetId": "$PROJECT_ID", "assetType": "FontProject"},
                    "target": {"assetId": "root"},
                }
            ]
        }
    },
    "deleteProject (duplicate)": {"input": {"projectId": "$DUPLICATE_PROJECT_ID"}},
    "deleteProject (original)": {"input": {"projectId": "$PROJECT_ID"}},
    "linkDocumentToProject": {
        "input": {"projectId": "$PROJECT_ID", "documentId": "$DOCUMENT_ID"}
    },
    "unlinkDocumentFromProject": {
        "input": {"projectId": "$PROJECT_ID", "documentId": "$DOCUMENT_ID"}
    },
    "updateFontProjectStyles": {
        "input": {
            "fontProjectId": "$PROJECT_ID",
            "resolutions": [
                {
                    "styleId": "$SEED_STYLE_ID",
                    "resolvedMd5": "$SEED_VARIATION_MD5",
                    "resolvedVariationId": "$SEED_VARIATION_ID",
                    "unresolvedMd5s": [],
                }
            ],
        }
    },
    "updateProfile": {"input": {"id": "$OTHER_PROFILE_ID", "isActive": True}},
    "bulkUpdateProfiles": {
        "input": {
            "targetProfileIds": ["$OTHER_PROFILE_ID"],
            "action": "CHANGE_TEAMS",
            "operation": {"teamIds": []},
        }
    },
    "deleteProfiles": {"input": {"profileIds": []}},
    "getSyncedVariations": {
        "pagination": {"first": 10},
        "filter": {"isActive": True},
    },
    "markOnboardingCompleted": {"input": {"customerId": "$CUSTOMER_ID"}},
    "updateCustomerSettings": {
        "input": {"customerId": "$CUSTOMER_ID", "isAnalyticsEnabled": False}
    },
    "syncUnSyncVariations": {
        "input": {"styleId": "$SEED_STYLE_ID", "variationIds": ["$SEED_VARIATION_ID"]}
    },
    "markAsProductionFont": {
        "input": {
            "companyId": "$CUSTOMER_ID",
            "styleIds": ["$SEED_STYLE_ID"],
            "sourceContext": "LIBRARY",
        }
    },
    # notificationRecipient flow — secondary user acts on primary profile
    "createProject (secondary)": {
        "input": {
            "name": "automation-recipient-project",
            "description": "Automation notification recipient project",
            "allowFontAdditionsByCollaborators": True,
            "allowFontDownloadsByCollaborators": False,
            "allowFontImportsByCollaborators": False,
            "enableProjectLevelImportedFonts": False,
            "autoActivateFontsForMembers": False,
        }
    },
    "publishProject (secondary)": {"input": {"projectId": "$SECONDARY_PROJECT_ID"}},
    "updateProfile (secondary updates primary)": {
        "input": {"id": "$PROFILE_ID", "isActive": True}
    },
    "bulkUpdateProfiles (secondary updates primary)": {
        "input": {
            "targetProfileIds": ["$PROFILE_ID"],
            "action": "CHANGE_TEAMS",
            "operation": {"teamIds": []},
        }
    },
    "createAsset (secondary folder for primary)": {
        "input": {
            "name": "automation-recipient-folder",
            "assetType": "Folder",
            "accessRight": "FullAccess",
        }
    },
    "updateAssetSharing (GRANT primary on secondary folder)": {
        "input": {
            "assetId": "$SECONDARY_FOLDER_ID",
            "assetType": "Folder",
            "notify": True,
            "data": [
                {
                    "action": "GRANT",
                    "payload": [
                        {
                            "shareeType": "User",
                            "accessIdMap": [{"accessId": 27, "shareeId": "$PROFILE_ID"}],
                        }
                    ],
                }
            ],
        }
    },
    "updateAssetSharing (GRANT primary on secondary project)": {
        "input": {
            "assetId": "$SECONDARY_PROJECT_ID",
            "assetType": "FontProject",
            "notify": True,
            "data": [
                {
                    "action": "GRANT",
                    "payload": [
                        {
                            "shareeType": "User",
                            "accessIdMap": [
                                {
                                    "accessId": "$FONTPROJECT_CONTRIBUTOR_ACCESS_ID",
                                    "shareeId": "$PROFILE_ID",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    },
    "updateAssetSharing (REVOKE primary on secondary project)": {
        "input": {
            "assetId": "$SECONDARY_PROJECT_ID",
            "assetType": "FontProject",
            "notify": True,
            "data": [
                {
                    "action": "REVOKE",
                    "payload": [
                        {
                            "shareeType": "User",
                            "accessIdMap": [{"shareeId": "$PROFILE_ID"}],
                        }
                    ],
                }
            ],
        }
    },
    "deleteProject (secondary recipient)": {"input": {"projectId": "$SECONDARY_PROJECT_ID"}},
}
