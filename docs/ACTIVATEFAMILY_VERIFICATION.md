# activateFamily — field verification guide

This document mirrors the **Results** UI table for `activateFamily`: each enriched
node is listed with its source system, the live fetch (API **or** MySQL when
`SOURCE_TRUTH=db`), and how values are transformed before compare.

Sample used below (our staged enrich):

| Key | Value |
|---|---|
| Family id | `794971` |
| Style id | `794973` |
| Actor profile | `bc195ef6-6884-11f1-a522-0e0a04e472ab` |
| Actor gcid | `4a949153-9cab-4023-b31c-8336a8a3ec46` |
| Role id | `19aa9d1a-50da-4d98-8562-f0758267b51a` |
| Enrich file | `payload/enrich/activateFamily.json` |

---

## 1. What Compare does (pipeline)

```
Mongo / payload enrich JSON
        │
        ├─ scan field paths (Results rows)
        │
        ├─ fetch LIVE source values
        │     • Typesense/Discovery  (batched already)
        │     • CMS customer         (per unique gcid)
        │     • UMS profile + role   (per unique profile/role)
        │     • AMS asset            (only if subject is an asset — N/A for activateFamily)
        │     • Bearer / Raw         (no remote call)
        │
        ├─ transform / align shapes  (see §3)
        │
        └─ PASS | FAIL | SKIP | N/A   → Results UI table
```

UI columns map as:

| UI | Meaning |
|---|---|
| Node / Sub-node | Logical group (`customer`, `user/profile`, `fontDetails[0]/styles[0]/catalog`, …) |
| Field | Leaf name |
| JSON path | `$.…` under enriched JSON |
| Source (X) · resource | CMS/UMS/Typesense/… + resource hint (`customers`, `profiles`, `styles`) |
| Match | Compare after transforms |

---

## 2. Live fetches that cover activateFamily

### 2.1 CMS — one customer → ~43 UI fields

**API (resolver parity)**

```http
GET {CMS_BASE_URL}/api/v2/customers/4a949153-9cab-4023-b31c-8336a8a3ec46
  ?projection=id,name,displayName,source,parentId,identityProviderId,externalId,metaData,isPreDeliveryEnabled,isTestDemo,subscription,createdAt
  &subscriptionFields=planDefinition,productType,seatsAvailable,terminationDate,isTrial,isActive,createdAt
  &application=MTConnect
Headers:
  accept: application/json
  x-client-id: mt-events-resolver-service
  x-correlation-id: <uuid>
```

**MySQL (`SOURCE_TRUTH=db`)**

```sql
SELECT id, name, display_name, source, metaData,
       is_predelivery_enabled, is_test_demo, created_on, modified_on
FROM customer_management.customers
WHERE id = '4a949153-9cab-4023-b31c-8336a8a3ec46';
```

Verified live: `display_name = Everest Admin` ↔ enrich `actor.enrichedSnapshot.customer.displayName`.

Subscription / `planDefinition` leaves come from the CMS API `subscription` object (HTTP).
On DB-only mode, subscription fields may SKIP until `customer_subscription` is joined.

### 2.2 UMS — one profile + one role → ~12 UI fields

**API**

```http
POST {UMS_BASE_URL}/api/v3/customers/{gcid}/profiles
Headers:
  content-type: application/json
  x-client-id: mt-events-resolver-service
  x-correlation-id: <uuid>
  X-HTTP-Method-Override: GET
Body:
{
  "projection": "isActive,firstName,lastName,email,meta,userId,externalUserId,customerId,tempUserExpiryDate,idpUserId,activity.lastActivityTimestamp,role.id,team.id,team.teamAdminIds,createdAt,profile.metaData",
  "filter": { "#and": { "isActive": { "eq": true }, "profile.id": { "in": ["bc195ef6-6884-11f1-a522-0e0a04e472ab"] } } },
  "limit": 1,
  "offset": 0
}
```

```http
GET {UMS_BASE_URL}/api/v3/customers/{gcid}/roles
  ?projection=id,displayName,typeId,permissions,description,profileCount
  &filter=19aa9d1a-50da-4d98-8562-f0758267b51a
  &filterType=id
```

