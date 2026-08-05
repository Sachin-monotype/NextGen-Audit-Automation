# Source validation architecture

Compare logic is split so ~200 audit operations stay maintainable without one monolithic file.

## Layers

| Layer | Role |
|-------|------|
| `mapping_registry.py` | Default field templates by enricher category (asset, font, role, actor envelope). |
| `audit_events_registry.py` | Resolver-aligned event specs from the QA workbook. |
| `operation_rules/` | **Surgical overrides** when an operation diverges from its category template. |
| `comparison_rows.py` | Generic enriched-first compare engine (UMS/CMS/AMS/Typesense/trigger). |
| `runner.py` | Live source prefetch (identity cache, Discovery, trigger replay). |

## When to add an operation rule

Add to `operation_rules/registry.py` (or a small sibling module) when:

- Actor identity uses a different UMS `userType` (e.g. service accounts).
- Subject ids echo from GraphQL input/response instead of an external API (`getPackageId`).
- AMS asset type must be inferred (`WebProject` vs default `Folder`).
- Envelope fields must prefer the **published** event over live replay (`xCorrelationId`).

Do **not** duplicate full field lists per operation — keep category templates and override only the exceptions.

## Adding a new operation

1. Confirm enricher in `mt-audit-log-resolver-service` (HTTP sources only today).
2. Ensure category exists in `mapping_registry._mapping_for_event_spec` or extend `_ASSET_CATEGORIES` / workbook row.
3. Add an `operation_rules` hook only if the generic path is wrong.
4. Add UI recipe + TestRail case in a **new batch JSON** (never rewrite existing case ids).

## External sources probed today

- **UMS** — profiles (POST-as-GET), roles, teams, users-by-idp; MySQL for invitations.
- **CMS** — `GET /api/v2/customers/{gcid}` (includes `metaData`).
- **AMS** — typed GET + bulk assets.
- **Discovery/Typesense** — styles, variations, private tags.
- **Trigger** — captured GraphQL input/response (not Raw Mongo echo).

No resolver DB queries exist — only add MySQL when the resolver itself reads a table (e.g. invitations).
