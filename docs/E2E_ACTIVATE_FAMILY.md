# End-to-End Guide: activateFamily

A complete walkthrough for new team members — from triggering the event to validating every enriched node against live APIs.

## What is activateFamily?

When a user activates a font family in NextGen, `mtconnect-api` emits an audit event. The resolver enriches it with data from UMS (user), CMS (customer), and Discovery/Typesense (font catalog), then publishes to RabbitMQ and MongoDB.

**Routing key:** `font.activation.success`  
**Domain template:** `fontActivation-family`

---

## Step 1 — Trigger the event (Simulation)

### UI path
Dashboard → Search → Select font family → Family details → **Activate**

### API call (GraphQL mutation)

```bash
curl -sS -X POST "https://nextgen.monotype-pp.com/graph" \
  -H "Authorization: Bearer $NEXTGEN_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -H "x-context-customerid: $GRAPHQL_CONTEXT_CUSTOMER_ID" \
  -H "accept: application/graphql-response+json,application/json;q=0.9" \
  -H "origin: https://nextgen.monotype-pp.com" \
  -H "referer: https://nextgen.monotype-pp.com/discover-fonts/all" \
  -d '{
    "operationName": "ActivateFamily",
    "variables": { "input": { "familyIds": ["794981"] } },
    "extensions": { "clientLibrary": { "name": "@apollo/client", "version": "4.0.9" } },
    "query": "mutation ActivateFamily($input: ActivateFamilyInput) { activateFamily(input: $input) { errors { code message } families { nodes { id activatedStatus { activationState } } } } }"
  }'
```

### Pre-transition logic

If the family is already `ACTIVATED`, automation first calls `deactivateFamilies` so the audit log captures a real state change (DEACTIVATED → ACTIVATED).

### Environment variables

| Variable | Purpose |
|----------|---------|
| `NEXTGEN_BEARER_TOKEN` | Browser SSO JWT for `/graph` mutations |
| `GRAPHQL_CONTEXT_CUSTOMER_ID` | Customer context header |
| `SEED_FAMILY_ID` | Family ID to activate (e.g. `794981`) |

**Postman:** `docs/postman/Simulation.postman_collection.json` → Font Activation → activateFamily

---

## Step 2 — Raw event capture

The mutation triggers a raw envelope on RabbitMQ queue `mt.platform.resolver.rawpayload`, captured to:

```
mt-audit-log-automation/payload/raw/activateFamily.json
```

### Key raw nodes

| Node | Example value | Meaning |
|------|---------------|---------|
| `xCorrelationId` | UUID | Pairs raw ↔ enriched |
| `source.service` | `mtconnect-api` | Originating service |
| `source.operation` | `activateFamily` | Operation name |
| `source.operationState` | `success` | Outcome |
| `actor.globalUserId` | Profile UUID | Who performed the action |
| `actor.globalCustomerId` | GCID | Customer scope |
| `subject.type` | `fontFamily` | Subject entity type |
| `subject.id` | `["910130728"]` | Family ID(s) |
| `subject.metadata.input.familyIds` | `["910130728"]` | Mutation input |
| `subject.metadata.result.families.nodes` | Array | GQL response families |

### View in UI

**Enrich/raw collection** → Collection: **Raw** → find `activateFamily` entry (latest per operation by default).

---

## Step 3 — Enrichment

The resolver calls UMS, CMS, and Discovery, then publishes to `mt.platform.resolver.enrichpayload` with routing key `font.activation.success`.

Captured to:

```
mt-audit-log-automation/payload/enrich/activateFamily.json
```

### What gets added

| Enriched path | Source API | Description |
|---------------|------------|-------------|
| `enrichmentVersion` | Resolver | Schema version |
| `actor.enrichedSnapshot.user.profile.id` | UMS | Profile ID |
| `actor.enrichedSnapshot.user.profile.email` | UMS | User email |
| `actor.enrichedSnapshot.user.role.displayName` | UMS | Role name |
| `actor.enrichedSnapshot.customer.id` | CMS | Customer ID |
| `actor.enrichedSnapshot.customer.name` | CMS | Customer name |
| `subject.enrichedSnapshot.fontDetails[0].family.id` | Discovery | Catalog family ID |
| `subject.enrichedSnapshot.fontDetails[0].family.catalog.name_en` | Discovery/Typesense | Family name |
| `subject.enrichedSnapshot.fontDetails[0].family.foundry.name_en` | Discovery | Foundry |
| `subject.enrichedSnapshot.fontDetails[0].styles[0].id` | Discovery | Style ID |
| `subject.enrichedSnapshot.fontDetails[0].styles[0].variations[0].catalog.md5` | Discovery | Variation MD5 |
| `subject.activationType` | Resolver | e.g. `permanent` |
| `subject.activationMode` | Resolver | e.g. `manual` |

### View in UI

**Enrich/raw collection** → Collection: **Enriched** → `activateFamily`

---

## Step 4 — Queue validation (3 layers)

The validator pairs raw and enriched by `xCorrelationId` and runs:

### Layer 1 — Envelope
- `eventVersion`, `enrichmentVersion` present
- `source.operation` = `activateFamily`
- Actor and subject types correct

### Layer 2 — Outcome
- On `success`: requires `actor.enrichedSnapshot` + `subject.enrichedSnapshot`

### Layer 3 — Template `fontActivation-family`
- `subject.type` = `fontFamily`
- `subject.enrichedSnapshot.fontDetails` present
- `metadata.input.familyIds` preserved from raw
- `metadata.result.families` preserved from raw

