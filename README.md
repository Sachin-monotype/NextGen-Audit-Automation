# NextGen Audit Automation

Generate audit events (GraphQL / UI via CasePilot), land them in Mongo (raw + enrich), compare enriched fields to sources, and review results — in one local app.

## Prerequisites

- Python 3.11+
- Node.js 20+
- Access to the Monotype Mongo cluster (or local Mongo)
- Corporate VPN for RabbitMQ / GraphQL when required
- Optional: `CASEPILOT_API_KEY` for **Generate in UI**

## Clone and run (minimum)

```bash
git clone <repo-url>
cd "NextGen-Audit Automation"
git checkout CasePilot-Integration   # or main after merge

cp .env.example .env
# Required at minimum:
#   MONGO_DB_URL=...
#   OAUTH_USERNAME / OAUTH_PASSWORD  (or NEXTGEN_BEARER_TOKEN)
# Optional for CasePilot:
#   CASEPILOT_API_KEY=cp_api_...

python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt
backend/.venv/bin/pip install -r python/requirements.txt

cd frontend && npm install && cd ..
chmod +x scripts/dev.sh backend/run.sh
./scripts/dev.sh
```

- UI: http://localhost:5174  
- API: http://localhost:3200/health  

That is enough to open the app, switch environment, and run Generate.

## Environments (PP / QA / UAT)

Use the **Environment** dropdown on Generate (also updates `.env` `AUDIT_TARGET`):

| Target | NextGen UI | Mongo DB (auto) |
|--------|------------|-----------------|
| **PP** | https://nextgen.monotype-pp.com | `AuditLogsPreprod` |
| **QA** | https://nextgen-qa.monotype-pp.com | `AuditLogsQA` |
| **UAT** | https://nextgen.monotype-uat.com | `AuditLogsUAT` |

### UAT local setup (teammate checklist)

Someone else testing **UAT** needs more than QA/PP because audit logs stay on **local Mongo**, AMS/CMS source truth uses **MySQL over SSH**, and Discovery/UMS need a **UAT user JWT**.

1. **Clone + `.env`**
   - Copy `.env.example` → `.env`
   - Set `AUDIT_TARGET=uat` and `INGEST_TARGETS=uat`
   - Set `MONGO_DB_URL_UAT=mongodb://localhost:27017`

2. **UAT user JWT + login** (from a logged-in NextGen UAT session, or shared test user)
   - `OAUTH_USERNAME` / `OAUTH_PASSWORD` (e.g. UAT test account)
   - Paste the same user JWT into `BEARER_TOKEN`, `NEXTGEN_BEARER_TOKEN`, and `DISCOVERY_BEARER_TOKEN` (no `Bearer ` prefix)
   - `GRAPHQL_CONTEXT_CUSTOMER_ID` / `OAUTH_GCID` = gcid claim from that JWT
   - `DISCOVERY_BASE_URL=https://nextgen.monotype-uat.com/api/search`
   - `UMS_BASE_URL=https://usermanagement.monotype-uat.com` (or `UMS_BASE_URL_UAT=…`)
   - `SOURCE_TRUTH=db` and `SOURCE_TRUTH_UMS=api` (UAT MySQL has no `user_management`)

3. **Local Mongo (raw/enrich)**
   ```bash
   ./scripts/start-audit-mongo.sh
   ```

4. **UAT MySQL tunnel (AMS/CMS, read-only)** — needs bastion SSH access + MySQL password
   ```bash
   # Requires ~/.ssh/id_rsa authorized on bastion (ask team for key access)
   ./scripts/start-uat-mysql-tunnel.sh
   ```
   Then in `.env`:
   ```bash
   MYSQL_HOST_UAT=127.0.0.1
   MYSQL_PORT_UAT=13306
   MYSQL_USER_UAT=Uba_uat
   MYSQL_PASSWORD_UAT=<from Workbench / team>
   MYSQL_SSL_UAT=true
   ```

5. **RabbitMQ / Results (optional but typical)**
   - `RABBITMQ_URL_UAT` / `INGRESS_RABBITMQ_URL_UAT` (vhost `mt-connect`)
   - `RESULTS_MONGO_URL_UAT` (+ db/collection) for the Results tab

