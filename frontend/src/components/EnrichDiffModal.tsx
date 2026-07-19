import { useMemo, useState } from "react";

type DiffKind = "changed" | "onlyA" | "onlyB";

type DiffRow = {
  path: string;
  kind: DiffKind;
  a: string;
  b: string;
};

type Props = {
  labelA: string;
  labelB: string;
  dataA: unknown;
  dataB: unknown;
  onClose: () => void;
};

/** Deep-diff two enrich (or any) JSON payloads and highlight leaf differences. */
export default function EnrichDiffModal({ labelA, labelB, dataA, dataB, onClose }: Props) {
  const [filter, setFilter] = useState("");
  const [onlyMeta, setOnlyMeta] = useState(false);

  const diffs = useMemo(() => diffJson(dataA, dataB), [dataA, dataB]);

  const visible = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return diffs.filter((d) => {
      if (onlyMeta && !isMetaPath(d.path)) return false;
      if (!q) return true;
      return (
        d.path.toLowerCase().includes(q) ||
        d.a.toLowerCase().includes(q) ||
        d.b.toLowerCase().includes(q)
      );
    });
  }, [diffs, filter, onlyMeta]);

  const counts = useMemo(() => {
    const changed = diffs.filter((d) => d.kind === "changed").length;
    const onlyA = diffs.filter((d) => d.kind === "onlyA").length;
    const onlyB = diffs.filter((d) => d.kind === "onlyB").length;
    return { changed, onlyA, onlyB, total: diffs.length };
  }, [diffs]);

  return (
    <div className="modal-backdrop" onClick={onClose} role="presentation">
      <div
        className="modal-card enrich-diff-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Enrich JSON diff"
      >
        <div className="modal-head">
          <strong>Compare enrich JSON</strong>
          <button type="button" className="link-btn" onClick={onClose}>
            close ✕
          </button>
        </div>
        <p className="muted small">
          Leaf-level diff — useful for spotting metadata gaps like{" "}
          <code>activationType</code>, <code>platformEnvironment</code>, etc.
        </p>
        <div className="enrich-diff-labels">
          <span>
            <strong>A</strong> {labelA}
          </span>
          <span>
            <strong>B</strong> {labelB}
          </span>
        </div>
        <div className="enrich-diff-toolbar">
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="filter path / value…"
          />
          <label className="checkbox">
            <input
              type="checkbox"
              checked={onlyMeta}
              onChange={(e) => setOnlyMeta(e.target.checked)}
            />
            metadata / source / activation only
          </label>
          <span className="muted small">
            {counts.changed} changed · {counts.onlyA} only A · {counts.onlyB} only B
            {visible.length !== counts.total ? ` · showing ${visible.length}` : ""}
          </span>
        </div>
        {visible.length === 0 ? (
          <p className="ok small">No differences{filter || onlyMeta ? " match the filter" : ""}.</p>
        ) : (
          <div className="result-table-wrap compact-table-wrap">
            <table className="result-table enrich-diff-table">
              <thead>
                <tr>
                  <th>Path</th>
                  <th>A</th>
                  <th>B</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {visible.map((d) => (
                  <tr key={d.path} className={d.kind === "changed" ? "fail" : "skip"}>
                    <td>
                      <code>{d.path}</code>
                    </td>
                    <td className="diff-val">{d.a || "—"}</td>
                    <td className="diff-val">{d.b || "—"}</td>
                    <td>
                      <span className={`badge ${d.kind === "changed" ? "fail" : "skip"}`}>
                        {d.kind === "changed" ? "≠" : d.kind === "onlyA" ? "A" : "B"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function isMetaPath(path: string): boolean {
  const p = path.toLowerCase();
  return (
    p.startsWith("source.") ||
    p.includes("activationtype") ||
    p.includes("activationmode") ||
    p.includes("platform") ||
    p.includes("operationstate") ||
    p.includes("metadata") ||
    p === "eventversion" ||
    p === "xcorrelationid" ||
    p === "correlationid"
  );
}

function stringify(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function flatten(value: unknown, prefix = "", out: Map<string, string> = new Map()): Map<string, string> {
  if (Array.isArray(value)) {
    if (value.length === 0) {
      out.set(prefix || "$", "[]");
      return out;
    }
    value.forEach((item, i) => flatten(item, prefix ? `${prefix}[${i}]` : `[${i}]`, out));
    return out;
  }
  if (isPlainObject(value)) {
    const keys = Object.keys(value);
    if (keys.length === 0) {
      out.set(prefix || "$", "{}");
      return out;
    }
    for (const k of keys) {
      flatten(value[k], prefix ? `${prefix}.${k}` : k, out);
    }
    return out;
  }
  out.set(prefix || "$", stringify(value));
  return out;
}

export function diffJson(a: unknown, b: unknown): DiffRow[] {
  const fa = flatten(a);
  const fb = flatten(b);
  const paths = new Set([...fa.keys(), ...fb.keys()]);
  const rows: DiffRow[] = [];
  for (const path of [...paths].sort()) {
    const av = fa.get(path);
    const bv = fb.get(path);
    if (av === bv) continue;
    if (av !== undefined && bv !== undefined) {
      rows.push({ path, kind: "changed", a: av, b: bv });
    } else if (av !== undefined) {
      rows.push({ path, kind: "onlyA", a: av, b: "" });
    } else {
      rows.push({ path, kind: "onlyB", a: "", b: bv || "" });
    }
  }
  return rows;
}
