import type { ComparisonRow, LatestComparisonItem } from "../api";

export type ScenarioValueRow = {
  field_path: string;
  values: Record<string, string>;
  same: boolean;
  /** True when path is a touchpoint discriminator (listIds, listType, projectIds, …). */
  discriminator?: boolean;
};

export type ScenarioStructureRow = {
  path: string;
  presence: Record<string, boolean>;
  values: Record<string, string>;
  kind: "presence" | "value";
  discriminator?: boolean;
};

/** Paths that differentiate global vs list vs project vs favourite scenarios. */
const DISCRIMINATOR_RE =
  /metadata\.input\.(listIds|listType|projectIds|assetIds|tagIds|favourite|familyIds|styleIds|activationType|activationMode)/i;

/** Deep GQL response trees — hide from structure diff (noise); input + summary counts matter. */
const STRUCTURE_SKIP_RE =
  /(?:^|\.)metadata\.result\.(?:families\.nodes|styles|variations|batch\.|asset\.)/i;

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** Collapse numeric array indexes so length-only diffs do not spawn [0]/[1] rows. */
export function normalizeStructurePath(path: string): string {
  return path.replace(/\[\d+\]/g, "[*]");
}

export function isScenarioDiscriminatorPath(path: string): boolean {
  return DISCRIMINATOR_RE.test(path);
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

function shouldSkipStructurePath(path: string): boolean {
  return STRUCTURE_SKIP_RE.test(path);
}

/** Collect normalized path → serialized leaf value (scalar arrays collapsed). */
export function collectStructureEntries(json: unknown): Map<string, string> {
  const out = new Map<string, string>();

  function walk(value: unknown, prefix: string): void {
    const normPrefix = normalizeStructurePath(prefix);
    if (prefix && shouldSkipStructurePath(normPrefix)) return;

    if (value === null || value === undefined) {
      if (prefix) out.set(normPrefix, "null");
      return;
    }
    if (Array.isArray(value)) {
      if (!value.length) {
        if (prefix) out.set(normPrefix, "[]");
        return;
      }
      if (isScalarArray(value)) {
        out.set(normPrefix, serializeScalarArray(value));
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
        if (prefix) out.set(normPrefix, "{}");
        return;
      }
      for (const k of keys) {
        const key = prefix ? `${prefix}.${k}` : k;
        walk(value[k], key);
      }
      return;
    }
    if (prefix) out.set(normPrefix, serializeScalar(value));
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

/** Scan subject.metadata.input scalars from enriched JSON (mirrors backend scanner). */
export function collectMetadataInputPaths(json: unknown): Map<string, string> {
  const out = new Map<string, string>();
  if (!isPlainObject(json)) return out;
  const subject = json.subject;
  if (!isPlainObject(subject)) return out;
  const metadata = subject.metadata;
  if (!isPlainObject(metadata)) return out;
  const input = metadata.input;
  if (!isPlainObject(input)) return out;

  function walk(obj: Record<string, unknown>, prefix: string): void {
    for (const [k, v] of Object.entries(obj)) {
      const path = `${prefix}.${k}`;
      if (isScalarLeaf(v)) {
        out.set(path, serializeScalar(v));
      } else if (Array.isArray(v) && isScalarArray(v)) {
        out.set(path, serializeScalarArray(v));
        v.forEach((item, idx) => {
          out.set(`${path}[${idx}]`, serializeScalar(item));
        });
      }
    }
  }
  walk(input, "subject.metadata.input");
  return out;
}

function formatValue(v: string | undefined): string {
  return v?.trim() ? v : "—";
}

export function compareScenarioValues(
  operations: string[],
  items: LatestComparisonItem[],
  enrichedByOp?: Record<string, unknown | null>,
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
  // Merge metadata.input paths from enriched samples (often missing from stored rows).
  if (enrichedByOp) {
    for (const op of operations) {
      const meta = collectMetadataInputPaths(enrichedByOp[op] ?? null);
      for (const p of meta.keys()) paths.add(p);
    }
  }

  return [...paths]
    .sort((a, b) => {
      const da = isScenarioDiscriminatorPath(a) ? 0 : 1;
      const db = isScenarioDiscriminatorPath(b) ? 0 : 1;
      if (da !== db) return da - db;
      return a.localeCompare(b);
    })
    .map((field_path) => {
      const values: Record<string, string> = {};
      for (const op of operations) {
        const row = byOp.get(op)?.find((r) => r.field_path === field_path);
        if (row?.value_in_enriched?.trim()) {
          values[op] = row.value_in_enriched;
          continue;
        }
        const meta = collectMetadataInputPaths(enrichedByOp?.[op] ?? null);
        values[op] = formatValue(meta.get(field_path));
      }
      const uniq = new Set(operations.map((op) => values[op]));
      return {
        field_path,
        values,
        same: uniq.size <= 1,
        discriminator: isScenarioDiscriminatorPath(field_path),
      };
    });
}

/** Rows where a field exists in one scenario but not another (touchpoint-specific). */
export function compareScenarioDiscriminators(
  operations: string[],
  enrichedByOp: Record<string, unknown | null>,
): ScenarioStructureRow[] {
  const entriesByOp = new Map<string, Map<string, string>>();
  for (const op of operations) {
    entriesByOp.set(op, collectMetadataInputPaths(enrichedByOp[op] ?? null));
  }
  const allPaths = new Set<string>();
  for (const m of entriesByOp.values()) {
    for (const p of m.keys()) allPaths.add(p);
  }
  const rows: ScenarioStructureRow[] = [];
  for (const path of [...allPaths].sort()) {
    const presence: Record<string, boolean> = {};
    const values: Record<string, string> = {};
    for (const op of operations) {
      const v = entriesByOp.get(op)?.get(path);
      presence[op] = v !== undefined;
      values[op] = v ?? "—";
    }
    const presentCount = operations.filter((op) => presence[op]).length;
    if (presentCount === 0 || presentCount === operations.length) {
      const presentValues = operations.filter((op) => presence[op]).map((op) => values[op]);
      if (presentCount === operations.length && uniqueStructureValues(presentValues, path) <= 1) {
        continue;
      }
      if (presentCount === operations.length) {
        rows.push({ path, presence, values, kind: "value", discriminator: true });
      }
      continue;
    }
    rows.push({ path, presence, values, kind: "presence", discriminator: true });
  }
  return rows;
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
  for (const path of [...allPaths].sort((a, b) => {
    const da = isScenarioDiscriminatorPath(a) ? 0 : 1;
    const db = isScenarioDiscriminatorPath(b) ? 0 : 1;
    if (da !== db) return da - db;
    return a.localeCompare(b);
  })) {
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
      discriminator: isScenarioDiscriminatorPath(path),
    });
  }
  return rows;
}

export function structureDiffSummary(rows: ScenarioStructureRow[]): {
  onlyFirst: number;
  onlySecond: number;
  both: number;
  divergent: number;
  discriminators: number;
} {
  let onlyFirst = 0;
  let onlySecond = 0;
  let both = 0;
  let divergent = 0;
  let discriminators = 0;
  for (const r of rows) {
    if (r.discriminator) discriminators += 1;
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
  return { onlyFirst, onlySecond, both, divergent, discriminators };
}
