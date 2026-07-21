import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import JsonTree from "../components/JsonTree";
import EnrichDiffModal from "../components/EnrichDiffModal";
import MultiSelect from "../components/MultiSelect";
import {
  fetchFilterValues,
  fetchIngestionStatus,
  fetchLogs,
  fetchOperationCurl,
  fetchUiConfig,
  hasActiveFilters,
  purgeIngestion,
  startCompare,
  startIngestion,
  stopIngestion,
  type FilterState,
  type FilterValues,
  type IngestionStatus,
  type LogRow,
  type OperationCurl,
  type Tab,
} from "../api";

type DisplayPageProps = {
  /** Start a compare for one operation, then land on the live Compare tab. */
  onCompareRequested?: (jobId: string) => void;
};

const EMPTY: FilterState = {
  xCorrelationId: "",
  "source.operation": "",
  "actor.globalUserId": "",
  "source.platformEnvironment": "",
  "source.service": "",
  "source.operationState": "",
};

const TEXT_FILTERS: { key: keyof FilterState; label: string; placeholder?: string }[] = [
  { key: "xCorrelationId", label: "correlation id", placeholder: "xCorrelationId or correlationId" },
  { key: "actor.globalUserId", label: "globalUserId" },
];

const ENUM_FILTERS: { key: keyof FilterValues; label: string }[] = [
  { key: "source.platformEnvironment", label: "environment" },
  { key: "source.service", label: "service" },
  { key: "source.operationState", label: "state" },
];

