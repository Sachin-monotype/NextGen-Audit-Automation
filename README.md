# NextGen Audit Automation

End-to-end UI for audit log generation, display, source comparison, and results — built on top of [mt-audit-log-automation](../mt-audit-log-automation) (Python) and MongoDB (from [audit-sense](../audit-sense)).

## Architecture

| Layer | Stack | Role |
|-------|-------|------|
| **Frontend** | React 19 + Vite | 4 sections: Generate, Enrich/raw, Compare, Result |
| **Backend** | FastAPI + pymongo | Mongo queries, job runner wrapping `audit_validator` |
| **Validator** | `python/audit_validator/` (vendored) | Simulation, E2E, source validation |

## Quick start

```bash
cp .env.example .env
# Edit MONGO_DB_URL and AUDIT_PROJECT_ROOT if needed

python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt
backend/.venv/bin/pip install -r python/requirements.txt

chmod +x backend/run.sh scripts/dev.sh
./scripts/dev.sh
```

`backend/run.sh` also installs both requirement files automatically. Run the
commands above explicitly when preparing a virtual environment without using
the development script.

- UI: http://localhost:5174
- API: http://localhost:3200
- Health: http://localhost:3200/health

## Sections

### Generate
Select operations (or leave empty for all). Two actions:
- **Generate** — trigger events, capture raw/enriched via RabbitMQ
- **Generate & validate** — full pipeline including UMS/CMS/Discovery source validation

Live logs show queue names, environment, capture progress, and Mongo verification.

### Enrich/raw collection
Browse **enriched** (default), **raw**, or **DLQ** from Mongo:
- Sticky filter + pagination bar (stays visible while scrolling)
- Page size: 20 / 50 / 100 / 200
- Default: **latest entry per unique operation**
- When filtered: all matching entries

### Compare
Select operations that exist in both raw and enriched collections. Runs Python source validation against Typesense, UMS, CMS.

### Result
Grouped card view or compact table view (`$.field.path` | enriched | source). Excel download included.

## Self-contained repo

The `python/audit_validator/` package is vendored in this repo — no sibling `mt-audit-log-automation` checkout required. Copy `.env.example` to `.env` and fill tokens/RabbitMQ/Mongo settings.

## Documentation

| Document | Description |
|----------|-------------|
| [docs/TECHNICAL.md](docs/TECHNICAL.md) | Architecture, API, pipeline overview |
| [docs/E2E_ACTIVATE_FAMILY.md](docs/E2E_ACTIVATE_FAMILY.md) | End-to-end walkthrough for activateFamily |
| [docs/postman/](docs/postman/) | Postman collections (Simulation, Ingress, Verification) |

## Environment

Copy `.env.example`. Key variables:

- `MONGO_DB_URL` — same cluster as audit-sense
- `AUDIT_PROJECT_ROOT` — defaults to `.` (this repo)
Tokens / RabbitMQ — set in this repo's `.env` (see `.env.example`)

## API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Mongo + project root status |
| GET | `/api/raw\|enriched\|dlq` | Filtered logs (limit 5 default) |
| GET | `/api/meta/comparable-operations` | Ops in both collections |
| POST | `/api/jobs/generate` | Start generation job |
| POST | `/api/jobs/compare` | Start comparison job |
| GET | `/api/jobs/{id}` | Job status + logs + results |