**MySQL**

```sql
-- Prefer the view (varchar UUIDs). Raw profiles.id is binary(16).
SELECT profile_Id_uuid, customer_id_uuid, first_name, last_name, email,
       is_active, idp_user_id, role_id_uuid, role_name, role_description
FROM user_management.vw_profile_details
WHERE profile_Id_uuid = 'bc195ef6-6884-11f1-a522-0e0a04e472ab'
  AND customer_id_uuid = '4a949153-9cab-4023-b31c-8336a8a3ec46'
  AND (is_deleted = 0 OR is_deleted IS NULL);

SELECT LOWER(BIN_TO_UUID(id)) AS id, display_name, type_id, description
FROM user_management.roles
WHERE BIN_TO_UUID(id) = '19aa9d1a-50da-4d98-8562-f0758267b51a';
```

Verified live: email / Company Admin match enrich `user.profile.*` and `user.role.displayName`.

### 2.3 Typesense / Discovery — family + styles + variations → ~47 UI fields

**Styles (family)**

```http
POST {DISCOVERY_BASE_URL}/v1/styles?skipInventoryCheck=true
Authorization: Bearer <discovery token>
Content-Type: application/json
x-correlation-id: <uuid>

{ "familyIds": ["794971"], "page": 1, "per_page": 250 }
```

Verified live: style `794973`, `font_name = 1066 Hastings Normal`.

**Variations**

```http
GET {DISCOVERY_BASE_URL}/v1/variations
  ?stylesIds=794973
  &includeStyle=true
  &skipInventoryCheck=true
  &page=1&perPage=250
Authorization: Bearer <discovery token>
```

Verified live: md5s `1e457877…`, `dd64034b…` match enrich `styles[0].catalog.md5[]` /
`variations[].catalog.md5`.

### 2.4 Bearer / Raw / Resolver — no upstream HTTP

| Source | How we verify |
|---|---|
| Bearer | Decode JWT / identity used to fire the mutation (`gcid`, profile, org) |
| Raw | Compare enrich envelope leaves to paired raw Mongo doc (`eventId`, `source.*`, …) |
| Resolver constants | Fixed strings like `user-management-service`, `mt-connect-middleware-discovery` |

`activateFamily` has **no AMS** subject asset — AMS queries are N/A for this op.

---

## 3. Transformations before match

Code: `value_match.values_equivalent` + `discovery_resolver.normalize_compare` +
`comparison_rows._ams_value` / `_cms_value` / `_ums_value`.

| Situation | Transform |
|---|---|
| Case / trailing `" test"` on names | Casefold + strip trailing `test` |
| Typesense array vs enrich scalar (`font_nids[0]`) | Take index `0` (or sole element) from source list |
| Nested object vs leaf (`visual_properties.contrast`) | Dig leaf key out of source object |
| CMS `display_name` (DB) vs `displayName` (API/enrich) | Map snake → camel in DB client |
| UMS `binary(16)` ids | `BIN_TO_UUID` / `vw_profile_details` before compare |
| Booleans vs `0/1` | Normalized as equivalent when representing same truth |
| Missing subscription on DB path | Field → SKIP (not FAIL) with note |
| Source unreachable (VPN / 1045) | Field → N/A |

Match rule (simplified): after align, string-normalize both sides; equal → **PASS**,
both empty → often SKIP/N/A, else **FAIL**.

---

## 4. Field table (same rows as Results UI)

Counts for current `reports/comparison-latest.json` → `activateFamily`:
**126 fields**.

### Bearer token (3 fields)

| Node | Sub-node | Field (UI) | Enriched JSON path | Source API / origin |
|---|---|---|---|---|
| — | — | globalCustomerId | `actor.globalCustomerId` | JWT claim (actor identity) |
| — | — | globalUserId | `actor.globalUserId` | JWT claim (actor identity) |
| — | — | orgId | `actor.orgId` | JWT claim (actor identity) |

### Raw (16 fields)