6. **Run**
   ```bash
   ./scripts/dev.sh
   # UI http://localhost:5174  API http://localhost:3200
   ```

**Enrich/raw without queue ingest:** search by correlation id — if local Mongo misses, the API falls back to resolver `…/v1/payload-dumps` (`PAYLOAD_DUMPS_FALLBACK=true`).

Switching target:

1. Rewrites NextGen / GraphQL URLs for that env  
2. Points Mongo at the DB above — **creates the DB + `raw` / `enriched` / `dlq` collections on first use** (indexes included)  
3. Rebuilds RabbitMQ ingestion against the configured queues  

Edit **User / queues** in the same bar to change OAuth credentials (token) or raw/enrich queue names without leaving the UI.

## Main workflows

| Action | Where |
|--------|--------|
| Generate / Generate & validate | Generate → select ops (left) → filters (right) |
| Generate in UI (CasePilot) | Select scenarios → Generate in UI → per-row TestRail ids (FDC-14091: C73303503…) |
| Generation Status | Export selected rows, Compare selected PASS → Result, Diff enrich JSON |
| Browse Mongo | Enrich/raw |
| Source compare | Compare tab, or from Generation Status |
| Connectivity | API Health |

UI-triggered scenarios show as `operation(touch)(ui)` in Generation Status.

## TestRail map (FDC-14091)

Synced from [suite 22395](https://type.testrail.com/index.php?/suites/view/22395&group_by=cases:section_id&group_order=asc&display_deleted_cases=0&group_id=4066542) — cases **C73303503–C73303610** (108).

Map file: `python/audit_validator/data/fdc14091_testrail_map.json`  
API: `GET /api/meta/ui-testrail-map`

Jira: [FDC-14091](https://monotype.atlassian.net/browse/FDC-14091)

## Docs (pushable)

| Doc | Topic |
|-----|--------|
| [docs/PROJECT_OVERVIEW.md](docs/PROJECT_OVERVIEW.md) | Directory layout + high-level data flow |
| [docs/CASEPILOT_INTEGRATION.md](docs/CASEPILOT_INTEGRATION.md) | CasePilot MCP, correlation-id, APIs |
| [docs/TECHNICAL.md](docs/TECHNICAL.md) | Architecture |
| [docs/mappings/README.md](docs/mappings/README.md) | Field mapping / trigger-as-source |

Scratch notes → `temp/` (gitignored). Do not put one-off notes in `docs/`.

## Local-only scripts

Maintenance tools (TestRail push, export pack builders, touchpoint generators) live in
**`local/scripts/`** and are **gitignored** — they stay on your machine. See
[local/README.md](local/README.md). Tracked dev scripts: `scripts/dev.sh`, `ingest.sh`, `playwright.sh`.

## Key `.env` variables

| Variable | Purpose |
|----------|---------|
| `MONGO_DB_URL` | Cluster connection string |
| `MONGO_DB_NAME` | Overridden by env profile on target switch |
| `AUDIT_TARGET` | `pp` \| `qa` \| `uat` |
| `OAUTH_USERNAME` / `OAUTH_PASSWORD` | Token for GraphQL |
| `RABBITMQ_URL` | Broker (vhost switched by profile) |
| `CASEPILOT_API_KEY` | Generate in UI |
| `CASEPILOT_UI_*` | Optional overrides for CasePilot browser login |

See `.env.example` for the full list.

## API (selected)

| Method | Path |
|--------|------|
| GET | `/health` |
| POST | `/api/meta/pipeline-target` `{ "target": "qa" }` |
| GET | `/api/meta/ui-testrail-map` |
| POST | `/api/jobs/generate` |
| POST | `/api/jobs/generate-ui` |
| POST | `/api/jobs/compare` |

## Playwright UI pack (CasePilot alternative)

Fast local UI triggers for `activateFamily` scenarios — see [`playwright-ui/README.md`](playwright-ui/README.md).

```bash
npm run playwright:install   # first time: deps + Chromium
npm run playwright           # 5 activateFamily scenarios + Mongo verify
```

Do **not** run `npx playwright test` from the repo root; use the commands above or `cd playwright-ui && ./run.sh`.

## Branch

Active CasePilot / multi-env work lives on **`CasePilot-Integration`**. Merge to `main` only after a smoke Generate on PP.
