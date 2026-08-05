# NextGen Audit Automation — Technical Documentation

This document describes the architecture, data flow, and API surface of the **NextGen Audit Automation** UI and its integration with **mt-audit-log-automation**.

## Overview

| Component | Path | Role |
|-----------|------|------|
| Frontend | `frontend/` | React SPA — Generate, Enrich/raw, Compare, Result |
| Backend | `backend/app/` | FastAPI — Mongo queries, background job runner |
| Validator | `python/audit_validator/` (vendored in-repo) | Simulation, enrichment validation, source probes |

The UI wraps the Python pipelines vendored in this repository. You do not need to know whether an event came from GraphQL, Ingress, or Cron — the pipeline handles that under the hood.

## Generatable catalog (Type filter)

The Generate page lists **everything the pipeline can produce**, tagged by source kind
(`GET /api/meta/operation-sources` → `audit_validator/operation_sources.py`):

| Kind | Count | Source | How it's generated |
|------|-------|--------|--------------------|
| **GraphQL** | ~199 | GraphQL documents (mtconnect-api / NextGen `/graph`) | Simulation flows |
| **Ingress** | ~29 | `data/ingress_payloads/` (desktop / plugin / UI events) | Replayed through the resolver Ingress API |
| **Cron** | ~22 | `data/cron_payloads/` (BYOF licence, subscription/token/account expiry, LMS…) | Published onto the raw queue |

Catalog item ids are stable: GraphQL uses the operation name, Ingress uses
`ingress:<case_id>`, Cron uses `cron:<case_id>`. Selecting items of any kind routes each
bucket to the right injector (`split_selection()`), so you can generate a single cron
payload or a handful of plugin events without triggering the whole suite.

> The earlier Generate page only counted `tracked_operations()` (GraphQL), which is why
> ingress showed **1** and cron **9** — those were *distinct operation names*, not the
> 29 ingress events / 22 cron payloads that actually exist.

## Targeted runs are bounded and non-destructive

When you select specific operations, a **generate+validate** run:

- **never purges** the shared platform queue (`mt.platform.resolver.*payload`) — purging it
  starved the ingestion service and other consumers (this caused the "purged 2043 messages"
  behaviour and the multi-minute freeze),
- caps the settle wait at `TARGETED_SETTLE_SEC` (default **60s**) and exits **as soon as the
  selected operations have raw+enriched pairs** (`QueueEventCollector.wait_for_operations`),
  instead of waiting for the whole busy queue to go idle (`wait_until_settled` +
  `wait_for_missing_enriched`, which never converge on a shared queue),
- disables cron injection unless a cron item is explicitly selected.

## How enrichment actually works (mt-audit-log-resolver-service)

The resolver is **event-driven and enrich-once**: a raw event is enriched a single time and
republished to `mt.platform.events`. There is **no continuous / scheduled re-enrichment** —
already-enriched messages are skipped (`enrichmentVersion >= 1` or `enrichedAt` present), and
every pass stamps `enrichmentVersion: 1`, a fresh `enrichedAt`, and a new `enrichedEventId`.

Field → source-API mapping the validator uses (`enriched_field_scanner.infer_source_system`):

| Enriched block | Source | Validated? |
|----------------|--------|-----------|
| `actor.enrichedSnapshot.user.{profile,role,teams}` | UMS | ✅ probed |
| `actor.enrichedSnapshot.customer.*`, `.subscription.*` | CMS | ✅ probed |
| `subject.enrichedSnapshot.fontDetails[].{family,styles,variations}.catalog` | Discovery/Typesense | ✅ probed |
| `subject.enrichedSnapshot.users[]` (service accounts) | UMS | ✅ probed |
| `subject.enrichedSnapshot.asset`, `sharingInfo` | AMS | ✅ probed |
| `subject.enrichedSnapshot.contract.*` | BYOF-License | accepted (not probed yet) |
| `subject.batchDetails.*` | Batch-Orchestration | accepted (not probed yet) |
| `isImportedFont`, `activationType`, `activationMode` | enricher-added flags/defaults | accepted (not from any API) |

