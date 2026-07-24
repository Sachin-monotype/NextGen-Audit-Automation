# Local-only tooling (not pushed to GitHub)

Scripts in `local/scripts/` are **maintenance and one-off generators** — TestRail push,
export pack builders, touchpoint sheet generators, etc. They are listed in `.gitignore`
so credentials and ad-hoc workflows stay on your machine.

## Core scripts (tracked in `scripts/`)

| Script | Purpose |
|--------|---------|
| `scripts/dev.sh` | Start FastAPI + Vite dev servers |
| `scripts/ingest.sh` | Run RabbitMQ → Mongo ingestion standalone |
| `scripts/playwright.sh` | Playwright UI pack wrapper |

## Local scripts (`local/scripts/`)

Run from repo root:

```bash
PYTHONPATH=python backend/.venv/bin/python local/scripts/<script>.py [args]
```

### TestRail

| Script | Purpose |
|--------|---------|
| `push_export_testrail_cases.py` | Push export TestRail packs (`--pack batch1\|batch2`, `--merge-map`) |
| `build_export_testrail_pack.py` | Build original 9 export cases + GQL samples |
| `build_export_batch2_testrail_pack.py` | Build batch-2 export cases (reporting, users, teams, …) |
| `sync_fdc14091_testrail_steps.py` | Sync TestRail steps from UI recipes |

### Compare / reports

| Script | Purpose |
|--------|---------|
| `refresh_stored_comparisons.py` | Re-run Compare for stored Result-tab operations |
| `export_ui_test_cases.py` | CSV from export UI catalog |
| `export_dual_run_catalog.py` | Dual-run catalog CSV |

### Catalog / mapping generators

| Script | Purpose |
|--------|---------|
| `sync_export_catalog.py` | Sync export mutations into GQL docs + routing |
| `build_touchpoint_modules.py` | Generate touchpoint module map |
| `build_touchpoint_sheet.py` | Excel touchpoint sheet (output gitignored) |
| `build_touchpoint_postman.py` | Postman collection (output gitignored) |
| `build_event_trigger_sheet.py` | `docs/mappings/event_trigger_sheet.csv` |
| `build_ui_navigation.py` | UI navigation JSON |
| `build_ingress_testcases.py` | Ingress testcase pack |
| `generate_enrichment_scope_manifest.py` | Enrichment scope manifest from resolver repo |
| `verify_touchpoint_live.py` | Live touchpoint payload smoke |
| `test_touchpoint_payloads.py` | Payload shape tests |

Requires `.env` with `TESTRAIL_URL`, `TESTRAIL_USERNAME`, `TESTRAIL_API_KEY` for TestRail scripts.
