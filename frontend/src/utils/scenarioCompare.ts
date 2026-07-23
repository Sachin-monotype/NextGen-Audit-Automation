import type { ComparisonRow, LatestComparisonItem } from "../api";

export type ScenarioValueRow = {
  field_path: string;
  values: Record<string, string>;
  same: boolean;
};

export type ScenarioStructureRow = {
  path: string;
  presence: Record<string, boolean>;
  values: Record<string, string>;
  kind: "presence" | "value";
};

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** Collapse numeric array indexes so length-only diffs do not spawn [0]/[1] rows. */
export function normalizeStructurePath(path: string): string {
  return path.replace(/\[\d+\]/g, "[*]");
}

/** Equivalent enriched paths (resolver root vs preserved metadata.input). */
const PATH_ALIASES: ReadonlyArray<readonly [string, string]> = [
  ["subject.activationType", "subject.metadata.input.activationType"],
  ["subject.activationMode", "subject.metadata.input.activationMode"],
  ["subject.deactivationType", "subject.metadata.input.deactivationType"],
];

function isScalarLeaf(v: unknown): boolean {
  return v === null || v === undefined || typeof v !== "object";
}

function isScalarArray(arr: unknown[]): boolean {
  return arr.every((x) => isScalarLeaf(x));
}

function serializeScalar(v: unknown): string {
  if (v === null || v === undefined) return "null";
  if (typeof v === "boolean") return v ? "true" : "false";
  return String(v);
}

function serializeScalarArray(arr: unknown[]): string {
  return JSON.stringify(arr.map(serializeScalar));
}

function structureValuesEqual(a: string, b: string, path: string): boolean {
  if (a === b) return true;
  if (/activationtype|activationmode|deactivationtype/i.test(path)) {
    return a.toLowerCase() === b.toLowerCase();
  }
  return false;
}

function uniqueStructureValues(values: string[], path: string): number {
  const uniq: string[] = [];
  for (const v of values) {
    if (!uniq.some((u) => structureValuesEqual(u, v, path))) uniq.push(v);
  }
  return uniq.length;
}

/** Collect normalized path → serialized leaf value (scalar arrays collapsed). */
export function collectStructureEntries(json: unknown): Map<string, string> {
  const out = new Map<string, string>();

  function walk(value: unknown, prefix: string): void {
    if (value === null || value === undefined) {
      if (prefix) out.set(normalizeStructurePath(prefix), "null");
      return;
    }
    if (Array.isArray(value)) {
      if (!value.length) {
        if (prefix) out.set(normalizeStructurePath(prefix), "[]");
        return;
      }
      if (isScalarArray(value)) {
        out.set(normalizeStructurePath(prefix), serializeScalarArray(value));
        return;
      }
      for (let i = 0; i < value.length; i += 1) {
        const seg = prefix ? `${prefix}[${i}]` : `[${i}]`;
        walk(value[i], seg);
      }
      return;
    }
    if (isPlainObject(value)) {
      const keys = Object.keys(value);
      if (!keys.length) {
        if (prefix) out.set(normalizeStructurePath(prefix), "{}");
        return;
      }
      for (const k of keys) {
        const key = prefix ? `${prefix}.${k}` : k;
        walk(value[k], key);
      }
      return;
    }
    if (prefix) out.set(normalizeStructurePath(prefix), serializeScalar(value));
  }

  walk(json, "");
  applyPathAliases(out);
  return out;
}

function applyPathAliases(entries: Map<string, string>): void {
  for (const [a, b] of PATH_ALIASES) {
    const va = entries.get(a);
    const vb = entries.get(b);
    if (va !== undefined && vb === undefined) entries.set(b, va);
    else if (vb !== undefined && va === undefined) entries.set(a, vb);
  }
}

/** Flatten enriched JSON into normalized structure paths. */
export function flattenJsonPaths(value: unknown): string[] {
  return [...collectStructureEntries(value).keys()];
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
  const entriesByOp = new Map<string, Map<string, string>>();
  for (const op of operations) {
    const json = enrichedByOp[op];
    entriesByOp.set(op, json ? collectStructureEntries(json) : new Map());
  }

  const allPaths = new Set<string>();
  for (const m of entriesByOp.values()) {
    for (const p of m.keys()) allPaths.add(p);
  }

  const rows: ScenarioStructureRow[] = [];
  for (const path of [...allPaths].sort((a, b) => a.localeCompare(b))) {
    const presence: Record<string, boolean> = {};
    const values: Record<string, string> = {};
    for (const op of operations) {
      const v = entriesByOp.get(op)?.get(path);
      presence[op] = v !== undefined;
      values[op] = v ?? "—";
    }

    const presentOps = operations.filter((op) => presence[op]);
    const presentCount = presentOps.length;
    const presentValues = presentOps.map((op) => values[op]);
    const valueDiff =
      presentCount === operations.length &&
      uniqueStructureValues(presentValues, path) > 1;

    const presenceDiff = presentCount > 0 && presentCount < operations.length;

    if (!presenceDiff && !valueDiff) continue;

    rows.push({
      path,
      presence,
      values,
      kind: valueDiff ? "value" : "presence",
    });
  }
  return rows;
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