| Node | Sub-node | Field (UI) | Enriched JSON path | Source API / origin |
|---|---|---|---|---|
| — | — | enrichmentVersion | `enrichmentVersion` | raw envelope |
| — | — | eventId | `eventId` | envelope |
| — | — | eventVersion | `eventVersion` | raw envelope |
| — | — | actorUserAgent | `source.actorUserAgent` | raw envelope |
| — | — | operationIndex | `source.operationIndex` | raw envelope |
| — | — | operationState | `source.operationState` | raw envelope |
| — | — | platform | `source.platform` | raw envelope |
| — | — | platformEnvironment | `source.platformEnvironment` | raw envelope |
| — | — | platformVersion | `source.platformVersion` | raw envelope |
| — | — | service | `source.service` | raw envelope |
| — | — | xCorrelationId | `xCorrelationId` | envelope |
| fontDetails[0] | family/catalog | id | `subject.enrichedSnapshot.fontDetails[0].family.catalog.id` | resolver/raw entity id (join key) |
| fontDetails[0] | styles[0]/catalog | id | `subject.enrichedSnapshot.fontDetails[0].styles[0].catalog.id` | resolver/raw entity id (join key) |
| fontDetails[0] | styles[0]/variations[0] | id | `subject.enrichedSnapshot.fontDetails[0].styles[0].variations[0].id` | resolver/raw entity id (join key) |
| fontDetails[0] | styles[0]/variations[0]/catalog | id | `subject.enrichedSnapshot.fontDetails[0].styles[0].variations[0].catalog.id` | resolver/raw entity id (join key) |
| operation | — | source | `source.operation` | envelope |

### Resolver (2 fields)

| Node | Sub-node | Field (UI) | Enriched JSON path | Source API / origin |
|---|---|---|---|---|
| customer | source | source | `actor.enrichedSnapshot.customer.source` | enricher constant (asset-management-service) |
| user | source | actor.enrichedSnapshot | `actor.enrichedSnapshot.user.source` | constant |

### Resolver (generated) (1 fields)

| Node | Sub-node | Field (UI) | Enriched JSON path | Source API / origin |
|---|---|---|---|---|
| enrichment | — | enrichedAt | `enrichedAt` | enrichment timestamp |

### CMS (43 fields)

