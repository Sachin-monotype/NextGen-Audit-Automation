import type { ComparisonRow, LatestComparisonItem } from "../api";

export type ScenarioValueRow = {
  field_path: string;
  values: Record<string, string>;
  same: boolean;
};

export type ScenarioStructureRow = {
  path: string;
  presence: Record<string, boolean>;
};

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** Flatten enriched JSON into dot/bracket paths (leaf scalars only). */
export function flattenJsonPaths(value: unknown, prefix = ""): string[] {
  if (value === null || value === undefined) {
    return prefix ? [prefix] : [];
  }
  if (Array.isArray(value)) {
    if (!value.length) return prefix ? [prefix] : [];
    const out: string[] = [];
    value.forEach((item, i) => {
        const key = prefix ? `${prefix}[${i}]` : `[${i}]`;
        out.push(...flattenJsonPaths(item, key));
      });
    return out;
  }
  if (isPlainObject(value)) {
    const keys = Object.keys(value);
    if (!keys.length) return prefix ? [prefix] : [];
    const out: string[] = [];
    for (const k of keys) {
      const key = prefix ? `${prefix}.${k}` : k;
      out.push(...flattenJsonPaths(value[k], key));
    }
    return out;
  }
  return prefix ? [prefix] : [];
}

export function compareScenarioValues(
  operations: string[],
  items: LatestComparisonItem[],
): ScenarioValueRow[] {
  const byOp = new Map<string, ComparisonRow[]>();
  for (const item of items) {
    if (operations.includes(item.operation)) {
      byOp.set(item.operation, item.rows);
    }
  }
  const paths = new Set<string>();
  for (const rows of byOp.values()) {
    for (const r of rows) paths.add(r.field_path);
  }
  return [...paths]
    .sort((a, b) => a.localeCompare(b))
    .map((field_path) => {
      const values: Record<string, string> = {};
      for (const op of operations) {
        const row = byOp.get(op)?.find((r) => r.field_path === field_path);
        values[op] = row?.value_in_enriched?.trim() ? row.value_in_enriched : "—";
      }
      const uniq = new Set(operations.map((op) => values[op]));
      return { field_path, values, same: uniq.size <= 1 };
    });
}

export function compareScenarioStructure(
  operations: string[],
  enrichedByOp: Record<string, unknown>,
): ScenarioStructureRow[] {
  const pathSets = new Map<string, Set<string>>();
  for (const op of operations) {
    const json = enrichedByOp[op];
    pathSets.set(op, new Set(json ? flattenJsonPaths(json) : []));
  }
  const allPaths = new Set<string>();
  for (const s of pathSets.values()) {
    for (const p of s) allPaths.add(p);
  }
  return [...allPaths]
    .sort((a, b) => a.localeCompare(b))
    .map((path) => {
      const presence: Record<string, boolean> = {};
      for (const op of operations) {
        presence[op] = pathSets.get(op)?.has(path) ?? false;
      }
      return { path, presence };
    });
}

export function structureDiffSummary(rows: ScenarioStructureRow[]): {
  onlyFirst: number;
  onlySecond: number;
  both: number;
  divergent: number;
} {
  let onlyFirst = 0;
  let onlySecond = 0;
  let both = 0;
  let divergent = 0;
  for (const r of rows) {
    const present = Object.values(r.presence).filter(Boolean).length;
    if (present === 0) continue;
    if (present === 1) {
      const first = Object.entries(r.presence).find(([, v]) => v)?.[0];
      if (first === Object.keys(r.presence)[0]) onlyFirst += 1;
      else onlySecond += 1;
    } else if (present === Object.keys(r.presence).length) {
      both += 1;
    } else {
      divergent += 1;
    }
  }
  return { onlyFirst, onlySecond, both, divergent };
}
