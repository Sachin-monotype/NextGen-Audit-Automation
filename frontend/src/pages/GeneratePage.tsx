import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchCategories,
  fetchCoverage,
  fetchDefaultPayload,
  fetchJob,
  fetchLastGenerateRun,
  fetchOperations,
  fetchOperationSources,
  fetchOperationStats,
  fetchPayloadCurl,
  fetchPipelineConfig,
  fetchTokenStatus,
  refreshToken,
  sendCustomPayload,
  startGenerate,
  type CategoryReport,
  type CoverageReport,
  type GenerateRunReport,
  type Job,
  type OperationSources,
  type OperationStats,
  type PipelineConfig,
  type SendCustomResult,
  type TokenStatus,
} from "../api";

const SOURCE_KINDS = [
  { id: "graphql", label: "GraphQL" },
  { id: "ingress", label: "Ingress" },
  { id: "cron", label: "Cron" },
] as const;

const JOB_KEY = "audit-generate-job";

type DropdownItem = { id: string; label: string; kind: string };

type ListModalState = {
  title: string;
  columns: string[];
  rows: Array<Record<string, string | number>>;
};

function OpListModal({
  state,
  onClose,
}: {
  state: ListModalState | null;
  onClose: () => void;
}) {
  if (!state) return null;
  return (
    <div className="modal-backdrop" onClick={onClose} role="presentation">
      <div className="modal-card op-list-modal" onClick={(e) => e.stopPropagation()} role="dialog">
        <div className="modal-head">
          <strong>{state.title}</strong>
          <span className="muted"> · {state.rows.length} row{state.rows.length === 1 ? "" : "s"}</span>
          <button type="button" className="link-btn" onClick={onClose}>close ✕</button>
        </div>
        <div className="result-table-wrap compact-table-wrap">
          <table className="result-table">
            <thead>
              <tr>
                {state.columns.map((c) => (
                  <th key={c}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {state.rows.length === 0 ? (
                <tr><td colSpan={state.columns.length} className="muted">No operations</td></tr>
              ) : (
                state.rows.map((row, i) => (
                  <tr key={i}>
                    {state.columns.map((c) => (
                      <td key={c}><code>{String(row[c] ?? "")}</code></td>
                    ))}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

type OperationDropdownProps = {
  options: DropdownItem[];
  selected: Set<string>;
  onToggle: (id: string) => void;
  onSelectAll: () => void;
  onClear: () => void;
};

function OperationDropdown({ options, selected, onToggle, onSelectAll, onClear }: OperationDropdownProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  const q = search.toLowerCase();
  const filtered = options.filter((o) => o.label.toLowerCase().includes(q));

  const label = selected.size
    ? `${selected.size} operation${selected.size > 1 ? "s" : ""} selected`
    : "All operations";

  return (
    <div className="op-dropdown" ref={ref}>
      <button
        type="button"
        className="op-dropdown-trigger"
        onClick={() => setOpen((o) => !o)}
      >
        <span>{label}</span>
        <span className="chevron">{open ? "▴" : "▾"}</span>
      </button>

      {open && (
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
            <button type="button" onClick={onSelectAll}>Select all</button>
            <button type="button" onClick={onClear}>Clear</button>
            <span className="muted">{filtered.length} shown</span>
          </div>
          <div className="op-dropdown-list">
            {filtered.map((o) => (
              <label key={o.id} className="op-dropdown-item">
                <input
                  type="checkbox"
                  checked={selected.has(o.id)}
                  onChange={() => onToggle(o.id)}
                />
                <span>{o.label}</span>
                {o.kind !== "graphql" && <span className={`kind-tag ${o.kind}`}>{o.kind}</span>}
              </label>
            ))}
            {filtered.length === 0 && <p className="muted op-dropdown-empty">No operations found.</p>}
          </div>
        </div>
      )}
    </div>
  );
}

type PayloadEditorProps = {
  itemId: string;
  label: string;
  onClose: () => void;
};

function PayloadEditor({ itemId, label, onClose }: PayloadEditorProps) {
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(true);
  const [meta, setMeta] = useState<{ kind?: string; endpoint?: string; hint?: string; note?: string }>({});
  const [jsonError, setJsonError] = useState("");
  const [sending, setSending] = useState(false);
  const [copying, setCopying] = useState(false);
  const [copied, setCopied] = useState(false);
  const [result, setResult] = useState<SendCustomResult | null>(null);
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    setLoading(true);
    fetchDefaultPayload(itemId)
      .then((p) => {
        setMeta({ kind: p.kind, endpoint: p.endpoint, hint: p.hint, note: p.note });
        if (p.error) setLoadError(p.error);
        if (p.payload !== undefined) setText(JSON.stringify(p.payload, null, 2));
        else if (!p.editable) setLoadError(p.note || "This event is not editable.");
      })
      .catch((e) => setLoadError(String(e)))
      .finally(() => setLoading(false));
  }, [itemId]);

  function validate(next: string) {
    setText(next);
    try {
      JSON.parse(next);
      setJsonError("");
    } catch (e) {
      setJsonError(String(e));
    }
  }

  function parseBody(): unknown | null {
    try {
      return JSON.parse(text);
    } catch (e) {
      setJsonError(String(e));
      return null;
    }
  }

  async function send() {
    const parsed = parseBody();
    if (parsed === null) return;
    setSending(true);
    setResult(null);
    try {
      setResult(await sendCustomPayload(itemId, parsed));
    } catch (e) {
      setResult({ ok: false, detail: String(e) });
    } finally {
      setSending(false);
    }
  }

  async function copyCurl() {
    const parsed = parseBody();
    if (parsed === null) return;
    setCopying(true);
    try {
      const r = await fetchPayloadCurl(itemId, parsed);
      if (!r.curl) {
        setResult({ ok: false, detail: r.detail || "No curl for this event kind." });
        return;
      }
      await navigator.clipboard.writeText(r.curl);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch (e) {
      setResult({ ok: false, detail: String(e) });
    } finally {
      setCopying(false);
    }
  }

  return (
    <div className="payload-editor-overlay" onClick={onClose}>
      <div className="payload-editor" onClick={(e) => e.stopPropagation()}>
        <div className="payload-editor-head">
          <div>
            <strong>Edit &amp; send payload</strong>
            <span className="muted"> · {label}</span>
            {meta.kind && <span className={`kind-tag ${meta.kind}`}>{meta.kind}</span>}
          </div>
          <button type="button" className="link-btn" onClick={onClose}>close ✕</button>
        </div>
        {meta.endpoint && <div className="payload-editor-endpoint mono">→ {meta.endpoint}</div>}
        {meta.hint && <p className="muted small">{meta.hint}</p>}
        {loadError && <p className="error">{loadError}</p>}
        {loading ? (
          <p className="muted">Loading default payload…</p>
        ) : (
          <>
            <textarea
              className="payload-editor-text mono"
              value={text}
              spellCheck={false}
              onChange={(e) => validate(e.target.value)}
            />
            {jsonError && <p className="error small">Invalid JSON: {jsonError}</p>}
            <div className="actions">
              <button type="button" className="primary" disabled={sending || !!jsonError || !text} onClick={send}>
                {sending ? "Sending…" : "Send payload"}
              </button>
              {meta.kind !== "cron" && (
                <button
                  type="button"
                  disabled={copying || !!jsonError || !text}
                  onClick={copyCurl}
                >
                  {copied ? "Copied!" : copying ? "Building…" : "Copy curl"}
                </button>
              )}
            </div>
            {result && (
              <div className={`payload-result ${result.ok ? "ok" : "fail"}`}>
                <strong>{result.ok ? "✓ Sent" : "✗ Failed"}</strong>
                {result.status_code != null && <span> · HTTP {result.status_code}</span>}
                {result.correlation_id && <span> · cid {result.correlation_id.slice(0, 8)}</span>}
                {result.detail && <p>{result.detail}</p>}
                {result.response !== undefined && (
                  <pre className="mono">{JSON.stringify(result.response, null, 2).slice(0, 4000)}</pre>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export default function GeneratePage() {
  const [available, setAvailable] = useState<string[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [skipPassed, setSkipPassed] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [pipeline, setPipeline] = useState<PipelineConfig | null>(null);
  const [token, setToken] = useState<TokenStatus | null>(null);
  const [tokenBusy, setTokenBusy] = useState(false);
  const [coverage, setCoverage] = useState<CoverageReport | null>(null);
  const [opStats, setOpStats] = useState<OperationStats | null>(null);
  const [categories, setCategories] = useState<CategoryReport | null>(null);
  const [category, setCategory] = useState("all");
  const [sources, setSources] = useState<OperationSources | null>(null);
  const [sourceKinds, setSourceKinds] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [editItem, setEditItem] = useState<string | null>(null);
  const [lastRun, setLastRun] = useState<GenerateRunReport | null>(null);
  const [lastRunBusy, setLastRunBusy] = useState(false);
  const [showLastRun, setShowLastRun] = useState(false);
  const [listModal, setListModal] = useState<ListModalState | null>(null);

  useEffect(() => {
    fetchOperations().then((r) => setAvailable(r.operations));
    fetchPipelineConfig().then(setPipeline);
    fetchTokenStatus().then(setToken).catch(() => {});
    fetchCoverage().then(setCoverage).catch(() => {});
    fetchCategories().then(setCategories).catch(() => {});
    fetchOperationSources().then(setSources).catch(() => {});
    fetchOperationStats().then(setOpStats).catch(() => {});
  }, []);

  const visibleOperations = useMemo<DropdownItem[]>(() => {
    let items: DropdownItem[] =
      sources?.catalog?.map((c) => ({ id: c.id, label: c.label, kind: c.kind })) ?? [];
    // Fallback to the plain operation list before the catalog loads.
    if (!items.length) items = available.map((op) => ({ id: op, label: op, kind: "graphql" }));
    if (sourceKinds.size) {
      items = items.filter((i) => sourceKinds.has(i.kind));
    }
    if (category !== "all" && categories && sources) {
      const catById = new Map(sources.catalog.map((c) => [c.id, c.operation]));
      items = items.filter((i) => categories.by_operation[catById.get(i.id) ?? i.id] === category);
    }
    return items;
  }, [available, category, categories, sourceKinds, sources]);

  const labelById = useMemo(() => {
    const m = new Map<string, string>();
    for (const c of sources?.catalog ?? []) m.set(c.id, c.label);
    return m;
  }, [sources]);

  function toggleSourceKind(kind: string) {
    setSourceKinds((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
    // Drop selections no longer visible under the new filter.
    setSelected(new Set());
  }

  async function onRefreshToken() {
    setTokenBusy(true);
    try {
      setToken(await refreshToken());
    } finally {
      setTokenBusy(false);
    }
  }

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const pollJob = useCallback((id: string) => {
    if (pollRef.current) clearInterval(pollRef.current);
    let misses = 0;
    pollRef.current = setInterval(async () => {
      try {
        const j = await fetchJob(id);
        misses = 0;
        setJob(j);
        if (j.status === "completed" || j.status === "failed") {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          if (j.result?.token) setToken(j.result.token);
          // Ingestion is auto-started during Generate — refresh header status.
          fetchPipelineConfig().then(setPipeline).catch(() => {});
        }
      } catch {
        // Job no longer on the server (e.g. backend restarted). Stop after a few misses
        // and forget the stored id so we don't poll a dead job forever.
        misses += 1;
        if (misses >= 3) {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          localStorage.removeItem(JOB_KEY);
        }
      }
    }, 1500);
  }, []);

  // Restore the last generate/validate job across page refreshes.
  useEffect(() => {
    const savedId = localStorage.getItem(JOB_KEY);
    if (!savedId) return;
    fetchJob(savedId)
      .then((j) => {
        setJob(j);
        if (j.result?.token) setToken(j.result.token);
        if (j.status === "running" || j.status === "pending") pollJob(j.id);
      })
      .catch(() => localStorage.removeItem(JOB_KEY));
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [pollJob]);

  function toggle(op: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(op)) next.delete(op);
      else next.add(op);
      return next;
    });
  }

  async function run(validate: boolean) {
    setBusy(true);
    setError("");
    setShowLastRun(false);
    try {
      const ops = selected.size ? [...selected] : [];
      // Empty selection = full catalog (GQL+ingress+cron). When the user filters
      // by source kind or picks ingress:* ids, always allow the ingress injector.
      const wantsIngress =
        !ops.length ||
        ops.some((id) => id.startsWith("ingress:")) ||
        sourceKinds.has("ingress");
      const j = await startGenerate({
        operations: ops,
        validate,
        skip_passed: skipPassed,
        include_ingress: wantsIngress,
      });
      setJob(j);
      localStorage.setItem(JOB_KEY, j.id);
      pollJob(j.id);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const running = busy || job?.status === "running";
  const mongo = (job?.result?.mongo ?? job?.result?.generate_run) as GenerateRunReport | undefined;

  async function onShowLastRun() {
    setLastRunBusy(true);
    setShowLastRun(true);
    try {
      const res = await fetchLastGenerateRun();
      setLastRun(res.ok && res.report ? res.report : null);
      if (!res.ok) setError(res.detail || "No generation status yet — run Generate first");
    } catch (e) {
      setError(String(e));
      setLastRun(null);
    } finally {
      setLastRunBusy(false);
    }
  }

  const runReport = showLastRun && lastRun ? lastRun : mongo;

  function openFunnelList(title: string, ops: string[], note = "") {
    setListModal({
      title,
      columns: note ? ["operation", "note"] : ["operation"],
      rows: ops.map((op) => (note ? { operation: op, note } : { operation: op })),
    });
  }

  function openCoverageList(status: string, title: string) {
    const ops = (coverage?.operations ?? []).filter((o) => o.status === status);
    setListModal({
      title,
      columns: ["operation", "status", "gaps", "category"],
      rows: ops.map((o) => ({
        operation: o.operation,
        status: o.status,
        gaps: (o.gaps || []).join(", "),
        category: o.category || "",
      })),
    });
  }

  return (
    <section className="panel">
      <header className="panel-head">
        <h2>Generate events</h2>
        <p>Pick operations from the dropdown, or leave empty for all. Pipeline runs in PP preprod.</p>
      </header>

      {pipeline && !pipeline.error && (
        <div className="pipeline-info">
          <span><strong>Environment</strong> {pipeline.target?.toUpperCase()}</span>
          <span><strong>Raw queue</strong> {pipeline.raw_queue}</span>
          <span><strong>Enriched queue</strong> {pipeline.enriched_queue}</span>
          <span title="Backend RabbitMQ → Mongo dump (auto-started so Generate can poll for raw+enrich)">
            <strong>Ingestion</strong>{" "}
            {pipeline.ingestion_running ? (
              <span className="ok">running</span>
            ) : (
              <span className="warn">stopped (starts with Generate)</span>
            )}
          </span>
        </div>
      )}

      {token && (
        <div className={`token-status ${!token.present ? "missing" : token.expired ? "expired" : "valid"}`}>
          <span className="token-dot" />
          <strong>Bearer token</strong>
          {!token.present ? (
            <span>not set — paste one into <code>.env</code></span>
          ) : token.expired ? (
            <span>expired{token.can_regenerate ? " — will auto-refresh on generate" : " — no creds to refresh"}</span>
          ) : (
            <span>
              valid{token.expires_in_hours != null ? ` · expires in ${token.expires_in_hours}h` : ""}
              {token.email ? ` · ${token.email}` : ""}
            </span>
          )}
          {token.regenerated && <span className="routing-tag">auto-refreshed</span>}
          {token.matches_provided === false && <span className="routing-tag warn">identity changed</span>}
          <button type="button" className="link-btn" disabled={tokenBusy} onClick={onRefreshToken}>
            {tokenBusy ? "refreshing…" : "refresh"}
          </button>
        </div>
      )}

      {opStats && !opStats.error && (
        <div className="op-funnel" title="Click a tile to open the operation list. An op is validatable only when raw+enriched share an xCorrelationId (or fingerprint match).">
          <button
            type="button"
            className="funnel-step funnel-cta"
            onClick={() => openFunnelList("Tracked operations", opStats.tracked_operations ?? available)}
          >
            <div className="funnel-num">{opStats.tracked ?? "—"}</div>
            <div className="funnel-label">tracked operations</div>
          </button>
          <button
            type="button"
            className="funnel-step funnel-cta"
            onClick={() => openFunnelList("In raw + enriched", opStats.in_both_operations ?? [])}
          >
            <div className="funnel-num">{opStats.in_both}</div>
            <div className="funnel-label">in raw + enriched</div>
          </button>
          <button
            type="button"
            className="funnel-step funnel-cta"
            onClick={() => openFunnelList("True pairs (validatable)", opStats.paired_operations ?? [])}
          >
            <div className="funnel-num">{opStats.true_pairs}</div>
            <div className="funnel-label">true pairs (validatable)</div>
          </button>
          <button
            type="button"
            className="funnel-step funnel-cta"
            onClick={() => openFunnelList("Raw only (no enrich)", opStats.raw_only ?? [], "raw in Mongo; enrich missing")}
          >
            <div className="funnel-num">{opStats.raw_only.length}</div>
            <div className="funnel-label">raw only (no enrich)</div>
          </button>
          <button
            type="button"
            className="funnel-step funnel-cta"
            onClick={() => openFunnelList("Enriched only (no raw)", opStats.enriched_only ?? [], "enrich in Mongo; raw missing")}
          >
            <div className="funnel-num">{opStats.enriched_only.length}</div>
            <div className="funnel-label">enriched only (no raw)</div>
          </button>
        </div>
      )}

      {coverage && !coverage.error && (
        <div className="coverage-badge">
          <strong>Validation coverage</strong>
          <button type="button" className="link-btn ok" onClick={() => openCoverageList("complete", "Complete mapping")}>
            {coverage.summary.complete ?? 0} complete
          </button>
          <button type="button" className="link-btn warn" onClick={() => openCoverageList("needs_mapping", "Need mapping")}>
            {coverage.summary.needs_mapping ?? 0} need mapping
          </button>
          <button type="button" className="link-btn warn" onClick={() => openCoverageList("needs_template", "Need template")}>
            {coverage.summary.needs_template ?? 0} need template
          </button>
          <button type="button" className="link-btn muted" onClick={() => openCoverageList("unmapped", "Unmapped")}>
            {coverage.summary.unmapped ?? 0} unmapped
          </button>
          <span className="muted">/ {coverage.total} tracked</span>
        </div>
      )}

      <OpListModal state={listModal} onClose={() => setListModal(null)} />

      <div className="source-kind-filter">
        <span className="muted">Type</span>
        {SOURCE_KINDS.map((k) => {
          const n = sources?.counts?.[k.id] ?? 0;
          return (
            <label key={k.id} className="checkbox source-kind">
              <input
                type="checkbox"
                checked={sourceKinds.has(k.id)}
                onChange={() => toggleSourceKind(k.id)}
              />
              {k.label}{n ? ` (${n})` : ""}
            </label>
          );
        })}
        {sourceKinds.size > 0 && (
          <button type="button" className="link-btn" onClick={() => { setSourceKinds(new Set()); setSelected(new Set()); }}>
            reset
          </button>
        )}
      </div>

      <div className="generate-controls">
        <label className="category-select">
          Category
          <select value={category} onChange={(e) => { setCategory(e.target.value); setSelected(new Set()); }}>
            <option value="all">All categories ({visibleOperations.length})</option>
            {(categories?.categories ?? []).map((c) => {
              const n = categories?.counts?.[c] ?? 0;
              return n > 0 ? (
                <option key={c} value={c}>{c} ({n})</option>
              ) : null;
            })}
          </select>
        </label>
        <OperationDropdown
          options={visibleOperations}
          selected={selected}
          onToggle={toggle}
          onSelectAll={() => setSelected(new Set(visibleOperations.map((i) => i.id)))}
          onClear={() => setSelected(new Set())}
        />
        <label className="checkbox">
          <input type="checkbox" checked={skipPassed} onChange={(e) => setSkipPassed(e.target.checked)} />
          Skip already-passed operations
        </label>
      </div>

      {selected.size > 0 && (
        <div className="selected-chips">
          {[...selected].map((id) => (
            <span key={id} className="chip selected chip-with-edit">
              <button type="button" className="chip-edit" title="Edit & send payload" onClick={() => setEditItem(id)}>
                ✎
              </button>
              <button type="button" className="chip-remove" onClick={() => toggle(id)}>
                {labelById.get(id) ?? id} ✕
              </button>
            </span>
          ))}
        </div>
      )}

      {editItem && (
        <PayloadEditor
          itemId={editItem}
          label={labelById.get(editItem) ?? editItem}
          onClose={() => setEditItem(null)}
        />
      )}

      <div className="actions">
        <button type="button" className="primary" disabled={running} onClick={() => run(false)}>
          {running ? "Running…" : "Generate"}
        </button>
        <button type="button" className="primary outline" disabled={running} onClick={() => run(true)}>
          {running ? "Running…" : "Generate & validate"}
        </button>
        <button type="button" className="primary outline" disabled={lastRunBusy} onClick={onShowLastRun}>
          {lastRunBusy ? "Loading…" : "Generation Status"}
        </button>
        {job && <span className={`status-pill ${job.status}`}>{job.status}</span>}
      </div>

      {error && <p className="error">{error}</p>}

      {runReport && (job?.status === "completed" || job?.status === "failed" || showLastRun) && (
        <div className="generate-run-status">
          <div className="mongo-status">
            <strong>Generation Status</strong>
            <span className="ok">PASS: {runReport.summary?.pass ?? runReport.summary?.success ?? 0}</span>
            <span className="warn">FAIL: {runReport.summary?.fail ?? runReport.summary?.needs_work ?? 0}</span>
            <span className="muted">N/A: {runReport.summary?.na ?? 0}</span>
            {runReport.summary?.total != null && (
              <span className="muted">/ {runReport.summary.total}</span>
            )}
            {(runReport.summary?.fingerprint_matched ?? 0) > 0 && (
              <span className="muted" title="Matched without xCorrelationId on the envelope">
                fingerprint: {runReport.summary?.fingerprint_matched}
              </span>
            )}
          </div>
          <div className="result-table-wrap compact-table-wrap generate-status-table">
            <table className="result-table">
              <thead>
                <tr>
                  <th>Operation</th>
                  <th>Status</th>
                  <th>Raw</th>
                  <th>Enrich</th>
                  <th>Match</th>
                  <th>Remark</th>
                </tr>
              </thead>
              <tbody>
                {(runReport.operations || []).map((o) => {
                  const ui = o.ui_status || (o.status === "success" ? "PASS" : o.status === "no_correlation" ? "N/A" : "FAIL");
                  return (
                    <tr key={o.operation}>
                      <td><code>{o.operation}</code></td>
                      <td><span className={`status-pill ${ui === "PASS" ? "completed" : ui === "N/A" ? "pending" : "failed"}`}>{ui}</span></td>
                      <td>{o.raw ? "✓" : "—"}</td>
                      <td>{o.enriched ? "✓" : "—"}</td>
                      <td className="muted">{o.pairing_method || (o.xCorrelationId ? "owned_cid" : "—")}</td>
                      <td className="remark-cell">{o.remark || o.status}{o.xCorrelationId ? ` · cid ${String(o.xCorrelationId).slice(0, 8)}` : ""}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {job && (
        <div className="log-box">
          <div className="log-head">
            <strong>Job {job.id.slice(0, 8)}</strong>
            {!!job.params.validate && <span className="routing-tag">generate + validate</span>}
            {job.error && <span className="error">{job.error}</span>}
            {job.result?.exit_code !== undefined && <span>exit {job.result.exit_code}</span>}
          </div>
          <pre className="job-logs">{job.logs.join("\n") || "Waiting for logs…"}</pre>
        </div>
      )}
    </section>
  );
}