The comparison compares **every scalar present in the enriched snapshot** against its source
(`SOURCE_VALIDATION_MAPPED_ONLY=false` by default). Blocks marked "accepted (not probed)" are
sourced by services the validator doesn't yet call, so they PASS as-is rather than producing
false SKIP/FAIL. LMS / SSO / Font-Version clients exist in the resolver but are **not wired
into enrichment**, so no such fields appear in the output.

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────────┐
│  React UI       │────▶│  FastAPI backend │────▶│  audit_validator    │
│  (port 5174)    │     │  (port 3200)     │     │  (Python package)   │
└─────────────────┘     └────────┬─────────┘     └──────────┬──────────┘
                                 │                            │
                                 ▼                            ▼
                          ┌─────────────┐              ┌──────────────┐
                          │  MongoDB    │              │  RabbitMQ +  │
                          │  raw/enrich │              │  live APIs   │
                          └─────────────┘              └──────────────┘
```

## Data flow: how RabbitMQ and MongoDB relate

**Two ways Mongo gets populated:**

1. **Built-in ingestion service** (`audit_validator.ingestion`, ported from `audit-sense`)
   — this repo can now drain the platform **subscription** queues into Mongo itself, so
   the project is self-contained (no external `audit-sense` process required). Start it from
   the **Enrich/raw** page ("Live ingestion" panel), via `POST /api/ingestion/start`, or run
   it standalone with `scripts/ingest.sh` (`python -m audit_validator.ingestion`).
2. The standalone **`audit-sense`** Node service (or the platform's persistence service),
   if you prefer to run that instead.

Run **one** of these at a time — they consume the same subscription queues, so running both
splits messages between them.

The `audit_validator` **validator** (source validation / compare) still never writes to Mongo
— it only reads. The relationship is:

```
 (1) We trigger an event (GraphQL simulation / Ingress API / cron)
        │
        ▼
 (2) Platform resolver publishes to RabbitMQ:
        • raw envelope     → exchange mt.platform.raw_events → queue mt.platform.resolver.rawpayload
        • enriched envelope→ exchange mt.platform.events     → queue mt.platform.resolver.enrichpayload
        │
        ├──────────────► (3a) Platform persistence service consumes those queues and
        │                      **writes** the documents into MongoDB `AuditLogsPreprod`
        │                      collections: raw / enriched / dlq.  ← this is what the
        │                      Enrich/raw UI reads.
        │
        └──────────────► (3b) Our validator ALSO taps the same queues live (passive
                               consume) during a Generate run to capture the raw+enriched
                               pair for the correlation id we just produced, saving them to
                               `payload/raw` and `payload/enrich` for immediate validation.
```

So there are **two distinct kinds of consumer** of RabbitMQ:

| Consumer | Queues | Writes to Mongo? | Purpose |
|----------|--------|------------------|---------|
| Ingestion service (`audit_validator.ingestion`) | `*.exchange.subscription` (catch-all `#`) | **Yes** — batch `insert_many` | Continuously persist **all** events → what the Enrich/raw page browses |
| `audit_validator` collector | `mt.platform.resolver.*payload` (resolver taps) | No (writes local `payload/` files) | Live capture of the pair we just generated during a Generate run |

### The ingestion service (RabbitMQ → Mongo)

Ported from `audit-sense`. Code: `python/audit_validator/ingestion/`.

- One consumer thread per queue, each with its own `pika` connection and auto-reconnect.
- Messages are batched and flushed to Mongo every `INGEST_BATCH_FLUSH_INTERVAL_MS` (default 5s);
  acks are sent (`multiple=True`) only **after** a successful insert, and nacked+requeued on
  repeated insert failure — so no data is lost. Acking removes the message from the queue, so a
  message is effectively purged from RabbitMQ as soon as it lands in Mongo.
- Ensures the same indexes `audit-sense` creates.
- A cleanup thread runs every `INGEST_CLEANUP_INTERVAL_MS` (default 30s) and keeps only the
  **latest `INGEST_CLEANUP_MAX_DOCS_PER_OPERATION` docs per `source.operation`** (default 20),
  sorted by `occurredAt` — this is why the collections stay bounded and show the latest N per event.

Because it consumes the catch-all subscription queues, **every** event (raw and enriched) is
captured, which is what guarantees complete raw+enrich pairs are available to validate.

