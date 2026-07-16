"""Render docs/SIMULATION_TESTING_GUIDE.md from flow catalog + testing sheet metadata."""

from __future__ import annotations

import logging
from pathlib import Path

from .testing_sheet import build_simulation_rows

log = logging.getLogger(__name__)


def write_testing_guide_md(
    *,
    path: Path,
    project_root: Path,
    validate_curls: bool = False,
    rows: list | None = None,
) -> None:
    from .testing_sheet import SimulationRow, build_simulation_rows

    rows = rows or build_simulation_rows(project_root, validate_curls=validate_curls)
    by_flow: dict[str, list] = {}
    for row in rows:
        by_flow.setdefault(row.flow, []).append(row)

    lines: list[str] = [
        "# Simulation Testing Guide",
        "",
        "Manual QA reference aligned with Postman collection `MT-Audit-Simulation`.",
        "Regenerate:",
        "",
        "```bash",
        "cd python",
        "python -m audit_validator testing-sheet --guide",
        "```",
        "",
        "## Quick start (curated)",
        "",
        "Use `reports/simulation-testing-working.csv` — cURLs, UI path, enriched checks for:",
        "",
    ]
    from .testing_sheet import _curated_simulations

    curated = _curated_simulations(project_root)
    for name in sorted(curated):
        match = next((r for r in rows if r.simulation_name == name), None)
        if match:
            lines.append(
                f"- **{name}** (`{match.flow}`) — {match.ui_navigation} — UI verify: **{match.ui_verify}**"
            )
    lines.extend(
        [
        "",
        "## How to use",
        "",
        "| Column | Meaning |",
        "|--------|---------|",
        "| **UI flow** | Short path in NextGen (like spreadsheet UI_Navigation) |",
        "| **Enriched snapshot** | What resolver should add beyond raw event |",
        "| **UI verify** | `YES` = check notification bell or visible change; `OPTIONAL` = confirm in UI if time; `NO` = queue/API only |",
        "| **cURL status** | `OK` = GraphQL returned data with current `.env` seeds |",
        "",
        "---",
        "",
        ]
    )

    for flow in sorted(by_flow.keys()):
        lines.append(f"## {flow}")
        lines.append("")
        for row in by_flow[flow]:
            lines.append(f"### {row.simulation_name}")
            lines.append("")
            lines.append(f"- **GraphQL:** `{row.graphql_operation}`")
            lines.append(f"- **Routing key:** `{row.expected_routing_key or 'n/a'}`")
            if row.uses_secondary_token:
                lines.append("- **Auth:** `BEARER_TOKEN_SECONDARY`")
            if row.skipped_by_default:
                lines.append("- **Automation:** skipped by default (`skip=True`)")
            lines.append(f"- **UI flow:** {row.ui_navigation or '(set in config/simulation_testing.json)'}")
            lines.append(f"- **Enriched snapshot:** {row.enriched_snapshot}")
            lines.append(f"- **Verify in UI:** {row.ui_verify}")
            if validate_curls:
                detail = f" — {row.curl_detail}" if row.curl_detail else ""
                lines.append(f"- **cURL:** {row.curl_status}{detail}")
            lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    log.info("Wrote testing guide to %s", path)
