# CasePilot Integration

How **Generate in UI** drives NextGen UI via CasePilot MCP, captures `correlation-id`, and lands raw + enriched events for Generation Status / Compare.

Jira: [FDC-14091](https://monotype.atlassian.net/browse/FDC-14091)

## How CasePilot gets its instructions

CasePilot loads TestRail case **ids**, but this app sends **authoritative step text** via MCP context:

- Recipes: `python/audit_validator/ui_case_recipes.py` (mtconnect-ui routes + `data-qa-id`s)
- Built into each job by `ui_trigger._build_context`
- Hint `prefer_steps=context_over_testrail` — ignore conflicting TestRail prose
- **One CasePilot run per selected scenario** (`dispatch_mode=one_case_per_run`) so multi-select does not mash 7 recipes into one wandering session

Seeds are dynamic (`SEED_FAMILY_ID` / any visible deactivated family) — recipes avoid hard-requiring a single family name or always opening family detail (except style/variation).

TestRail map: `data/fdc14091_testrail_map.json` (all GraphQL Generate scenarios mapped).


## Architecture

```
Generate catalog (selection)
        │
        ▼
POST /api/jobs/generate-ui  ──►  ui_trigger.create_ui_trigger_job
        │                              │
        │                              ├─ planned UI steps (fast path)
        │                              ├─ TestRail case ids (per scenario)
        │                              └─ CasePilot MCP run_testrail_ui_tests
        ▼
CasePilot connector (Chrome) runs NextGen UI
        │
        ├─ mutations emit response header correlation-id
        ├─ agent notes AUDIT_RESULT|operation=…|correlation_id=…|touchpoint=…
        ▼
refresh / auto-finalize
        │
        ├─ extract AUDIT_RESULT (reject <op>/<touch> placeholders)
        ├─ record_generation(kind=ui) + display name …(ui)
        └─ verify Mongo raw + enrich → Generation Status
```

## Configuration

| Variable | Role |
|----------|------|
| `CASEPILOT_API_KEY` | Bearer for `https://casepilot.monotype-pp.com/mcp` |
| `CASEPILOT_UI_BASE_URL` | NextGen PP URL |
| `CASEPILOT_UI_USERNAME` / `PASSWORD` or OAuth vars | Login for the UI connector |

See `.env.example`. Do not commit secrets.

## Correlation (critical)

- Use response header **`correlation-id`**.
- Do **not** use `x-correlation-id` (Cloudflare rewrite).
- Agent must emit one line per mutation:

```text
AUDIT_RESULT|operation=activateFamily|correlation_id=7a4f9f30-f35b-400c-89af-3cc21b15c51a|touchpoint=global
```

Never leave angle-bracket templates (`<op>`, `<touch>`, `<uuid>`) — those become junk rows like `<op>(<touch)` in Generation Status and are filtered out.

## Fast UI path

Planned steps (`ui_steps_for_selection`) prefer:

1. Search seed families `910042901` / `910011880`
2. Deactivate if already activated
3. Perform the scoped action
4. Emit `AUDIT_RESULT` and close the browser

**Project > List** is explicit: create/open project → add family → create list **inside** the project → add family to that list → activate so the mutation carries **both** `projectId` and `listIds`. It must not collapse to list-only activate.

## Naming: `(ui)` suffix

UI-triggered scenarios appear as `activateFamily(global)(ui)` in Generation Status, raw/enrich viewers, and enrich compare — so they stay distinct from API generate runs of the same operation.

## APIs

| Method | Path | Notes |
|--------|------|-------|
| `GET` | `/api/meta/casepilot` | MCP health / connector online |
| `POST` | `/api/jobs/generate-ui` | Create handoff; `dispatch: true` sends immediately |
| `POST` | `/api/jobs/generate-ui/{id}/send` | Queue CasePilot |
| `POST` | `/api/jobs/generate-ui/{id}/refresh` | Poll + auto-verify when ready |
| `POST` | `/api/jobs/generate-ui/{id}/results` | Paste correlation ids manually |
| `POST` | `/api/jobs/generate-ui/{id}/verify` | Force Generation Status write |

Selection items may include per-scenario `test_case_id` and `notes`.

## TestRail map

Canonical smoke + touchpoint pack is **FDC-14091** ([suite section](https://type.testrail.com/index.php?/suites/view/22395&group_by=cases:section_id&group_order=asc&display_deleted_cases=0&group_id=4066542)):

| Range | Count |
|-------|-------|
| C73303503 – C73303610 | 108 |

Stored in `python/audit_validator/data/fdc14091_testrail_map.json`. Generate-in-UI resolves selection → case id automatically.

Jira: [FDC-14091](https://monotype.atlassian.net/browse/FDC-14091)

## Code map

| Module | Role |
|--------|------|
| `python/audit_validator/casepilot_mcp.py` | Streamable HTTP/SSE MCP client |
| `python/audit_validator/ui_trigger.py` | Handoff, steps, extract, verify |
| `python/audit_validator/ui_testrail_map.py` | Scenario → TestRail id |
| `frontend/src/components/GenerateInUiModal.tsx` | Per-event TestRail + details UI |
| `frontend/src/pages/GeneratePage.tsx` | Generation Status + polling |

## Source compare vs trigger curl

Compare prefers **GraphQL curl / event trigger** context (`payload/trigger/`, live mutation input+response) over raw envelope for join keys. See [docs/mappings/README.md](mappings/README.md).

## Operational notes

- CasePilot connectors are often **single-flight** (`job_busy`). Prefer smaller batches.
- Refresh the Generate-in-UI log until auto-verify writes Generation Status.
- Scratch / one-off notes belong under `temp/` (gitignored), not `docs/`.