| Purpose | Env var | Default queue |
|---------|---------|---------------|
| Raw (ingestion) | `INGEST_RAW_QUEUE` | `mt.platform.raw_events.exchange.subscription` |
| Enriched (ingestion) | `INGEST_ENRICHED_QUEUE` | `mt.platform.events.exchange.subscription` |
| DLQ (ingestion) | `INGEST_DLQ_QUEUE` | `mt.platform.raw_events.exchange.subscription.dlq` |

Control endpoints: `GET /api/ingestion/status`, `POST /api/ingestion/start`, `POST /api/ingestion/stop`,
`POST /api/ingestion/purge` (drops any remaining queued backlog — set `INGEST_PURGE_ON_START=true` to
do this automatically on each start).

### Keeping Mongo bounded (retention)

Two layers keep each collection small (max ~20 docs per operation), so a filtered view of e.g.
`getBatches` / `activateFamily` never balloons:

1. **Ingestion cleanup** (only while ingestion runs) — every 30s, keeps latest
   `INGEST_CLEANUP_MAX_DOCS_PER_OPERATION` (default 20) per operation.
2. **Backend retention scheduler** (always, even when ingestion is stopped) — runs once at
   startup and then every `MONGO_RETENTION_INTERVAL_SEC` (default 3600s / hourly), trimming
   `raw` + `enriched` + `dlq` to the latest `MONGO_RETENTION_MAX_DOCS_PER_OPERATION` (default 20)
   per operation. Trigger it manually with `POST /api/mongo/prune` (optional `?max_docs=N`).

The Enrich/Raw UI also caps a **single-operation filter** to the latest 5 entries — enough to
inspect recent activity without paging through dozens of duplicates.

### Which queues (per-run validator tap)

The validator's live capture uses different queues, consumed **passively**
(`RAW_QUEUE_PASSIVE`/`ENRICHED_QUEUE_PASSIVE` default `true`) — it never re-declares or rebinds,
only reads. Consumer code: `audit_validator/rabbitmq/collector.py`.

| Purpose | Env var | Default queue |
|---------|---------|---------------|
| Raw events | `RAW_EVENTS_QUEUE` | `mt.platform.resolver.rawpayload` |
| Enriched events | `ENRICHED_EVENTS_QUEUE` | `mt.platform.resolver.enrichpayload` |
| Dead-letter | `DEAD_LETTER_QUEUE` | `mt.platform.raw_events.resolver.dlq` |

### Which Mongo database / collections

| Setting | Env var | Default |
|---------|---------|---------|
| Database | `MONGO_DB_NAME` | `AuditLogsPreprod` |
| Raw collection | `MONGO_COLLECTION_RAW` | `raw` |
| Enriched collection | `MONGO_COLLECTION_ENRICHED` | `enriched` |
| DLQ collection | `MONGO_COLLECTION_DLQ` | `dlq` |
| Connection | `MONGO_DB_URL` | (preprod URI in `.env`) |

### Confirming Mongo is receiving fresh data

`GET /health` pings Mongo. `GET /api/meta/operation-stats` returns live distinct-operation
counts straight from the raw and enriched collections — if those numbers grow after a Generate
run, Mongo is receiving fresh data. In the UI, the **Generate** page shows this as a funnel
(tracked → in raw+enriched → true pairs).

### Why "208 generated" but only ~170 validated

An operation is only **validatable** when it lands in **both** the raw **and** enriched
collections **with a matching `xCorrelationId`** (a true pair). The funnel:

1. **Tracked operations** — everything the tool knows how to trigger (~208).
2. **In raw + enriched** — operations present in *both* collections (~171). Some events only
   ever produce a raw envelope (queries, fire-and-forget) or arrive enriched-only, so they drop out here.
3. **True pairs** — of those, the ones whose latest raw and enriched share the same
   `xCorrelationId` (~146). Raw/enriched from *different* runs are intentionally skipped so we
   never compare mismatched events (logged as `⚠ Skip … xCorrelationId differs`).

The Generate/Result pages surface each step, and validation logs list exactly which operations
were paired vs skipped and why.

## UI Sections

### 1. Generate

**Purpose:** Run the full audit pipeline in the background.