### Raw ↔ enriched compare
Fields present in raw must match in enriched. Enrichment-only fields are allowed on the enriched side.

---

## Step 5 — Source verification (live API probes)

Run via UI: **Compare** → select `activateFamily` → **Compare** → view **Result**.

Or via API:

```bash
curl -X POST http://localhost:3200/api/jobs/compare \
  -H "Content-Type: application/json" \
  -d '{"operations": ["activateFamily"], "sample_source": "fresh"}'
```

### 5a. UMS — Actor profile

Extract from enriched JSON:
- `actor.globalUserId` → `profile_id`
- `actor.globalCustomerId` → `global_customer_id`

```bash
curl -X POST "https://usermanagement-pp.monotype.com/api/v3/customers/$GCID/profiles" \
  -H "x-client-id: mt-events-resolver-service" \
  -H "x-correlation-id: $(uuidgen)" \
  -H "Content-Type: application/json" \
  -H "X-HTTP-Method-Override: GET" \
  -d '{
    "projection": "isActive,firstName,lastName,email,role.id",
    "filter": { "#and": { "isActive": { "eq": true }, "profile.id": { "in": ["'"$PROFILE_ID"'"] } } },
    "limit": 1
  }'
```

**Validates:** `actor.enrichedSnapshot.user.profile.email`, `firstName`, `lastName`

### 5b. UMS — Role

```bash
curl -G "https://usermanagement-pp.monotype.com/api/v3/customers/$GCID/roles" \
  -H "x-client-id: mt-events-resolver-service" \
  --data-urlencode "filter=$ROLE_ID" \
  --data-urlencode "filterType=id"
```

**Validates:** `actor.enrichedSnapshot.user.role.displayName`

### 5c. CMS — Customer

```bash
curl -G "https://customermanagement-preprod.monotype.com/api/v2/customers/$GCID" \
  -H "x-client-id: mt-events-resolver-service" \
  --data-urlencode "projection=id,name,displayName,subscription" \
  --data-urlencode "application=MTConnect"
```

**Validates:** `actor.enrichedSnapshot.customer.id`, `name`

### 5d. Discovery (Typesense middleware) — Font data

Extract `subject.id[0]` or `subject.metadata.input.familyIds[0]` as `family_id`.

```bash
curl -X POST "https://mtc-middleware-discovery.monotype-pp.com/v1/styles" \
  -H "Authorization: Bearer $NEXTGEN_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -H "x-correlation-id: $(uuidgen)" \
  -d '{"familyIds": ["'"$FAMILY_ID"'"], "page": 1, "per_page": 250}'
```

```bash
curl -G "https://mtc-middleware-discovery.monotype-pp.com/v1/variations" \
  -H "Authorization: Bearer $NEXTGEN_BEARER_TOKEN" \
  --data-urlencode "familyIds=$FAMILY_ID" \
  --data-urlencode "page=1" \
  --data-urlencode "perPage=250"
```

**Validates:** family catalog names, style IDs, variation MD5s in `subject.enrichedSnapshot.fontDetails`

**Postman:** `docs/postman/Verification.postman_collection.json`

---

## Step 6 — Review results

### UI (Result section)

- Grouped by event (`activateFamily`)
- Each field shows enriched vs source side-by-side
- PASS / FAIL / SKIP badges
- **Download Excel** — same format as `source-comparison.xlsx`

### Excel columns

| Column | Example |
|--------|---------|
| event | `activateFamily` |
| field | `catalog.name_en` |
| node/subnode | `fontDetails / family / catalog` |
| value_in_enriched_json | `Helvetica Neue` |
| value_in_source_json | `Helvetica Neue` |
| source | `Typesense` |
| status | `PASS` |
| remark | (empty or API error) |
| routing_key | `font.activation.success` |

### File outputs (validator)

| File | Location |
|------|----------|
| Validation report | `mt-audit-log-automation/temp/validation.json` |
| Source comparison | `mt-audit-log-automation/result/source-comparison.xlsx` |
| E2E results | `mt-audit-log-automation/result/result.xlsx` |

---

## Full pipeline (one command)

```bash
cd mt-audit-log-automation/python
source ../venv/bin/activate
SKIP_REFRESH_TOKENS=true ./run.sh
```

Or via UI: **Generate** → **Generate events** → wait for completion → **Compare** → select ops → **Result**.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| GQL fails in job logs | Expired `NEXTGEN_BEARER_TOKEN` | Refresh browser SSO token in `.env` |
| Raw YES, enriched NO | Resolver backlog / DLQ | Check DLQ collection; increase settle time |
| Discovery 401 in results | Wrong token type for Discovery | Use `NEXTGEN_BEARER_TOKEN` (browser SSO), not OAuth |
| `family.id` FAIL | Stale catalog or wrong family ID | Re-run E2E; verify `SEED_FAMILY_ID` exists |
| Duplicate operations in collection view | Filter not applied | Default shows latest per type; filter by operation to see all entries |

---

## Quick reference

| Item | Value |
|------|-------|
| GraphQL endpoint | `https://nextgen.monotype-pp.com/graph` |
| Raw queue | `mt.platform.resolver.rawpayload` |
| Enriched queue | `mt.platform.resolver.enrichpayload` |
| Routing key | `font.activation.success` |
| Mongo filter | `source.operation` = `activateFamily` |
| UI collection | Enrich/raw → Enriched or Raw |