| Node | Sub-node | Field (UI) | Enriched JSON path | Source API / origin |
|---|---|---|---|---|
| customer | displayName | actor.enrichedSnapshot | `actor.enrichedSnapshot.customer.displayName` | GET customer |
| customer | id | actor.enrichedSnapshot | `actor.enrichedSnapshot.customer.id` | GET /api/v2/customers/{gcid} |
| customer | metaData | customerOnboarded | `actor.enrichedSnapshot.customer.metaData.customerOnboarded` | GET /api/v2/customers/{gcid} |
| customer | metaData | invitedPrimaryContactEmail | `actor.enrichedSnapshot.customer.metaData.invitedPrimaryContactEmail` | GET /api/v2/customers/{gcid} |
| customer | metaData | isAnalyticsEnabled | `actor.enrichedSnapshot.customer.metaData.isAnalyticsEnabled` | GET /api/v2/customers/{gcid} |
| customer | metaData | isExpired | `actor.enrichedSnapshot.customer.metaData.isExpired` | GET /api/v2/customers/{gcid} |
| customer | metaData | isResearchParticipationEnabled | `actor.enrichedSnapshot.customer.metaData.isResearchParticipationEnabled` | GET /api/v2/customers/{gcid} |
| customer | metaData | isSSOEnabled | `actor.enrichedSnapshot.customer.metaData.isSSOEnabled` | GET /api/v2/customers/{gcid} |
| customer | metaData | isWorkspaceEnabled | `actor.enrichedSnapshot.customer.metaData.isWorkspaceEnabled` | GET /api/v2/customers/{gcid} |
| customer | metaData | primaryContactId | `actor.enrichedSnapshot.customer.metaData.primaryContactId` | GET /api/v2/customers/{gcid} |
| customer | metaData | supportedLanguage | `actor.enrichedSnapshot.customer.metaData.supportedLanguage` | GET /api/v2/customers/{gcid} |
| customer | metaData/companySettings | enableDownload | `actor.enrichedSnapshot.customer.metaData.companySettings.enableDownload` | GET /api/v2/customers/{gcid} |
| customer | metaData/companySettings | enableFontFormatSelection | `actor.enrichedSnapshot.customer.metaData.companySettings.enableFontFormatSelection` | GET /api/v2/customers/{gcid} |
| customer | metaData/companySettings | enableImportedFonts | `actor.enrichedSnapshot.customer.metaData.companySettings.enableImportedFonts` | GET /api/v2/customers/{gcid} |
| customer | metaData/companySettings | enableSelfHostingKits | `actor.enrichedSnapshot.customer.metaData.companySettings.enableSelfHostingKits` | GET /api/v2/customers/{gcid} |
| customer | metaData/companySettings | enableWebFontAccess | `actor.enrichedSnapshot.customer.metaData.companySettings.enableWebFontAccess` | GET /api/v2/customers/{gcid} |
| customer | name | actor.enrichedSnapshot | `actor.enrichedSnapshot.customer.name` | GET /api/v2/customers/{gcid} |
| customer | subscription | createdAt | `actor.enrichedSnapshot.customer.subscription.createdAt` | GET /api/v2/customers/{gcid} |
| customer | subscription | customerId | `actor.enrichedSnapshot.customer.subscription.customerId` | GET /api/v2/customers/{gcid} |
| customer | subscription | isActive | `actor.enrichedSnapshot.customer.subscription.isActive` | GET /api/v2/customers/{gcid} |
| customer | subscription | isTrial | `actor.enrichedSnapshot.customer.subscription.isTrial` | GET /api/v2/customers/{gcid} |
| customer | subscription | productType | `actor.enrichedSnapshot.customer.subscription.productType` | GET /api/v2/customers/{gcid} |
| customer | subscription | seatsAvailable | `actor.enrichedSnapshot.customer.subscription.seatsAvailable` | GET /api/v2/customers/{gcid} |
| customer | subscription | terminationDate | `actor.enrichedSnapshot.customer.subscription.terminationDate` | GET /api/v2/customers/{gcid} |
| customer | subscription/planDefinition | adsImpressionLimit | `actor.enrichedSnapshot.customer.subscription.planDefinition.adsImpressionLimit` | GET /api/v2/customers/{gcid} |
| customer | subscription/planDefinition | allowClickThroughEula | `actor.enrichedSnapshot.customer.subscription.planDefinition.allowClickThroughEula` | GET /api/v2/customers/{gcid} |
| customer | subscription/planDefinition | allowDownload | `actor.enrichedSnapshot.customer.subscription.planDefinition.allowDownload` | GET /api/v2/customers/{gcid} |
| customer | subscription/planDefinition | allowFontFormatSelectionInFontList | `actor.enrichedSnapshot.customer.subscription.planDefinition.allowFontFormatSelectionInFontList` | GET /api/v2/customers/{gcid} |
| customer | subscription/planDefinition | allowMosaicDTInstall | `actor.enrichedSnapshot.customer.subscription.planDefinition.allowMosaicDTInstall` | GET /api/v2/customers/{gcid} |
| customer | subscription/planDefinition | allowProductionFont | `actor.enrichedSnapshot.customer.subscription.planDefinition.allowProductionFont` | GET /api/v2/customers/{gcid} |
| customer | subscription/planDefinition | allowRemoteActivation | `actor.enrichedSnapshot.customer.subscription.planDefinition.allowRemoteActivation` | GET /api/v2/customers/{gcid} |
| customer | subscription/planDefinition | allowSelfHostingKits | `actor.enrichedSnapshot.customer.subscription.planDefinition.allowSelfHostingKits` | GET /api/v2/customers/{gcid} |
| customer | subscription/planDefinition | enableLicenseManagement | `actor.enrichedSnapshot.customer.subscription.planDefinition.enableLicenseManagement` | GET /api/v2/customers/{gcid} |
| customer | subscription/planDefinition | enableProjectManagement | `actor.enrichedSnapshot.customer.subscription.planDefinition.enableProjectManagement` | GET /api/v2/customers/{gcid} |
| customer | subscription/planDefinition | hasDigitalAdsAccess | `actor.enrichedSnapshot.customer.subscription.planDefinition.hasDigitalAdsAccess` | GET /api/v2/customers/{gcid} |
| customer | subscription/planDefinition | hasWebFontAccess | `actor.enrichedSnapshot.customer.subscription.planDefinition.hasWebFontAccess` | GET /api/v2/customers/{gcid} |
| customer | subscription/planDefinition | isAISearchEnabled | `actor.enrichedSnapshot.customer.subscription.planDefinition.isAISearchEnabled` | GET /api/v2/customers/{gcid} |
| customer | subscription/planDefinition | isPeerInviteAllowed | `actor.enrichedSnapshot.customer.subscription.planDefinition.isPeerInviteAllowed` | GET /api/v2/customers/{gcid} |
| customer | subscription/planDefinition | isPrivateFontAllowed | `actor.enrichedSnapshot.customer.subscription.planDefinition.isPrivateFontAllowed` | GET /api/v2/customers/{gcid} |
| customer | subscription/planDefinition | isWhatTheFontEnabled | `actor.enrichedSnapshot.customer.subscription.planDefinition.isWhatTheFontEnabled` | GET /api/v2/customers/{gcid} |
| customer | subscription/planDefinition | pageViewLimit | `actor.enrichedSnapshot.customer.subscription.planDefinition.pageViewLimit` | GET /api/v2/customers/{gcid} |
| customer | subscription/planDefinition | planType | `actor.enrichedSnapshot.customer.subscription.planDefinition.planType` | GET /api/v2/customers/{gcid} |
| customer | subscription/planDefinition | productionFontCount | `actor.enrichedSnapshot.customer.subscription.planDefinition.productionFontCount` | GET /api/v2/customers/{gcid} |

