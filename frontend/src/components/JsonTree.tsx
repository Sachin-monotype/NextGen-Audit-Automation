import { useMemo, useState, type ReactNode } from "react";

type Mode = "tree" | "raw";

type Props = {
  data: unknown;
  /** Start expanded one level (root children visible). */
  defaultOpen?: boolean;
};

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function typeLabel(v: unknown): string {
  if (v === null) return "null";
  if (Array.isArray(v)) return `array [${v.length}]`;
  if (isPlainObject(v)) return `object {${Object.keys(v).length}}`;
  return typeof v;
}

function formatPrimitive(v: unknown): string {
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (v === null) return "null";
  return String(v);
}

function TreeNode({
  name,
  value,
  depth,
  defaultExpanded,
}: {
  name: string;
  value: unknown;
  depth: number;
  defaultExpanded: boolean;
}) {
  const complex = isPlainObject(value) || Array.isArray(value);
  const [open, setOpen] = useState(defaultExpanded && depth < 2);

  if (!complex) {
    return (
      <div className="json-tree-row" style={{ paddingLeft: depth * 14 }}>
        <span className="json-tree-key">{name}</span>
        <span className="json-tree-sep">:</span>
        <span className={`json-tree-val t-${typeof value}`}>
          {typeof value === "string" ? `"${value}"` : formatPrimitive(value)}
        </span>
      </div>
    );
  }

  const entries: [string, unknown][] = Array.isArray(value)
    ? value.map((v, i) => [String(i), v])
    : Object.entries(value as Record<string, unknown>);

  return (
    <div className="json-tree-node">
      <button
        type="button"
        className="json-tree-toggle"
        style={{ paddingLeft: depth * 14 }}
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span className="json-tree-caret">{open ? "▾" : "▸"}</span>
        <span className="json-tree-key">{name}</span>
        <span className="json-tree-meta muted">{typeLabel(value)}</span>
      </button>
      {open && (
        <div className="json-tree-children">
          {entries.length === 0 ? (
            <div className="json-tree-row muted" style={{ paddingLeft: (depth + 1) * 14 }}>
              empty
            </div>
          ) : (
            entries.map(([k, v]) => (
              <TreeNode
                key={`${name}.${k}`}
                name={k}
                value={v}
                depth={depth + 1}
                defaultExpanded={defaultExpanded}
              />
            ))
          )}
        </div>
      )}
    </div>
  );
}

export default function JsonTree({ data, defaultOpen = true }: Props) {
  const [mode, setMode] = useState<Mode>("tree");
  const [expandAll, setExpandAll] = useState(0);
  const text = useMemo(() => JSON.stringify(data, null, 2), [data]);

  let body: ReactNode;
  if (mode === "raw") {
    body = <pre className="open json-tree-raw">{text}</pre>;
  } else if (data === undefined) {
    body = <p className="muted">No data</p>;
  } else {
    body = (
      <div className="json-tree" key={expandAll}>
        <TreeNode
          name={isPlainObject(data) ? "object" : Array.isArray(data) ? "array" : "value"}
          value={data}
          depth={0}
          defaultExpanded={defaultOpen || expandAll > 0}
        />
      </div>
    );
  }

  return (
    <div className="json-block">
      <div className="json-actions">
        <select
          className="json-mode-select"
          value={mode}
          onChange={(e) => setMode(e.target.value as Mode)}
          aria-label="JSON view mode"
        >
          <option value="tree">Tree</option>
          <option value="raw">Raw</option>
        </select>
        {mode === "tree" && (
          <button type="button" onClick={() => setExpandAll((n) => n + 1)}>
            Expand root
          </button>
        )}
        <button type="button" onClick={() => navigator.clipboard.writeText(text)}>
          Copy JSON
        </button>
      </div>
      {body}
    </div>
  );
}