function OperationFilter({
  options,
  selected,
  onSelectedChange,
}: {
  options: string[];
  selected: string[];
  onSelectedChange: (ops: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [listSearch, setListSearch] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
        setListSearch("");
      }
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const filtered = options.filter((op) =>
    op.toLowerCase().includes(listSearch.toLowerCase())
  );

  function toggle(op: string) {
    if (selected.includes(op)) onSelectedChange(selected.filter((v) => v !== op));
    else onSelectedChange([...selected, op]);
  }

  const label = selected.length
    ? selected.length === 1
      ? selected[0]
      : `${selected.length} operations`
    : "All operations";

  return (
    <div className="filter-field operation-filter" ref={ref}>
      <span>operation</span>
      <button type="button" className="op-dropdown-trigger" onClick={() => setOpen((o) => !o)}>
        <span className={selected.length ? "" : "muted"}>{label}</span>
        <span className="chevron">{open ? "▴" : "▾"}</span>
      </button>
      {open && (
        <div className="op-dropdown-menu operation-filter-menu">
          <div className="op-dropdown-search">
            <input
              autoFocus
              placeholder="Search operations…"
              value={listSearch}
              onChange={(e) => setListSearch(e.target.value)}
            />
          </div>
          <div className="op-dropdown-actions">
            <button type="button" onClick={() => onSelectedChange(filtered)}>Select all</button>
            <button type="button" onClick={() => onSelectedChange([])}>Clear</button>
            <span className="muted">{filtered.length} of {options.length}</span>
          </div>
          <div className="op-dropdown-list">
            {filtered.map((op) => (
              <label key={op} className="op-dropdown-item">
                <input
                  type="checkbox"
                  checked={selected.includes(op)}
                  onChange={() => toggle(op)}
                />
                <span>{op}</span>
              </label>
            ))}
            {filtered.length === 0 && (
              <p className="muted op-dropdown-empty">No operations match.</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

const TAB_LABELS: Record<Tab, string> = {
  raw: "Raw",
  enriched: "Enriched",
  dlq: "DLQ",
};

const DEFAULT_PAGE_SIZES = [20, 50, 100, 200];

function IngestionPanel() {
  const [status, setStatus] = useState<IngestionStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setStatus(await fetchIngestionStatus());
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh]);

  const [notice, setNotice] = useState("");

  async function toggle() {
    setBusy(true);
    try {
      setStatus(status?.running ? await stopIngestion() : await startIngestion());
    } finally {
      setBusy(false);
    }
  }

  async function purge() {
    if (!confirm("Purge all queued messages? Consumed events already in Mongo are kept.")) return;
    setBusy(true);
    setNotice("");
    try {
      const res = await purgeIngestion();
      setNotice(res.ok ? `Purged ${res.total_purged ?? 0} queued message(s)` : res.error || "Purge failed");
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  const running = status?.running ?? false;
  const connError = (status?.consumers || []).map((c) => c.last_error).find((e) => e && e.trim());
  const unreachable = running && !status?.rabbitmq_connected && !!connError;
  const dotClass = running
    ? status?.rabbitmq_connected
      ? "on"
      : unreachable
        ? "off"
        : "connecting"
    : "off";

  return (
    <div className="ingestion-panel">
      <div className="ingestion-summary">
        <span className={`ingestion-dot ${dotClass}`} />
        <strong>Live ingestion</strong>
        <span className={unreachable ? "error" : "muted"}>
          {running
            ? status?.rabbitmq_connected
              ? "draining queues → Mongo"
              : unreachable
                ? "unreachable — check VPN"
                : "connecting…"
            : "stopped"}
        </span>
        {status && (
          <span className="ingestion-metrics muted">
            {status.totals.inserted} inserted · {status.totals.consumed} consumed
          </span>
        )}
        {notice && <span className="muted">{notice}</span>}
        <button type="button" className="link-btn" onClick={() => setOpen((o) => !o)}>
          {open ? "hide" : "details"}
        </button>
        <button type="button" className="link-btn" disabled={busy} onClick={purge} title="Drop queued backlog so only fresh events are ingested">
          Purge queue
        </button>
        <button type="button" className={running ? "" : "primary"} disabled={busy} onClick={toggle}>
          {busy ? "…" : running ? "Stop" : "Start"}
        </button>
      </div>
      {open && status && (
        <div className="ingestion-details">
          <p className="muted">
            Retains latest {status.max_docs_per_operation ?? "?"} docs per operation · Mongo{" "}
            {status.mongo_connected ? "connected" : "offline"} · pruned {status.cleanup_deleted ?? 0}
          </p>
          {connError && <p className="error">{connError}</p>}
          <table className="ingestion-table">
            <thead>
              <tr>
                <th>Queue</th>
                <th>→ Collection</th>
                <th>Conn</th>
                <th>Consumed</th>
                <th>Inserted</th>
              </tr>
            </thead>
            <tbody>
              {status.consumers.map((c) => (
                <tr key={c.name}>
                  <td className="mono">{c.queue}</td>
                  <td className="mono">{c.collection}</td>
                  <td>{c.connected ? "✓" : "—"}</td>
                  <td>{c.consumed}</td>
                  <td>{c.inserted}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {status.error && <p className="error">{status.error}</p>}
        </div>
      )}
    </div>
  );
}

function JsonBlock({ data }: { data: Record<string, unknown> }) {
  return <JsonTree data={data} defaultOpen />;
}

function TriggerInfo({ operation }: { operation: string }) {
  const [open, setOpen] = useState(false);
  const [curl, setCurl] = useState<OperationCurl | null>(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (next && !curl && operation) {
      setLoading(true);
      try {
        setCurl(await fetchOperationCurl(operation));
      } finally {
        setLoading(false);
      }
    }
  }

  function copyCurl() {
    if (!curl?.curl) return;
    navigator.clipboard.writeText(curl.curl);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  const nav = curl?.ui_navigation?.navigation ?? [];

  return (
    <div className="trigger-info">
      <button type="button" className="trigger-toggle" onClick={toggle}>
        {open ? "▾" : "▸"} How to trigger this event
      </button>
      {open && (
        <div className="trigger-body">
          {loading && <p className="muted">Loading…</p>}
          {curl && (
            <>
              {nav.length > 0 ? (
                <div className="trigger-nav">
                  <span className="trigger-label">UI navigation</span>
                  <ul>
                    {nav.map((n, i) => (
                      <li key={i}>{n}</li>
                    ))}
                  </ul>
                </div>
              ) : (
                <p className="muted">No UI navigation recorded for this event.</p>
              )}
              <div className="trigger-curl">
                <div className="trigger-curl-head">
                  <span className="trigger-label">
                    {curl.kind === "graphql" ? "GraphQL" : "Ingress"} curl
                    {!curl.has_captured_event && <em className="muted"> (skeleton — no captured event)</em>}
                  </span>
                  <button type="button" className="primary sm" onClick={copyCurl}>
                    {copied ? "Copied!" : "Copy curl"}
                  </button>
                </div>
                <pre className="curl-block">{curl.curl}</pre>
                {curl.note && <p className="trigger-note muted">{curl.note}</p>}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

export default function DisplayPage({ onCompareRequested }: DisplayPageProps) {
  const [tab, setTab] = useState<Tab>("enriched");
  const [comparingOp, setComparingOp] = useState<string | null>(null);
  const [filters, setFilters] = useState<FilterState>(EMPTY);
  const [applied, setApplied] = useState<FilterState>(EMPTY);
  const [filterValues, setFilterValues] = useState<FilterValues>({
    "source.platformEnvironment": [],
    "source.service": [],
    "source.operationState": [],
    "source.operation": [],
  });
  const [opSelected, setOpSelected] = useState<string[]>([]);
  const [rows, setRows] = useState<LogRow[]>([]);
  const [total, setTotal] = useState(0);
  const [unique, setUnique] = useState(true);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [pageSizes, setPageSizes] = useState<number[]>(DEFAULT_PAGE_SIZES);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());
  const [diffPick, setDiffPick] = useState<string[]>([]);
  const [enrichDiff, setEnrichDiff] = useState<{
    labelA: string;
    labelB: string;
    dataA: unknown;
    dataB: unknown;
  } | null>(null);

  useEffect(() => {
    fetchUiConfig().then((cfg) => {
      if (cfg.pageSizeOptions?.length) setPageSizes(cfg.pageSizeOptions);
      if (cfg.defaultPageSize) setPageSize(cfg.defaultPageSize);
    });
  }, []);

  useEffect(() => {
    setDiffPick([]);
    setEnrichDiff(null);
  }, [tab]);

  useEffect(() => {
    fetchFilterValues(tab).then(setFilterValues).catch(() => {});
  }, [tab]);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const dedupe = !hasActiveFilters(applied);
      const data = await fetchLogs(tab, applied, page, pageSize, dedupe);
      setRows(data.results);
      setTotal(data.total);
      setUnique(data.unique ?? dedupe);
      if ((data as { error?: string }).error) {
        setError((data as { error?: string }).error || "");
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [tab, applied, page, pageSize]);

  useEffect(() => {
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, [load]);

  function apply() {
    setPage(1);
    const next = { ...filters, "source.operation": opSelected.join(",") };
    setFilters(next);
    setApplied(next);
  }

  function clear() {
    setFilters(EMPTY);
    setApplied(EMPTY);
    setOpSelected([]);
    setPage(1);
  }

  function onFilterKeyDown(e: KeyboardEvent) {
    if (e.key === "Enter") apply();
  }

  function setEnum(key: keyof FilterValues, values: string[]) {
    const next = { ...filters, [key]: values.join(",") };
    setFilters(next);
    setPage(1);
    setApplied(next);
  }

  const pages = Math.max(1, Math.ceil(total / pageSize));

  function rowKey(row: LogRow, i: number): string {
    const op = row["source.operation"] || "(unknown)";
    return unique
      ? `${tab}:${op}`
      : `${tab}:${op}:${row.xCorrelationId || row.correlationId || row.occurredAt || i}`;
  }

  function toggleDiffPick(key: string) {
    setDiffPick((prev) => {
      if (prev.includes(key)) return prev.filter((k) => k !== key);
      if (prev.length >= 2) return [prev[1], key];
      return [...prev, key];
    });
  }

  async function compareRow(row: LogRow) {
    if (!onCompareRequested) return;
    // Prefer the scenario label (owned/UI/BE) so Compare pairs by our own
    // correlation; fall back to the bare operation for events fired by others.
    const op = row.scenario || row["source.operation"] || "";
    if (!op) return;
    setComparingOp(op);
    try {
      // Pin the exact event on this card so Compare pairs by its correlation —
      // works for our own runs and for events other teams fired.
      const cid = row.xCorrelationId || row.correlationId || "";
      const job = await startCompare(
        [op],
        undefined,
        cid ? { [op]: cid } : undefined,
      );
      onCompareRequested(job.id);
    } catch (e) {
      setError(String(e));
    } finally {
      setComparingOp(null);
    }
  }

  function openEnrichDiff() {
    if (diffPick.length !== 2) return;
    const a = rows.find((r, i) => rowKey(r, i) === diffPick[0]);
    const b = rows.find((r, i) => rowKey(r, i) === diffPick[1]);
    if (!a?.message || !b?.message) return;
    setEnrichDiff({
      labelA: `${a["source.operation"]} · ${(a.xCorrelationId || a.correlationId || "").slice(0, 8)}`,
      labelB: `${b["source.operation"]} · ${(b.xCorrelationId || b.correlationId || "").slice(0, 8)}`,
      dataA: a.message,
      dataB: b.message,
    });
  }

  return (
    <section className="panel display-panel">
      <div className="display-sticky">
        <header className="panel-head panel-head-row display-compact-head">
          <div className="display-title-line">
            <h2>Enrich/raw</h2>
            <span className="muted">
              {unique ? "latest per operation" : "all matches"}
            </span>
          </div>
          <label className="collection-select">
            Collection
            <select
              value={tab}
              onChange={(e) => {
                setTab(e.target.value as Tab);
                setPage(1);
              }}
            >
              {(["raw", "enriched", "dlq"] as Tab[]).map((t) => (
                <option key={t} value={t}>{TAB_LABELS[t]}</option>
              ))}
            </select>
          </label>
        </header>

        <IngestionPanel />

        <form
          className="filter-row display-filter-row"
          onSubmit={(e) => {
            e.preventDefault();
            apply();
          }}
        >
          <div className="display-filter-left">
            <OperationFilter
              options={filterValues["source.operation"] ?? []}
              selected={opSelected}
              onSelectedChange={setOpSelected}
            />
          </div>
          <div className="display-filter-right">
            {TEXT_FILTERS.map(({ key, label, placeholder }) => (
              <label key={key} className="filter-field">
                <span>{label}</span>
                <input
                  value={filters[key]}
                  placeholder={placeholder}
                  onChange={(e) => setFilters({ ...filters, [key]: e.target.value })}
                  onKeyDown={onFilterKeyDown}
                />
              </label>
            ))}
            {ENUM_FILTERS.map(({ key, label }) => (
              <MultiSelect
                key={key}
                label={label}
                options={filterValues[key] ?? []}
                selected={filters[key] ? filters[key].split(",").filter(Boolean) : []}
                onChange={(values) => setEnum(key, values)}
              />
            ))}
            <div className="filter-actions">
              <button type="submit" className="primary">Apply</button>
              <button type="button" onClick={clear}>Clear</button>
            </div>
          </div>
        </form>

        {error && <p className="error">{error}</p>}

        <div className="pager">
          <button type="button" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Previous</button>
          <span>
            Page {page} of {pages} ({total} {unique ? "operations" : "entries"})
          </span>
          <button type="button" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>Next</button>
          <label className="page-size-select">
            Limit
            <select
              value={pageSize}
              onChange={(e) => {
                setPageSize(Number(e.target.value));
                setPage(1);
              }}
            >
              {pageSizes.map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </label>
          {tab === "enriched" && (
            <>
              <button
                type="button"
                className="link-btn"
                disabled={diffPick.length !== 2}
                onClick={openEnrichDiff}
                title="Pick two enriched cards, then compare leaf-level JSON"
              >
                Compare enrich ({diffPick.length}/2)
              </button>
              {diffPick.length > 0 && (
                <button type="button" className="link-btn" onClick={() => setDiffPick([])}>
                  clear pick
                </button>
              )}
            </>
          )}
          {loading && <span className="muted">Loading…</span>}
        </div>
      </div>

      <div className="display-table">
        {rows.length === 0 && !loading && <p className="muted">No entries match.</p>}
        {rows.map((row, i) => {
          const key = rowKey(row, i);
          return (
            <LogCard
              key={key}
              row={row}
              open={expandedRows.has(key)}
              selectable={tab === "enriched"}
              selected={diffPick.includes(key)}
              onSelect={() => toggleDiffPick(key)}
              onCompare={onCompareRequested ? () => compareRow(row) : undefined}
              comparing={comparingOp === (row.scenario || row["source.operation"])}
              onToggle={() => setExpandedRows((current) => {
                const next = new Set(current);
                if (next.has(key)) next.delete(key);
                else next.add(key);
                return next;
              })}
            />
          );
        })}
      </div>
      {enrichDiff && (
        <EnrichDiffModal
          labelA={enrichDiff.labelA}
          labelB={enrichDiff.labelB}
          dataA={enrichDiff.dataA}
          dataB={enrichDiff.dataB}
          onClose={() => setEnrichDiff(null)}
        />
      )}
    </section>
  );
}

/** Enrich/raw payload card — collapsed by default; expand to view JSON. */
function LogCard({
  row,
  open,
  onToggle,
  selectable,
  selected,
  onSelect,
  onCompare,
  comparing,
}: {
  row: LogRow;
  open: boolean;
  onToggle: () => void;
  selectable?: boolean;
  selected?: boolean;
  onSelect?: () => void;
  onCompare?: () => void;
  comparing?: boolean;
}) {
  const op = row["source.operation"] || "(unknown)";
  const cid = row.xCorrelationId || row.correlationId || "";
  const channel = row.channel;
  return (
    <article className={`log-card${open ? " is-open" : " is-collapsed"}${selected ? " is-picked" : ""}`}>
      <button
        type="button"
        className="log-card-head"
        onClick={onToggle}
        aria-expanded={open}
      >
        {selectable && (
          <input
            type="checkbox"
            checked={!!selected}
            onClick={(e) => e.stopPropagation()}
            onChange={() => onSelect?.()}
            title="Select for enrich diff"
            aria-label={`Select ${op} for enrich diff`}
          />
        )}
        <span className="chevron">{open ? "▾" : "▸"}</span>
        <div className="log-meta">
          <span className="meta-chip">
            <strong>operation</strong> {row.scenario || op}
            {channel && (
              <span className={`channel-badge ${channel.toLowerCase()}`}>({channel})</span>
            )}
          </span>
          <span className="meta-chip"><strong>state</strong> {row["source.operationState"]}</span>
          <span className="meta-chip"><strong>service</strong> {row["source.service"]}</span>
          <span className="meta-chip"><strong>env</strong> {row["source.platformEnvironment"]}</span>
          <span className="meta-chip"><strong>occurredAt</strong> {row.occurredAt}</span>
        </div>
        <span className="log-card-hint muted">{open ? "collapse" : "expand payload"}</span>
      </button>
      <div className="log-correlation">
        <strong>xCorrelationId</strong> {cid || "—"}
        {row.correlationId && row.correlationId !== row.xCorrelationId ? (
          <> · <strong>correlationId</strong> {row.correlationId}</>
        ) : null}
        {onCompare && (
          <button
            type="button"
            className="link-btn log-card-compare"
            disabled={comparing}
            onClick={onCompare}
            title="Compare this event's enriched vs source now"
          >
            {comparing ? "Comparing…" : "Compare now"}
          </button>
        )}
      </div>
      <div className="log-card-body" hidden={!open}>
        <TriggerInfo operation={op} />
        <JsonBlock data={row.message} />
      </div>
    </article>
  );
}