### UMS (12 fields)

| Node | Sub-node | Field (UI) | Enriched JSON path | Source API / origin |
|---|---|---|---|---|
| user | profile | customerId | `actor.enrichedSnapshot.user.profile.customerId` | POST/GET /api/v3/customers/{gcid}/profiles |
| user | profile | idpUserId | `actor.enrichedSnapshot.user.profile.idpUserId` | POST/GET /api/v3/customers/{gcid}/profiles |
| user | profile | isActive | `actor.enrichedSnapshot.user.profile.isActive` | POST/GET /api/v3/customers/{gcid}/profiles |
| user | profile | lastName | `actor.enrichedSnapshot.user.profile.lastName` | POST/GET /api/v3/customers/{gcid}/profiles |
| user | profile.email | actor.enrichedSnapshot | `actor.enrichedSnapshot.user.profile.email` | POST/GET /api/v3/customers/{gcid}/profiles |
| user | profile.firstName | actor.enrichedSnapshot | `actor.enrichedSnapshot.user.profile.firstName` | POST profiles |
| user | profile.id | actor.enrichedSnapshot | `actor.enrichedSnapshot.user.profile.id` | POST/GET /api/v3/customers/{gcid}/profiles |
| user | role | id | `actor.enrichedSnapshot.user.role.id` | GET /api/v3/customers/{gcid}/roles |
| user | role.displayName | actor.enrichedSnapshot | `actor.enrichedSnapshot.user.role.displayName` | POST/GET /api/v3/customers/{gcid}/profiles |
| user | role/permissions[0] | id | `actor.enrichedSnapshot.user.role.permissions[0].id` | GET /api/v3/customers/{gcid}/roles |
| user | role/permissions[1] | id | `actor.enrichedSnapshot.user.role.permissions[1].id` | GET /api/v3/customers/{gcid}/roles |
| user | role/permissions[2] | id | `actor.enrichedSnapshot.user.role.permissions[2].id` | GET /api/v3/customers/{gcid}/roles |

### Typesense (47 fields)