- Single **Generate events** action — no mode/flow selection exposed
- Optional: skip already-passed operations
- Polls job logs until complete

**Backend:** `POST /api/jobs/generate` with `mode: "full"`, `include_ingress: true`

### 2. Enrich/raw collection

**Purpose:** Browse MongoDB audit log collections.

| Collection | Mongo collection env | Contents |
|------------|---------------------|----------|
| Raw | `MONGO_COLLECTION_RAW` | Unenriched envelopes from resolver |
| Enriched | `MONGO_COLLECTION_ENRICHED` | UMS/CMS/Discovery-enriched events |
| DLQ | `MONGO_COLLECTION_DLQ` | Dead-letter / failed enrichments |

**Default view:** Latest entry **per unique operation** (deduplicated).  
**Filtered view:** All matching entries when any filter is applied.

**Filters:**
- Text fields (press **Enter** to apply): `xCorrelationId`, `source.operation`, `actor.globalUserId`
- **Multi-select dropdowns** (populated from live distinct values, apply on change):
  `source.platformEnvironment`, `source.service`, `source.operationState`. Selecting several
  values matches any of them (`$in`).

**JSON:** each event's message JSON is **collapsed by default** — click *Expand* to view, *Copy JSON* to copy.

**How to trigger this event:** every event card has a *How to trigger this event* toggle that shows:
- **UI navigation** path(s) from `docs/UI Navigation of Event.xlsx` (e.g. `Discover Fonts > Family > Activate`).
- A **Copy curl** button that builds a ready-to-run curl for the operation:
  - GraphQL operations → POST to the NextGen `/graph` or `/graphql` endpoint with the operation's
    GraphQL document and `variables.input` taken from the captured raw event's `subject.metadata.input`.
  - Ingress operations (desktop/plugin) → POST the captured raw envelope to the resolver Ingress API.
  - The token is emitted as `$BEARER_TOKEN` (export it in your shell first); paste into a terminal or Postman.

### 3. Compare

**Purpose:** Select operations that exist in both raw and enriched Mongo collections, then run source validation.

### 4. Result

**Purpose:** Interactive field-level comparison (PASS/FAIL/SKIP) with grouped event cards and Excel download.

Excel columns match `source-comparison.xlsx`:

`event | field | node/subnode | value_in_enriched_json | value_in_source_json | source | status | remark | routing_key`

## Backend API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Mongo + project root status |
| GET | `/api/raw`, `/api/enriched`, `/api/dlq` | Filtered logs (`unique=true` default) |
| GET | `/api/meta/comparable-operations` | Ops in both raw + enriched |
| GET | `/api/meta/filter-values` | Distinct env/service/state values for filter dropdowns |
| GET | `/api/meta/operation-stats` | Funnel: tracked → in both → true pairs (explains generate vs validate gap) |
| GET | `/api/meta/ui-navigation` | UI navigation paths per operation (from Excel) |
| GET | `/api/curl/{operation}` | Copy-pasteable curl (GraphQL or Ingress) with the real `BEARER_TOKEN` embedded |
| GET | `/api/ingestion/status` | Live RabbitMQ → Mongo ingestion status + per-queue counters |
| POST | `/api/ingestion/start` | Start the ingestion service |
| POST | `/api/ingestion/stop` | Stop the ingestion service |
| POST | `/api/ingestion/purge` | Purge remaining queued backlog (keep only fresh events) |
| POST | `/api/mongo/prune` | Trim collections to latest N docs/op (`?max_docs=N`, default 20) |
| GET | `/api/health/apis` | Reachability + workability of every dependency (Mongo, RabbitMQ, GraphQL, Ingress, CMS, UMS, Discovery, **user_invitation SQL**, **privateTag**) |
| POST | `/api/health/probe/{target}` | Re-run one probe on demand (button click) |

### Network / VPN requirement (important)

All preprod dependencies except MongoDB Atlas live **behind the corporate network**:

- **RabbitMQ** (`rabbitmq-preprod.monotype-pp.com:5671`) — TCP connect times out off-VPN.
- **CMS / UMS / Discovery / GraphQL** (`*.monotype.com`, `*.monotype-pp.com`) — sit behind
  Cloudflare, which returns **HTTP 403 `error code: 1006`** (edge access-denied) to non-allowlisted
  IPs.

