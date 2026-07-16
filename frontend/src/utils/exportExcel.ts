import type { ComparisonRow } from "../api";

const HEADERS = [
  "event",
  "section",
  "branch",
  "field",
  "node/subnode",
  "enriched_json_path",
  "value_in_enriched_json",
  "value_in_source_json",
  "source",
  "status",
  "remark",
  "routing_key",
] as const;

const ENVELOPE_ORDER = [
  "event",
  "source",
  "subject",
  "subject.enrichedSnapshot",
  "actor",
  "actor.enrichedSnapshot",
] as const;

function envelopeKey(path: string): (typeof ENVELOPE_ORDER)[number] {
  if (path.startsWith("actor.enrichedSnapshot.")) return "actor.enrichedSnapshot";
  if (path.startsWith("actor.")) return "actor";
  if (path.startsWith("subject.enrichedSnapshot.")) return "subject.enrichedSnapshot";
  if (path.startsWith("subject.")) return "subject";
  if (path.startsWith("source.")) return "source";
  return "event";
}

function snapshotBranch(path: string): string {
  for (const p of ["subject.enrichedSnapshot.", "actor.enrichedSnapshot."] as const) {
    if (path.startsWith(p)) {
      const rest = path.slice(p.length);
      const m = rest.match(/^([^.]+(?:\[\d+\])?)/);
      return m?.[1] || rest;
    }
  }
  return "";
}

function enrichPathSortKey(path: string): string {
  const section = ENVELOPE_ORDER.indexOf(envelopeKey(path));
  return `${String(section).padStart(2, "0")}|${snapshotBranch(path)}|${path}`;
}

function nodeSubnode(row: ComparisonRow): string {
  const parts = [row.node, row.sub_node].filter(Boolean);
  if (parts.length) return parts.join(" / ");
  if (row.field_path.startsWith("subject.enrichedSnapshot.")) {
    return row.field_path.slice("subject.enrichedSnapshot.".length);
  }
  if (row.field_path.startsWith("actor.enrichedSnapshot.")) {
    return row.field_path.slice("actor.enrichedSnapshot.".length);
  }
  return row.field_path;
}

function displayField(row: ComparisonRow): string {
  return row.field || row.field_path.split(".").pop() || row.field_path;
}

function escapeCell(value: string): string {
  const escaped = value.replace(/"/g, '""');
  return `"${escaped}"`;
}

export function downloadComparisonExcel(rows: ComparisonRow[]) {
  const grouped = new Map<string, ComparisonRow[]>();
  for (const row of rows) {
    const list = grouped.get(row.operation) ?? [];
    list.push(row);
    grouped.set(row.operation, list);
  }

  const lines: string[] = [HEADERS.join(",")];
  const operations = [...grouped.keys()].sort();

  for (let i = 0; i < operations.length; i++) {
    const op = operations[i];
    const opRows = [...(grouped.get(op) ?? [])].sort((a, b) =>
      enrichPathSortKey(a.field_path).localeCompare(enrichPathSortKey(b.field_path))
    );
    const routingKey = opRows[0]?.routing_key ?? "";

    for (const row of opRows) {
      lines.push(
        [
          op,
          envelopeKey(row.field_path),
          snapshotBranch(row.field_path),
          displayField(row),
          nodeSubnode(row),
          `$.${row.field_path}`,
          row.value_in_enriched,
          row.value_in_source,
          row.source_system,
          row.match_status,
          row.notes,
          routingKey,
        ]
          .map((v) => escapeCell(v ?? ""))
          .join(",")
      );
    }

    if (i < operations.length - 1) {
      lines.push(Array(HEADERS.length).fill('""').join(","));
    }
  }

  const blob = new Blob(["\uFEFF" + lines.join("\n")], {
    type: "text/csv;charset=utf-8;",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `source-comparison-${new Date().toISOString().slice(0, 10)}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}