| Node | Sub-node | Field (UI) | Enriched JSON path | Source API / origin |
|---|---|---|---|---|
| fontDetails[0] | family.catalog.name_en | subject.enrichedSnapshot | `subject.enrichedSnapshot.fontDetails[0].family.catalog.name_en` | POST /v1/styles |
| fontDetails[0] | family.catalog.title_en | subject.enrichedSnapshot | `subject.enrichedSnapshot.fontDetails[0].family.catalog.title_en` | POST /v1/styles |
| fontDetails[0] | family.foundry.name_en | subject.enrichedSnapshot | `subject.enrichedSnapshot.fontDetails[0].family.foundry.name_en` | POST /v1/styles |
| fontDetails[0] | family.id | subject.enrichedSnapshot | `subject.enrichedSnapshot.fontDetails[0].family.id` | POST /v1/styles |
| fontDetails[0] | family/catalog | family_url_key | `subject.enrichedSnapshot.fontDetails[0].family.catalog.family_url_key` | POST /v1/styles |
| fontDetails[0] | family/foundry | handle | `subject.enrichedSnapshot.fontDetails[0].family.foundry.handle` | POST /v1/styles |
| fontDetails[0] | family/foundry | logo | `subject.enrichedSnapshot.fontDetails[0].family.foundry.logo` | POST /v1/styles |
| fontDetails[0] | family/foundry | title_en | `subject.enrichedSnapshot.fontDetails[0].family.foundry.title_en` | POST /v1/styles |
| fontDetails[0] | styles[0].id | subject.enrichedSnapshot | `subject.enrichedSnapshot.fontDetails[0].styles[0].id` | POST /v1/styles |
| fontDetails[0] | styles[0].variations[0].catalog.md5 | subject.enrichedSnapshot | `subject.enrichedSnapshot.fontDetails[0].styles[0].variations[0].catalog.md5` | GET /v1/variations |
| fontDetails[0] | styles[0]/catalog | derelease_date | `subject.enrichedSnapshot.fontDetails[0].styles[0].catalog.derelease_date` | POST /v1/styles |
| fontDetails[0] | styles[0]/catalog | font_name | `subject.enrichedSnapshot.fontDetails[0].styles[0].catalog.font_name` | POST /v1/styles |
| fontDetails[0] | styles[0]/catalog | font_nids[0] | `subject.enrichedSnapshot.fontDetails[0].styles[0].catalog.font_nids[0]` | POST /v1/styles |
| fontDetails[0] | styles[0]/catalog | font_nids[1] | `subject.enrichedSnapshot.fontDetails[0].styles[0].catalog.font_nids[1]` | POST /v1/styles |
| fontDetails[0] | styles[0]/catalog | font_pim_style_id | `subject.enrichedSnapshot.fontDetails[0].styles[0].catalog.font_pim_style_id` | POST /v1/styles |
| fontDetails[0] | styles[0]/catalog | font_ps_names[0] | `subject.enrichedSnapshot.fontDetails[0].styles[0].catalog.font_ps_names[0]` | POST /v1/styles |
| fontDetails[0] | styles[0]/catalog | font_url_key | `subject.enrichedSnapshot.fontDetails[0].styles[0].catalog.font_url_key` | POST /v1/styles |
| fontDetails[0] | styles[0]/catalog | is_custom | `subject.enrichedSnapshot.fontDetails[0].styles[0].catalog.is_custom` | POST /v1/styles |
| fontDetails[0] | styles[0]/catalog | is_default | `subject.enrichedSnapshot.fontDetails[0].styles[0].catalog.is_default` | POST /v1/styles |
| fontDetails[0] | styles[0]/catalog | is_imported_font | `subject.enrichedSnapshot.fontDetails[0].styles[0].catalog.is_imported_font` | POST /v1/styles |
| fontDetails[0] | styles[0]/catalog | is_var | `subject.enrichedSnapshot.fontDetails[0].styles[0].catalog.is_var` | POST /v1/styles |
| fontDetails[0] | styles[0]/catalog | md5[0] | `subject.enrichedSnapshot.fontDetails[0].styles[0].catalog.md5[0]` | POST /v1/styles |
| fontDetails[0] | styles[0]/catalog | md5[1] | `subject.enrichedSnapshot.fontDetails[0].styles[0].catalog.md5[1]` | POST /v1/styles |
| fontDetails[0] | styles[0]/catalog | render_md5 | `subject.enrichedSnapshot.fontDetails[0].styles[0].catalog.render_md5` | POST /v1/styles |
| fontDetails[0] | styles[0]/catalog/visual_properties | contrast | `subject.enrichedSnapshot.fontDetails[0].styles[0].catalog.visual_properties.contrast` | POST /v1/styles |
| fontDetails[0] | styles[0]/catalog/visual_properties | height | `subject.enrichedSnapshot.fontDetails[0].styles[0].catalog.visual_properties.height` | POST /v1/styles |
| fontDetails[0] | styles[0]/catalog/visual_properties | slant | `subject.enrichedSnapshot.fontDetails[0].styles[0].catalog.visual_properties.slant` | POST /v1/styles |
| fontDetails[0] | styles[0]/catalog/visual_properties | weight | `subject.enrichedSnapshot.fontDetails[0].styles[0].catalog.visual_properties.weight` | POST /v1/styles |
| fontDetails[0] | styles[0]/catalog/visual_properties | width | `subject.enrichedSnapshot.fontDetails[0].styles[0].catalog.visual_properties.width` | POST /v1/styles |
| fontDetails[0] | styles[0]/variations[0]/catalog | derelease_date | `subject.enrichedSnapshot.fontDetails[0].styles[0].variations[0].catalog.derelease_date` | GET /v1/variations |
| fontDetails[0] | styles[0]/variations[0]/catalog | font_filename | `subject.enrichedSnapshot.fontDetails[0].styles[0].variations[0].catalog.font_filename` | GET /v1/variations |
| fontDetails[0] | styles[0]/variations[0]/catalog | font_format | `subject.enrichedSnapshot.fontDetails[0].styles[0].variations[0].catalog.font_format` | GET /v1/variations |
| fontDetails[0] | styles[0]/variations[0]/catalog | font_identifier | `subject.enrichedSnapshot.fontDetails[0].styles[0].variations[0].catalog.font_identifier` | GET /v1/variations |
| fontDetails[0] | styles[0]/variations[0]/catalog | font_psname | `subject.enrichedSnapshot.fontDetails[0].styles[0].variations[0].catalog.font_psname` | GET /v1/variations |
| fontDetails[0] | styles[0]/variations[0]/catalog | font_stretch | `subject.enrichedSnapshot.fontDetails[0].styles[0].variations[0].catalog.font_stretch` | GET /v1/variations |
| fontDetails[0] | styles[0]/variations[0]/catalog | font_style | `subject.enrichedSnapshot.fontDetails[0].styles[0].variations[0].catalog.font_style` | GET /v1/variations |
| fontDetails[0] | styles[0]/variations[0]/catalog | font_version | `subject.enrichedSnapshot.fontDetails[0].styles[0].variations[0].catalog.font_version` | GET /v1/variations |
| fontDetails[0] | styles[0]/variations[0]/catalog | font_weight | `subject.enrichedSnapshot.fontDetails[0].styles[0].variations[0].catalog.font_weight` | GET /v1/variations |
| fontDetails[0] | styles[0]/variations[0]/catalog | font_width | `subject.enrichedSnapshot.fontDetails[0].styles[0].variations[0].catalog.font_width` | GET /v1/variations |
| fontDetails[0] | styles[0]/variations[0]/catalog | fontsenseid | `subject.enrichedSnapshot.fontDetails[0].styles[0].variations[0].catalog.fontsenseid` | GET /v1/variations |
| fontDetails[0] | styles[0]/variations[0]/catalog | glyph_count | `subject.enrichedSnapshot.fontDetails[0].styles[0].variations[0].catalog.glyph_count` | GET /v1/variations |
| fontDetails[0] | styles[0]/variations[0]/catalog | is_custom | `subject.enrichedSnapshot.fontDetails[0].styles[0].variations[0].catalog.is_custom` | GET /v1/variations |
| fontDetails[0] | styles[0]/variations[0]/catalog | matno | `subject.enrichedSnapshot.fontDetails[0].styles[0].variations[0].catalog.matno` | GET /v1/variations |
| fontDetails[0] | styles[0]/variations[0]/catalog | sub_family_name | `subject.enrichedSnapshot.fontDetails[0].styles[0].variations[0].catalog.sub_family_name` | GET /v1/variations |
| fontDetails[0] | styles[0]/variations[0]/catalog | variation_name | `subject.enrichedSnapshot.fontDetails[0].styles[0].variations[0].catalog.variation_name` | GET /v1/variations |
| fontDetails[0] | styles[0]/variations[0]/catalog | variety | `subject.enrichedSnapshot.fontDetails[0].styles[0].variations[0].catalog.variety` | GET /v1/variations |
| source | — | subject.enrichedSnapshot | `subject.enrichedSnapshot.source` | POST /v1/styles |

