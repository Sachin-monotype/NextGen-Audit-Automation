import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import {
  fetchUiScriptCatalog,
  importGenerateFromUiScript,
  type UiScriptCatalog,
  type UiScriptCatalogRow,
  type UiTriggerJob,
} from "../api";

type Props = {
  onClose: () => void;
  onComplete: (job: UiTriggerJob) => void;
};

type Target = "web" | "app";

type EventGroup = {
  key: string;
  event_name: string;
  children: UiScriptCatalogRow[];
};

function pairId(row: Pick<UiScriptCatalogRow, "event_name" | "scenario">): string {
  return row.scenario ? `${row.event_name}::${row.scenario}` : row.event_name;
}

function EventScenarioDropdown({
  rows,
  selected,
  onToggle,
  onSelectAll,
  onClear,
  disabled,
}: {
  rows: UiScriptCatalogRow[];
  selected: Set<string>;
  onToggle: (id: string) => void;
  onSelectAll: () => void;
  onClear: () => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  const groups = useMemo(() => {
    const byOp = new Map<string, EventGroup>();
    for (const r of rows) {
      const op = r.event_name;
      if (!op) continue;
      const g = byOp.get(op) || { key: op, event_name: op, children: [] };
      g.children.push({ ...r, id: r.id || pairId(r) });
      byOp.set(op, g);
    }
    return [...byOp.values()]
      .map((g) => ({
        ...g,
        children: [...g.children].sort((a, b) => a.scenario.localeCompare(b.scenario)),
      }))
      .sort((a, b) => a.event_name.localeCompare(b.event_name));
  }, [rows]);

  const q = search.toLowerCase().trim();
  const filtered = useMemo(() => {
    if (!q) return groups;
    return groups
      .map((g) => {
        if (g.event_name.toLowerCase().includes(q)) return g;
        const children = g.children.filter(
          (c) =>
            c.scenario.toLowerCase().includes(q) ||
            c.correlation_id.toLowerCase().includes(q),
        );
        return children.length ? { ...g, children } : null;
      })
      .filter(Boolean) as EventGroup[];
  }, [groups, q]);

  useEffect(() => {
    if (!q) return;
    setExpanded(new Set(filtered.map((g) => g.key)));
  }, [q, filtered]);

  const shownCount = filtered.reduce((n, g) => n + g.children.length, 0);
  const label = selected.size
    ? `${selected.size} scenario${selected.size > 1 ? "s" : ""} selected`
    : "All operations";

  function toggleGroup(g: EventGroup) {
    const ids = g.children.map((c) => c.id || pairId(c));
    const allOn = ids.every((id) => selected.has(id));
    for (const id of ids) {
      if (allOn) {
        if (selected.has(id)) onToggle(id);
      } else if (!selected.has(id)) {
        onToggle(id);
      }
    }
  }

  function toggleExpand(key: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  return (
    <div className={`op-dropdown ui-script-op-dropdown${disabled ? " disabled" : ""}`} ref={ref}>
      <button
        type="button"
        className="op-dropdown-trigger"
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
      >
        <span>{label}</span>
        <span className="chevron">{open ? "▴" : "▾"}</span>
      </button>
      {open && !disabled && (
        <div className="op-dropdown-menu">
          <div className="op-dropdown-search">
            <input
              autoFocus
              placeholder="Search operations…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <div className="op-dropdown-actions">
            <button type="button" onClick={onSelectAll}>
              Select all
            </button>
            <button type="button" onClick={onClear}>
              Clear
            </button>
            <span className="muted">{shownCount} shown</span>
          </div>
          <div className="op-dropdown-list">
            {filtered.length === 0 && (
              <span className="muted op-dropdown-empty">No matches</span>
            )}
            {filtered.map((g) => {
              const ids = g.children.map((c) => c.id || pairId(c));
              const selectedCount = ids.filter((id) => selected.has(id)).length;
              const allOn = selectedCount === ids.length && ids.length > 0;
              const someOn = selectedCount > 0 && !allOn;
              const isOpen = expanded.has(g.key) || Boolean(q);
              return (
                <div key={g.key} className="op-dropdown-group">
                  <div className="op-dropdown-group-head">
                    <button
                      type="button"
                      className="op-group-expand"
                      onClick={() => toggleExpand(g.key)}
                      aria-expanded={isOpen}
                    >
                      {isOpen ? "▾" : "▸"}
                    </button>
                    <label className="op-dropdown-item op-group-label">
                      <input
                        type="checkbox"
                        checked={allOn}
                        ref={(el) => {
                          if (el) el.indeterminate = someOn;
                        }}
                        onChange={() => toggleGroup(g)}
                      />
                      <span>
                        <strong>{g.event_name}</strong>
                        <span className="muted"> · {g.children.length}</span>
                      </span>
                    </label>
                  </div>
                  {isOpen && (
                    <div className="op-dropdown-children">
                      {g.children.map((c) => {
                        const id = c.id || pairId(c);
                        return (
                          <label key={id} className="op-dropdown-item nested">
                            <input
                              type="checkbox"
                              checked={selected.has(id)}
                              onChange={() => onToggle(id)}
                            />
                            <span>
                              {c.scenario || "(default)"}
                              {c.has_auth_token ? (
                                <span className="muted steps-hint"> · auth_token</span>
                              ) : null}
                            </span>
                          </label>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

export default function GenerateFromUiScriptModal({ onClose, onComplete }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [target, setTarget] = useState<Target>("web");
  const [file, setFile] = useState<File | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [catalog, setCatalog] = useState<UiScriptCatalog | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const loadCatalog = useCallback(async (tgt: Target, excel: File | null) => {
    setLoading(true);
    setError("");
    setSelected(new Set());
    try {
      const data = await fetchUiScriptCatalog(tgt, excel);
      setCatalog(data);
    } catch (e) {
      setCatalog(null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadCatalog(target, file);
  }, [target, file, loadCatalog]);

  const rows = catalog?.rows ?? [];

  const matchingRows = useMemo(() => {
    if (selected.size === 0) return rows;
    return rows.filter((r) => selected.has(r.id || pairId(r)));
  }, [rows, selected]);

  const authTokenCount = matchingRows.filter((r) => r.has_auth_token).length;

  function onPick(f: File | null) {
    setError("");
    if (!f) {
      setFile(null);
      return;
    }
    const name = f.name.toLowerCase();
    if (!name.endsWith(".xlsx") && !name.endsWith(".xls") && !name.endsWith(".csv")) {
      setError("Use an Excel file (.xlsx) from the Playwright web-audit runner.");
      return;
    }
    setFile(f);
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) onPick(f);
  }

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function onSubmit() {
    if (matchingRows.length === 0) {
      setError("No matching OK rows for this selection.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const pairs =
        selected.size === 0
          ? undefined
          : matchingRows.map((r) => ({
              event_name: r.event_name,
              scenario: r.scenario,
            }));
      const res = await importGenerateFromUiScript({
        target,
        pairs,
        file,
      });
      if (!res.job) throw new Error(res.error || "Import failed");
      onComplete(res.job);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const sourceLabel = file
    ? `${file.name} · ${catalog?.sheet || target} · ${catalog?.count ?? 0} OK rows`
    : catalog
      ? `${catalog.sheet} · ${catalog.count ?? 0} OK rows`
      : "";

  return (
    <div className="modal-backdrop" onClick={onClose} role="presentation">
      <div
        className="modal-card generate-ui-script-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Generate from UI Script"
      >
        <div className="modal-head">
          <strong>Generate from UI Script</strong>
          <button type="button" className="link-btn" onClick={onClose}>
            close ✕
          </button>
        </div>

        <p className="muted small">
          Uses <code>datasource-latest.xlsx</code> by default, or drop your own Excel. Pick Web/App,
          then event → scenario. Actor JWT comes from Excel <code>auth_token</code> when set;
          otherwise the project logged-in user.
        </p>

        <div
          className={`ui-script-dropzone${dragOver ? " drag-over" : ""}${file ? " has-file" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
          onClick={() => inputRef.current?.click()}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
          }}
        >
          <input
            ref={inputRef}
            type="file"
            accept=".xlsx,.xls,.csv"
            hidden
            onChange={(e) => onPick(e.target.files?.[0] ?? null)}
          />
          {file ? (
            <>
              <strong>{file.name}</strong>
              <span className="muted">
                ({Math.round(file.size / 1024)} KB) · click to replace ·{" "}
                <button
                  type="button"
                  className="link-btn"
                  onClick={(e) => {
                    e.stopPropagation();
                    onPick(null);
                  }}
                >
                  use default path
                </button>
              </span>
            </>
          ) : (
            <>
              <strong>Drop Excel here</strong>
              <span className="muted">or click to browse · leave empty to use default path</span>
            </>
          )}
        </div>

        <div className="ui-script-target-toggle" role="group" aria-label="Target">
          <button
            type="button"
            className={target === "web" ? "active" : ""}
            disabled={busy || loading}
            onClick={() => setTarget("web")}
          >
            Web
          </button>
          <button
            type="button"
            className={target === "app" ? "active" : ""}
            disabled={busy || loading}
            onClick={() => setTarget("app")}
          >
            App
          </button>
        </div>

        {loading ? (
          <p className="muted small">Loading catalog…</p>
        ) : catalog ? (
          <>
            <p className="muted small ui-script-path" title={catalog.path || catalog.filename || ""}>
              {sourceLabel}
            </p>
            <div className="ui-script-filters">
              <EventScenarioDropdown
                rows={rows}
                selected={selected}
                disabled={busy}
                onToggle={toggle}
                onSelectAll={() =>
                  setSelected(new Set(rows.map((r) => r.id || pairId(r))))
                }
                onClear={() => setSelected(new Set())}
              />
            </div>
            <p className="muted small">
              Will import <strong>{matchingRows.length}</strong> row
              {matchingRows.length === 1 ? "" : "s"}
              {authTokenCount > 0
                ? ` · ${authTokenCount} with Excel auth_token`
                : " · actor from logged-in user"}
              {selected.size === 0 ? " (all)" : ""}.
            </p>
          </>
        ) : null}

        {error && <p className="error small">{error}</p>}

        <div className="modal-actions">
          <button type="button" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            className="primary"
            disabled={busy || loading || matchingRows.length === 0}
            onClick={() => void onSubmit()}
          >
            {busy ? "Importing…" : "Import & verify"}
          </button>
        </div>
      </div>
    </div>
  );
}
