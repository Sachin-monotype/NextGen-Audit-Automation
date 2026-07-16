# Field mapping workbooks (Results-parity)

One Excel file **per notification category**. Each operation sheet lists every
enrich-JSON leaf (same set as the Results **List** view).

## Columns

| Column | Example |
|--------|---------|
| `#` | Row number |
| Enriched JSON path | `actor.enrichedSnapshot.customer.subscription.isTrial` |
| Section | `actor.enrichedSnapshot` |
| Source | `cms>customer_subscription>is_trial` |
| Query | SQL or Discovery `curl` (once per unique source); later rows → `↻ same as above` |
| Transformation | `MySQL TINYINT 0/1 → JSON bool` |

**Source** form: **`service>table>column`**. Never `unknown` / bare `resolver`.

| Prefix | Meaning |
|--------|---------|
| `cms>` | MySQL `customer_management.*` |
| `ums>` | MySQL `user_management.*` |
| `ams>` | MySQL `asset_management.*` |
| `typesense>` | Discovery HTTP as copy-paste `curl` |
| `raw>` | Producer audit envelope — Query left **blank** |
| `jwt>` | Bearer claims — Query left **blank** |
| `audit-service>` | Enricher-derived — Query left **blank** |

Within each sheet, the first row for a given source (e.g. all `cms>customers>*`) gets
the full SQL/curl; following rows show **`↻ same as above`**.

## Regenerate

```bash
PYTHONPATH=python backend/.venv/bin/python -m audit_validator.source_validation.export_field_mappings \
  --out docs/mappings
```

## TouchPoint × unique GraphQL inputs

**Preferred (module-split):** [`docs/touchpoint/`](../touchpoint/) — **189** unique ops across
activation / library / projects / favourites / imported_fonts / documents / tags /
teams_orgs / notifications / sharing / ingress / cron.

```bash
PYTHONPATH=python backend/.venv/bin/python scripts/build_touchpoint_modules.py
PYTHONPATH=python backend/.venv/bin/python scripts/verify_touchpoint_live.py
```

Legacy monolithic workbook (still refreshed by older builder):

**`TouchPoint.xlsx`** — the QA sheet for GQL mutations across Discovery / List /
Favourite / Project / Project>List / Document. Built from:

- `audit-event-context-matrix.xlsx` (Matrix tab context flags)
- Your TouchPoint examples (real `activateFamily` input shapes)
- `mtf-graphql-schema` + `mtconnect-api` list-scope routing

| Sheet | Purpose |
|-------|---------|
| `Touch Points` | Event · TouchPoint · step 1–5 curls (same layout as Downloads/TouchPoint.xlsx) |
| `UniqueInputShapes` | One row per distinct `variables.input` shape (`listIds` / `listType` / `projectId` …) |
| `TouchPoints` | Same curls + matrix flags + enrich notes |
| `AutomationFlows` | Compact `create → seed → trigger` sequence for dynamic generate |
| `Gaps` | Matrix ops not yet in our GraphQL catalog |

Each step cell is titled then curl, e.g. `Create List :` / `Activate family :`.
The builder embeds your current `.env` `BEARER_TOKEN`, real `SEED_FAMILY_ID` /
`SEED_STYLE_ID`, and freshly provisioned list/project IDs so curls paste into
Postman. Rebuild when the token expires.

```bash
PYTHONPATH=python backend/.venv/bin/python scripts/build_touchpoint_sheet.py
PYTHONPATH=python backend/.venv/bin/python scripts/build_touchpoint_postman.py
PYTHONPATH=python backend/.venv/bin/python scripts/test_touchpoint_payloads.py
```

Also refreshes `event_trigger_sheet.csv` and `trigger_sequences.json`.

**`TouchPoint.postman_collection.json`** — same multi-step flows for Postman:

| Folder | Steps |
|--------|--------|
| `addFontListFamilies / List` | Create List → Add family (`fontListId` + `families.familyIds`) |
| `activateFamily / List` | Create List → Add family → Activate |
| `activateFamily / Project` | Create Project → Add family → Activate |
| … | Favourite / Project>List / activateList |

Import into Postman, set `bearerToken` if expired, run folder top→bottom. Create-list **Tests** write `listId` / `projectId`; Add-family has a **Pre-request** guard if you skip Create.

## Event trigger CSV (legacy)

`event_trigger_sheet.csv` — lighter CSV export of Event × TouchPoint × step ops.

```bash
python3 scripts/build_event_trigger_sheet.py   # older nav-only builder
# prefer build_touchpoint_sheet.py for input-shape accuracy
```