### Unknown (2 fields)

| Node | Sub-node | Field (UI) | Enriched JSON path | Source API / origin |
|---|---|---|---|---|
| — | — | id[0] | `subject.id[0]` | — |
| — | — | type | `subject.type` | — |

> Tip: open Results → filter `activateFamily`. Source file: `reports/comparison-latest.json` → `activateFamily.rows`.

## 5. Bulk queries for ~250 events (minimal fan-out)

### Naive cost (bad)

Per event × sources ≈ 250 × (1 CMS + 1–2 UMS + 0–1 AMS) + Discovery
→ hundreds of round trips, mostly **duplicate**.

### Real pattern

Across 250 events for *our* Bearer, uniqueness collapses:

| Entity | Typical unique count | Fetch strategy |
|---|---|---|
| CMS `gcid` | **1–few** tenants | `WHERE id IN (…)`, or 1 HTTP per unique gcid (cache) |
| UMS profile | **1–few** actors | `vw_profile_details WHERE profile_Id_uuid IN (…)` |
| UMS role | **few** | `roles WHERE BIN_TO_UUID(id) IN (…)` |
| AMS asset | **0…N** (activateFamily = 0) | `assets WHERE asset_id IN (…)` |
| Typesense family/style/md5 | **N families** | already **batched** (50-id chunks) in `_prefetch_discovery` |

