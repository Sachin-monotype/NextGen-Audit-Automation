import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import JsonTree from "../components/JsonTree";
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
  setPipelineTarget,
  sendCustomPayload,
  startGenerate,
  type CategoryReport,
  type CoverageReport,
  type DefaultPayload,
  type GenerateRunOpStatus,
  type GenerateRunReport,
  type GenerateScenarioStatus,
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

type DropdownItem = {
  id: string;
  label: string;
  kind: string;
  operation?: string;
  touchpoint?: string | null;
  steps?: string[] | null;
};

type ListModalState = {
  title: string;
  columns: string[];
  rows: Array<Record<string, string | number>>;
};

function csvEscape(v: unknown): string {
  const s = v == null ? "" : String(v);
  if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

function downloadCsv(filename: string, headers: string[], rows: string[][]) {
  const lines = [headers.map(csvEscape).join(",")];
  for (const row of rows) lines.push(row.map(csvEscape).join(","));
  const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function EventJsonCell({
  label,
  present,
  data,
}: {
  label: string;
  present?: boolean;
  data?: Record<string, unknown> | null;
}) {
  const [open, setOpen] = useState(false);
  if (!present && !data) return <span className="muted">—</span>;
  return (
    <div className="event-json-cell">
      <button type="button" className="link-btn" onClick={() => setOpen((o) => !o)}>
        {present ? "✓" : "—"} {label} {open ? "▴" : "▾"}
      </button>
      {open && (
        <div className="event-json-panel">
          {data ? <JsonTree data={data} defaultOpen={false} /> : <span className="muted">No JSON captured</span>}
        </div>
      )}
    </div>
  );
}

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

type OpGroup = {
  key: string;
  operation: string;
  kind: string;
  children: DropdownItem[];
};

function OperationDropdown({ options, selected, onToggle, onSelectAll, onClear }: OperationDropdownProps) {
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

  const q = search.toLowerCase();

  const groups = useMemo(() => {
    const byOp = new Map<string, OpGroup>();
    const flat: DropdownItem[] = [];
    for (const o of options) {
      const opName = o.operation || o.id;
      const hasTouch = Boolean(o.touchpoint) || o.id.includes("::");
      if (o.kind === "graphql" && hasTouch) {
        const g = byOp.get(opName) || {
          key: opName,
          operation: opName,
          kind: "graphql",
          children: [],
        };
        g.children.push(o);
        byOp.set(opName, g);
      } else {
        flat.push(o);
      }
    }
    const grouped = [...byOp.values()].sort((a, b) => a.operation.localeCompare(b.operation));
    return { grouped, flat };
  }, [options]);

  const filteredGroups = useMemo(() => {
    if (!q) return groups;
    const grouped = groups.grouped
      .map((g) => {
        if (g.operation.toLowerCase().includes(q)) return g;
        const children = g.children.filter(
          (c) =>
            c.label.toLowerCase().includes(q) ||
            (c.touchpoint || "").toLowerCase().includes(q),
        );
        return children.length ? { ...g, children } : null;
      })
      .filter(Boolean) as OpGroup[];
    const flat = groups.flat.filter((o) => o.label.toLowerCase().includes(q));
    return { grouped, flat };
  }, [groups, q]);

  const shownCount =
    filteredGroups.grouped.reduce((n, g) => n + g.children.length, 0) + filteredGroups.flat.length;

  const label = selected.size
    ? `${selected.size} operation${selected.size > 1 ? "s" : ""} selected`
    : "All operations";

  function toggleGroup(g: OpGroup) {
    const ids = g.children.map((c) => c.id);
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

  useEffect(() => {
    if (!q) return;
    setExpanded(new Set(filteredGroups.grouped.map((g) => g.key)));
  }, [q, filteredGroups.grouped]);

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
            <span className="muted">{shownCount} shown</span>
          </div>
          <div className="op-dropdown-list">
            {filteredGroups.grouped.map((g) => {
              const ids = g.children.map((c) => c.id);
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
                        <strong>{g.operation}</strong>
                        <span className="muted"> · {g.children.length}</span>
                      </span>
                    </label>
                  </div>
                  {isOpen && (
                    <div className="op-dropdown-children">
                      {g.children.map((o) => (
                        <label key={o.id} className="op-dropdown-item nested">
                          <input
                            type="checkbox"
                            checked={selected.has(o.id)}
                            onChange={() => onToggle(o.id)}
                          />
                          <span>{o.touchpoint || o.label}</span>
                          {(o.steps?.length ?? 0) > 1 && (
                            <span className="muted steps-hint" title={o.steps?.join(" → ")}>
                              {o.steps!.length} steps
                            </span>
                          )}
                        </label>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
            {filteredGroups.flat.map((o) => (
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
            {shownCount === 0 && <p className="muted op-dropdown-empty">No operations found.</p>}
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
  onGenerateScenario?: (id: string) => void;
};

function PayloadEditor({ itemId, label, onClose, onGenerateScenario }: PayloadEditorProps) {
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(true);
  const [meta, setMeta] = useState<Partial<DefaultPayload>>({});
  const [jsonError, setJsonError] = useState("");
  const [sending, setSending] = useState(false);
  const [copying, setCopying] = useState(false);
  const [copied, setCopied] = useState(false);
  const [result, setResult] = useState<SendCustomResult | null>(null);
  const [loadError, setLoadError] = useState("");
  const [curlText, setCurlText] = useState("");
  const [correlationId, setCorrelationId] = useState("");

  useEffect(() => {
    setLoading(true);
    fetchDefaultPayload(itemId)
      .then((p) => {
        setMeta(p);
        setCorrelationId(p.correlation_id || "");
        if (p.error) setLoadError(p.error);
        if (p.payload !== undefined) {
          setText(JSON.stringify(p.payload, null, 2));
          if (p.kind !== "cron") {
            fetchPayloadCurl(itemId, p.payload, p.correlation_id)
              .then((r) => setCurlText(r.curl || ""))
              .catch(() => {});
          }
        } else if (!p.editable) setLoadError(p.note || "This event is not editable.");
      })
      .catch((e) => setLoadError(String(e)))
      .finally(() => setLoading(false));
  }, [itemId]);

  function validateJson(next: string) {
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
      setCurlText(r.curl);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch (e) {
      setResult({ ok: false, detail: String(e) });
    } finally {
      setCopying(false);
    }
  }

  const flow = meta.flow;
  const multiStep = (flow?.steps?.length ?? 0) > 1;

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
        {correlationId && (
          <div className="payload-editor-cid">
            <strong>x-correlation-id</strong>
            <code>{correlationId}</code>
            <button type="button" className="link-btn" onClick={() => navigator.clipboard.writeText(correlationId)}>
              copy
            </button>
          </div>
        )}
        {meta.hint && <p className="muted small">{meta.hint}</p>}
        {multiStep && flow && (
          <details className="flow-preview" open>
            <summary>
              Dependency flow ({flow.steps!.length} steps) — same as Generate / UI Navigation sheet
            </summary>
            <ol className="flow-steps">
              {flow.step_payloads?.map((s, i) => (
                <li key={`${s.operation}-${i}`} className={s.is_trigger ? "trigger-step" : ""}>
                  <div>
                    <code>{s.operation}</code>
                    {s.is_trigger && <span className="routing-tag">trigger</span>}
                  </div>
                  <pre className="input-preview">{JSON.stringify(s.variables || {}, null, 2)}</pre>
                </li>
              ))}
            </ol>
            {flow.note && <p className="muted small">{flow.note}</p>}
            {onGenerateScenario && (
              <button
                type="button"
                className="primary"
                onClick={() => {
                  onGenerateScenario(itemId);
                  onClose();
                }}
              >
                Generate full flow (create → seed → trigger)
              </button>
            )}
          </details>
        )}
        {loadError && <p className="error">{loadError}</p>}
        {loading ? (
          <p className="muted">Loading default payload…</p>
        ) : (
          <>
            <textarea
              className="payload-editor-text mono"
              value={text}
              spellCheck={false}
              onChange={(e) => validateJson(e.target.value)}
            />
            {jsonError && <p className="error small">Invalid JSON: {jsonError}</p>}
            {curlText && (
              <details className="payload-curl-preview" open={!multiStep}>
                <summary>Exact curl for final trigger (expects IDs already created)</summary>
                <pre className="curl-block">{curlText}</pre>
              </details>
            )}
            <div className="actions">
              <button type="button" className="primary" disabled={sending || !!jsonError || !text} onClick={send}>
                {sending ? "Sending…" : "Send trigger only"}
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
  const [targetBusy, setTargetBusy] = useState(false);
  const logRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    fetchOperations().then((r) => setAvailable(r.operations));
    fetchPipelineConfig().then(setPipeline);
    fetchTokenStatus().then(setToken).catch(() => {});
    fetchCoverage().then(setCoverage).catch(() => {});
    fetchCategories().then(setCategories).catch(() => {});
    fetchOperationSources().then(setSources).catch(() => {});
    fetchOperationStats().then(setOpStats).catch(() => {});
  }, []);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [job?.logs]);

  const visibleOperations = useMemo<DropdownItem[]>(() => {
    let items: DropdownItem[] =
      sources?.catalog?.map((c) => ({
        id: c.id,
        label: c.label,
        kind: c.kind,
        operation: c.operation,
        touchpoint: c.touchpoint,
        steps: c.steps,
      })) ?? [];
    if (!items.length) items = available.map((op) => ({ id: op, label: op, kind: "graphql", operation: op }));
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

  async function onTargetChange(target: string) {
    setTargetBusy(true);
    setError("");
    try {
      setPipeline(await setPipelineTarget(target));
      setToken(await fetchTokenStatus());
    } catch (e) {
      setError(String(e));
    } finally {
      setTargetBusy(false);
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
          fetchPipelineConfig().then(setPipeline).catch(() => {});
        }
      } catch {
        misses += 1;
        if (misses >= 3) {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          localStorage.removeItem(JOB_KEY);
        }
      }
    }, 1500);
  }, []);

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

  async function run(validate: boolean, opsOverride?: string[]) {
    setBusy(true);
    setError("");
    setShowLastRun(false);
    try {
      const ops = opsOverride ?? (selected.size ? [...selected] : []);
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
  const isValidateMode = Boolean(
    runReport?.validate ?? (showLastRun ? lastRun?.validate : job?.params?.validate),
  );

  function exportStatusCsv() {
    if (!runReport) return;
    if (runReport.scenarios?.length) {
      const headers = isValidateMode
        ? ["operation", "touchpoint", "status", "raw", "enrich", "xCorrelationId", "steps", "error"]
        : ["operation", "touchpoint", "raw", "enrich", "xCorrelationId", "steps"];
      const rows = runReport.scenarios.map((s: GenerateScenarioStatus) =>
        isValidateMode
          ? [
              s.operation,
              s.touchpoint,
              s.status,
              s.raw ? "yes" : "no",
              s.enriched ? "yes" : "no",
              s.xCorrelationId || "",
              (s.steps || []).join(" > "),
              s.error || "",
            ]
          : [
              s.operation,
              s.touchpoint,
              s.raw ? "yes" : "no",
              s.enriched ? "yes" : "no",
              s.xCorrelationId || "",
              (s.steps || []).join(" > "),
            ],
      );
      downloadCsv(`generate-status-${isValidateMode ? "validate" : "generate"}.csv`, headers, rows);
      return;
    }
    const headers = isValidateMode
      ? ["operation", "status", "raw", "enrich", "match", "remark", "xCorrelationId"]
      : ["operation", "raw", "enrich", "xCorrelationId"];
    const rows = (runReport.operations || []).map((o: GenerateRunOpStatus) => {
      const ui = o.ui_status || (o.status === "success" ? "PASS" : o.status === "no_correlation" ? "N/A" : "FAIL");
      return isValidateMode
        ? [
            o.operation,
            ui,
            o.raw ? "yes" : "no",
            o.enriched ? "yes" : "no",
            o.pairing_method || "",
            o.remark || o.status || "",
            o.xCorrelationId || "",
          ]
        : [o.operation, o.raw ? "yes" : "no", o.enriched ? "yes" : "no", o.xCorrelationId || ""];
    });
    downloadCsv(`generate-status-${isValidateMode ? "validate" : "generate"}.csv`, headers, rows);
  }

  function openAllCoverage() {
    setListModal({
      title: "Validation coverage by event",
      columns: ["operation", "status", "gaps", "category"],
      rows: (coverage?.operations ?? []).map((o) => ({
        operation: o.operation,
        status: o.status,
        gaps: (o.gaps || []).join(", ") || "—",
        category: o.category || "",
      })),
    });
  }

  function openQueueCoverage() {
    if (!opStats) return;
    const rawOnly = new Set(opStats.raw_only ?? []);
    const enrichOnly = new Set(opStats.enriched_only ?? []);
    const paired = new Set(opStats.paired_operations ?? []);
    const both = new Set(opStats.in_both_operations ?? []);
    const ops = opStats.tracked_operations ?? available;
    setListModal({
      title: "Raw / enriched event coverage",
      columns: ["operation", "queue status"],
      rows: ops.map((operation) => ({
        operation,
        "queue status": paired.has(operation)
          ? "validatable pair"
          : rawOnly.has(operation)
            ? "raw only"
            : enrichOnly.has(operation)
              ? "enriched only"
              : both.has(operation)
                ? "raw + enriched (not paired)"
                : "not observed",
      })),
    });
  }

  return (
    <section className="panel">
      <header className="panel-head compact-page-head">
        <h2>Generate events</h2>
        <p>Choose a scenario, generate it, then inspect its exact input and correlation ID.</p>
      </header>

      <div className="generate-context-bar">
        {pipeline && !pipeline.error && (
          <>
            <label className="inline-control">
              Environment
              <select
                value={pipeline.target || "pp"}
                disabled={targetBusy || running}
                onChange={(e) => onTargetChange(e.target.value)}
              >
                {(pipeline.available_targets ?? []).map((t) => (
                  <option key={t.id} value={t.id}>{t.label}</option>
                ))}
              </select>
            </label>
            {pipeline.nextgen_url && (
              <a href={pipeline.nextgen_url} target="_blank" rel="noreferrer" className="context-link">
                Open NextGen ↗
              </a>
            )}
            <a
              href={pipeline.raw_queue_url || "#"}
              target="_blank"
              rel="noreferrer"
              className={`context-link${pipeline.raw_queue_url ? "" : " disabled"}`}
              title={pipeline.raw_queue}
            >
              Raw queue ↗
            </a>
            <a
              href={pipeline.enriched_queue_url || "#"}
              target="_blank"
              rel="noreferrer"
              className={`context-link${pipeline.enriched_queue_url ? "" : " disabled"}`}
              title={pipeline.enriched_queue}
            >
              Enriched queue ↗
            </a>
            <span className={pipeline.ingestion_running ? "ok" : "warn"}>
              ● ingestion {pipeline.ingestion_running ? "running" : "stopped"}
            </span>
            {pipeline.queue_warning && (
              <span className="warn" title={pipeline.queue_warning}>⚠ queues: {pipeline.queue_environment?.toUpperCase()}</span>
            )}
          </>
        )}
        {coverage && !coverage.error && (
          <button type="button" onClick={openAllCoverage}>
            Validation coverage ({coverage.summary.complete ?? 0}/{coverage.total})…
          </button>
        )}
        {opStats && !opStats.error && (
          <button type="button" onClick={openQueueCoverage}>
            Raw/enrich coverage ({opStats.true_pairs ?? 0} paired)…
          </button>
        )}
        <a
          className="context-link"
          href="/api/meta/ui-navigation-mapping.xlsx"
          download
        >
          UI Navigation mapping.xlsx ↗
        </a>
        {token && (
          <span className={`compact-token ${!token.present || token.expired ? "warn" : "ok"}`}>
            ● token {!token.present ? "missing" : token.expired ? "expired" : "valid"}
            {token.expires_in_hours != null && !token.expired ? ` ${token.expires_in_hours}h` : ""}
            <button type="button" className="link-btn" disabled={tokenBusy} onClick={onRefreshToken}>
              {tokenBusy ? "…" : "refresh"}
            </button>
          </span>
        )}
      </div>

      <OpListModal state={listModal} onClose={() => setListModal(null)} />

      <div className="generate-filter-row">
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
        </div>
        <label className="category-select inline-control">
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
        <label className="checkbox compact-checkbox">
          <input type="checkbox" checked={skipPassed} onChange={(e) => setSkipPassed(e.target.checked)} />
          Skip passed
        </label>
        {(sourceKinds.size > 0 || category !== "all") && (
          <button type="button" onClick={() => { setSourceKinds(new Set()); setCategory("all"); setSelected(new Set()); }}>
            Reset filters
          </button>
        )}
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
          onGenerateScenario={(id) => run(false, [id])}
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

      {job && (
        <div className="log-box generation-log-box">
          <div className="log-head">
            <strong>Live generation log · Job {job.id.slice(0, 8)}</strong>
            {!!job.params.validate && <span className="routing-tag">generate + validate</span>}
            <span className={`status-pill ${job.status}`}>{job.status}</span>
            {job.error && <span className="error">{job.error}</span>}
            {job.result?.exit_code !== undefined && <span>exit {job.result.exit_code}</span>}
          </div>
          <pre ref={logRef} className="job-logs">{job.logs.join("\n") || "Waiting for logs…"}</pre>
        </div>
      )}

      {runReport && (job?.status === "completed" || job?.status === "failed" || showLastRun) && (
        <div className="generate-run-status">
          <div className="mongo-status">
            <strong>Generation Status</strong>
            <span className="routing-tag">{isValidateMode ? "generate + validate" : "generate only"}</span>
            {isValidateMode && (
              <>
                <span className="ok">PASS: {runReport.summary?.pass ?? runReport.summary?.success ?? 0}</span>
                <span className="warn">FAIL: {runReport.summary?.fail ?? runReport.summary?.needs_work ?? 0}</span>
                <span className="muted">N/A: {runReport.summary?.na ?? 0}</span>
              </>
            )}
            {!isValidateMode && (
              <span className="muted">
                {runReport.scenarios?.length ?? runReport.operations?.length ?? 0} event(s) · raw/enrich landing only
              </span>
            )}
            {runReport.summary?.total != null && isValidateMode && (
              <span className="muted">/ {runReport.summary.total}</span>
            )}
            <button type="button" className="link-btn" onClick={exportStatusCsv}>
              Export CSV
            </button>
          </div>
          {(runReport.scenarios?.length ?? 0) > 0 && (
            <div className="result-table-wrap compact-table-wrap generate-status-table">
              <table className="result-table scenario-status-table">
                <thead>
                  <tr>
                    <th>Scenario / touchpoint</th>
                    {isValidateMode && <th>Status</th>}
                    <th>Raw</th>
                    <th>Enrich</th>
                    <th>x-correlation-id</th>
                    <th>Flow / input</th>
                  </tr>
                </thead>
                <tbody>
                  {runReport.scenarios?.map((s) => (
                    <tr key={`${s.scenario_id}-${s.xCorrelationId || ""}`}>
                      <td>
                        <code>{s.operation}</code>
                        <div className="muted">{s.touchpoint}</div>
                        {(s.steps?.length ?? 0) > 0 && (
                          <div className="muted small">{s.steps!.join(" → ")}</div>
                        )}
                      </td>
                      {isValidateMode && (
                        <td>
                          <span className={`status-pill ${s.status === "PASS" ? "completed" : "failed"}`}>
                            {s.status}
                          </span>
                          {s.error && <div className="error small">{s.error}</div>}
                        </td>
                      )}
                      <td>
                        <EventJsonCell label="raw" present={s.raw} data={s.raw_event} />
                      </td>
                      <td>
                        <EventJsonCell label="enrich" present={s.enriched} data={s.enriched_event} />
                      </td>
                      <td className="cid-cell">
                        {s.xCorrelationId ? (
                          <>
                            <code>{s.xCorrelationId}</code>
                            <button
                              type="button"
                              className="link-btn"
                              onClick={() => navigator.clipboard.writeText(String(s.xCorrelationId))}
                            >
                              copy
                            </button>
                          </>
                        ) : "—"}
                      </td>
                      <td><pre className="input-preview">{JSON.stringify(s.input || {}, null, 2)}</pre></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <details className="operation-summary-details" open={!runReport.scenarios?.length}>
            <summary>Operation-level Mongo summary</summary>
            <div className="result-table-wrap compact-table-wrap generate-status-table">
              <table className="result-table">
                <thead>
                  <tr>
                    <th>Operation</th>
                    {isValidateMode && <th>Status</th>}
                    <th>Raw</th>
                    <th>Enrich</th>
                    {isValidateMode && <th>Match</th>}
                    {isValidateMode && <th>Remark</th>}
                    {!isValidateMode && <th>x-correlation-id</th>}
                  </tr>
                </thead>
                <tbody>
                  {(runReport.operations || []).map((o, index) => {
                    const ui = o.ui_status || (o.status === "success" ? "PASS" : o.status === "no_correlation" ? "N/A" : "FAIL");
                    return (
                      <tr key={`${o.operation}-${o.xCorrelationId || index}`}>
                        <td><code>{o.operation}</code></td>
                        {isValidateMode && (
                          <td><span className={`status-pill ${ui === "PASS" ? "completed" : ui === "N/A" ? "pending" : "failed"}`}>{ui}</span></td>
                        )}
                        <td>
                          <EventJsonCell label="raw" present={o.raw} data={o.raw_event} />
                        </td>
                        <td>
                          <EventJsonCell label="enrich" present={o.enriched} data={o.enriched_event} />
                        </td>
                        {isValidateMode && (
                          <td className="muted">{o.pairing_method || (o.xCorrelationId ? "owned_cid" : "—")}</td>
                        )}
                        {isValidateMode && (
                          <td className="remark-cell">
                            {o.remark || o.status}
                            {o.xCorrelationId && (
                              <>
                                <div><code>{String(o.xCorrelationId)}</code></div>
                                <button type="button" className="link-btn" onClick={() => navigator.clipboard.writeText(String(o.xCorrelationId))}>copy cid</button>
                              </>
                            )}
                          </td>
                        )}
                        {!isValidateMode && (
                          <td className="cid-cell">
                            {o.xCorrelationId ? <code>{o.xCorrelationId}</code> : "—"}
                          </td>
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </details>
        </div>
      )}
    </section>
  );
}