When **off the corporate VPN**: live ingestion cannot connect (panel shows *"unreachable — check
VPN"*), and source validation cannot fetch CMS/UMS/Discovery values. Those rows are marked **N/A**
(*"Source API unreachable — connect to VPN"*), **not** SKIP or FAIL, and the Results page shows a
banner. The **API Health** tab probes every system and shows exactly what is reachable — use it to
confirm you are on the VPN before generating/validating. No code change can bypass a Cloudflare
IP block; this is purely a network-reachability requirement.
| POST | `/api/jobs/generate` | Start generation job |
| POST | `/api/jobs/compare` | Start comparison job |
| GET | `/api/jobs`, `/api/jobs/{id}` | Job status, logs, results |

### Unique operations query

When `unique=true` and no filters are set, the backend uses a Mongo aggregation:

1. Sort by `occurredAt` DESC
2. Group by `source.operation`, keep first (latest)
3. Paginate

When any filter is active, `unique` is ignored and all matching documents are returned.

## Pipeline Stages (under the hood)

Three event sources feed the same resolver → Mongo path:

### Simulation (GraphQL)

- NextGen UI mutations/queries via `/graph` and `/graphql`
- Example: `activateFamily` → raw event on `mtconnect-api`
- Postman: `docs/postman/Simulation.postman_collection.json`

### Ingress (Desktop / Plugin)

- POST to `mt-audit-log-resolver-service` Ingress API
- Desktop app events (cache cleared, connectivity, etc.)
- Postman: `docs/postman/Ingress.postman_collection.json`

### Cron (Scheduler)

- Scheduled JSON payloads published to RabbitMQ raw exchange
- License expiry, batch progress, notifications, etc.

### Verification (Source validation)

After enrichment, the validator probes live APIs:

| Source | API | Validates |
|--------|-----|-----------|
| Discovery | `POST /v1/styles`, `GET /v1/variations` | Font catalog data in `subject.enrichedSnapshot` |
| Discovery | `POST /v1/privateTag/{id}` | Private tag fields (`tags[].id`, `name`, `customerId`, …) on updatePrivateTag |
| UMS | `POST /api/v3/customers/{gcid}/profiles` | Actor profile + role |
| UMS / MySQL | `SELECT * FROM user_management.user_invitation WHERE email = ?` | createUserInvitations `invitations[]` (email, status, roleId, customerId) |
| CMS | `GET /api/v2/customers/{gcid}` | Actor customer |

**API Health** tab includes dedicated probes: `ums_invitations` (SQL above) and `typesense_private_tag` (`POST /v1/privateTag/{id}`). Sample email/tag id are taken from staged `createUserInvitations` / `updatePrivateTag` enrich payloads when available.

Postman: `docs/postman/Verification.postman_collection.json`

## Environment Variables

Copy `.env.example`. Key variables:

```bash
MONGO_DB_URL=mongodb://...
MONGO_DB_NAME=AuditLogsPreprod
MONGO_COLLECTION_RAW=raw
MONGO_COLLECTION_ENRICHED=enriched
MONGO_COLLECTION_DLQ=dlq
AUDIT_PROJECT_ROOT=../mt-audit-log-automation
API_DEFAULT_PAGE_SIZE=5
```

Tokens and RabbitMQ settings are inherited from `mt-audit-log-automation/.env`.

## Running Locally

```bash
cp .env.example .env
chmod +x scripts/dev.sh backend/run.sh
./scripts/dev.sh
```

- UI: http://localhost:5174
- API: http://localhost:3200
- OpenAPI: http://localhost:3200/docs

## Theme

The UI supports dark and light themes. Toggle via the sidebar button; preference is stored in `localStorage` (`audit-theme`).

## Related Documentation

| Document | Description |
|----------|-------------|
| [E2E_ACTIVATE_FAMILY.md](./E2E_ACTIVATE_FAMILY.md) | Step-by-step walkthrough for `activateFamily` |
| [postman/README.md](./postman/README.md) | Postman collection import guide |
| [../mt-audit-log-automation/README.md](../mt-audit-log-automation/README.md) | Validator project docs |