Worked example for “we always activate as Sachin @ Everest Admin”:

```
250 events
→ 1 CMS row
→ 1 UMS profile + 1 role
→ Discovery: unique familyIds only (e.g. 30–80 fonts, not 250)
→ AMS: 0 calls for activateFamily
```

### Bulk SQL templates

```sql
-- CMS
SELECT id, name, display_name, source, metaData
FROM customer_management.customers
WHERE id IN ( /* distinct actor.globalCustomerId */ );

-- UMS profiles
SELECT *
FROM user_management.vw_profile_details
WHERE profile_Id_uuid IN ( /* distinct actor.globalUserId */ )
  AND (is_deleted = 0 OR is_deleted IS NULL);

-- UMS roles
SELECT LOWER(BIN_TO_UUID(id)) AS id, display_name, type_id, description
FROM user_management.roles
WHERE BIN_TO_UUID(id) IN ( /* distinct role ids from profiles / enrich */ );

-- AMS (asset ops only)
SELECT asset_id, asset_type, created_by, global_customer_id, meta_data
FROM asset_management.assets
WHERE asset_id IN ( /* distinct subject asset ids */ );
```

### Bulk Discovery (already in runner)

```text
POST /v1/styles   familyIds=[… up to 50 …]
POST /v1/styles   styleIds=[… chunks of 50 …]
GET  /v1/variations stylesIds=…
GET  /v1/variations md5s=…   (only missing md5s)
```

Then each event resolves font fields from the **shared hit cache** (no per-event Discovery call).

### Product rule we enforce in code

1. Collect distinct ids across all samples up front.  
2. Prefetch once into `cms_by_id` / `ums_profile_by_id` / `ums_role_by_id` / `ams_by_id` / Discovery hits.  
3. Per-event Compare only **looks up** the cache.

Typesense stays HTTP (no MySQL). CMS/UMS/AMS prefer MySQL IN-lists when `SOURCE_TRUTH=db`.

---

## 6. Quick self-check (this sample)

| Check | Expected |
|---|---|
| CMS displayName | `Everest Admin` |
| UMS email | `everestadmin.sachin@gmail.com` |
| UMS role | `Company Admin` |
| Discovery style font_name | `1066 Hastings Normal` |
| Variation md5 | includes `dd64034b42a293cce9307176f6ff49fe` |

```bash
PYTHONPATH=python backend/.venv/bin/python -m audit_validator.source_validation.db.smoke
```
