# NextGen Audit Automation

End-to-end UI for audit event **generation**, Mongo **raw/enrich** inspection, **source comparison**, and **results** — with optional **Generate in UI** via CasePilot for touchpoint coverage.

## Architecture

| Layer | Stack | Role |
|-------|-------|------|
| **Frontend** | React 19 + Vite | Generate, Enrich/raw, Compare, Result |
| **Backend** | FastAPI + pymongo | Mongo queries, job runner wrapping `audit_validator` |
| **Validator** | `python/audit_validator/` (vendored) | Simulation, E2E, source validation, CasePilot handoff |

## Quick start

```bash
cp .env.example .env
# Set MONGO_DB_URL, GraphQL/token settings, and (optional) CASEPILOT_* vars

python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt
backend/.venv/bin/pip install -r python/requirements.txt

chmod +x backend/run.sh scripts/dev.sh
./scripts/dev.sh
```

- UI: http://localhost:5174  
- API: http://localhost:3200  
- Health: http://localhost:3200/health  

## Sections

### Generate
- **Generate** / **Generate & validate** — GraphQL (or flow) triggers, capture raw/enriched, optional source validation.
- **Generate in UI** — send selected scenarios to CasePilot; auto-capture `correlation-id` and open Generation Status with raw + enrich. UI runs are labeled with an `(ui)` suffix (e.g. `activateFamily(global)(ui)`).

### Enrich/raw
Browse enriched (default), raw, or DLQ from Mongo with sticky filters and pagination.

### Compare
Source-validate enriched fields against GraphQL **trigger/curl** context, JWT, UMS, CMS, Discovery — not only the raw envelope.

### Result
Grouped or table view of compare outcomes; Excel export available.

## CasePilot (Generate in UI)

Requires `CASEPILOT_API_KEY` and UI credentials in `.env`. See the full technical write-up:

→ **[docs/CASEPILOT_INTEGRATION.md](docs/CASEPILOT_INTEGRATION.md)**

TestRail pack for touchpoints: sibling repo `qa_agent/output/test_cases/FDC-14091.json` ([Jira FDC-14091](https://monotype.atlassian.net/browse/FDC-14091)).

## Repository layout

| Path | Purpose |
|------|---------|
| `frontend/` | React app |
| `backend/` | FastAPI |
| `python/audit_validator/` | Core validator + CasePilot client |
| `docs/` | **Pushable** technical docs (CasePilot, E2E, mappings README, …) |
| `temp/` | Local scratch notes (gitignored except `.gitkeep`) |
| `payload/` | Runtime raw/enrich/trigger artifacts (gitignored JSON) |
| `reports/` | Local run reports (gitignored) |

Keep one-off investigation notes in `temp/`, not under `docs/`.

## Documentation

| Document | Description |
|----------|-------------|
| [docs/CASEPILOT_INTEGRATION.md](docs/CASEPILOT_INTEGRATION.md) | CasePilot MCP flow, correlation, APIs |
| [docs/TECHNICAL.md](docs/TECHNICAL.md) | Architecture, API, pipeline overview |
| [docs/E2E_ACTIVATE_FAMILY.md](docs/E2E_ACTIVATE_FAMILY.md) | End-to-end activateFamily walkthrough |
| [docs/mappings/README.md](docs/mappings/README.md) | Field-mapping workbook conventions (trigger/curl source) |

Excel / Postman / generated touchpoint packs stay local (see `.gitignore`).

## Environment (high level)

| Variable | Purpose |
|----------|---------|
| `MONGO_DB_URL` | Same cluster as audit-sense |
| `AUDIT_PROJECT_ROOT` | Defaults to this repo |
| GraphQL / Bearer / RabbitMQ | See `.env.example` |
| `CASEPILOT_API_KEY` + UI login vars | Generate in UI |

## API (selected)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Mongo + project root |
| GET | `/api/raw\|enriched\|dlq` | Filtered logs |
| POST | `/api/jobs/generate` | GraphQL generate job |
| POST | `/api/jobs/generate-ui` | CasePilot UI handoff (`dispatch` optional) |
| GET | `/api/meta/casepilot` | CasePilot MCP health |
| POST | `/api/jobs/compare` | Source comparison job |
| GET | `/api/jobs/{id}` | Job status + logs |

## Branching

Feature work that touches CasePilot / Generate-in-UI should land on a dedicated branch (e.g. `CasePilot-Integration`) and merge to `main` only after smoke verification — avoid breaking the default Generate path.
