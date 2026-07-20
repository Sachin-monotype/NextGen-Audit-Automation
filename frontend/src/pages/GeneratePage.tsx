import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import JsonTree from "../components/JsonTree";
import EnrichDiffModal from "../components/EnrichDiffModal";
import GenerateInUiModal from "../components/GenerateInUiModal";
import VerifyInUiModal, { type VerifyInUiContext } from "../components/VerifyInUiModal";
import {
  fetchCategories,
  fetchCoverage,
  fetchDefaultPayload,
  fetchGenerateInUi,
  fetchJob,
  fetchLastGenerateRun,
  fetchOperations,
  fetchOperationSources,
  fetchOperationStats,
  fetchPayloadCurl,
  fetchPipelineConfig,
  fetchTokenStatus,
  refreshGenerateInUi,
  cancelGenerateInUi,
  refreshToken,
  applyTokenCredentials,
  recordGenerateInUiResults,
  setPipelineTarget,
  setPipelineQueues,
  sendCustomPayload,
  startCompare,
  startGenerate,
  verifyGenerateInUi,
  type CategoryReport,
  type CoverageReport,
  type DefaultPayload,
  type GenerateRunReport,
  type GenerateScenarioStatus,
  type Job,
  type OperationSources,
  type OperationStats,
  type PipelineConfig,
  type SendCustomResult,
  type TokenStatus,
  type UiTriggerJob,
} from "../api";

const SOURCE_KINDS = [
  { id: "graphql", label: "GraphQL" },
  { id: "ingress", label: "Ingress" },
  { id: "cron", label: "Cron" },
] as const;

const DEFAULT_TARGETS = [
  { id: "pp", label: "PP", url: "https://nextgen.monotype-pp.com" },
  { id: "qa", label: "QA", url: "https://nextgen-qa.monotype-pp.com" },
  { id: "uat", label: "UAT", url: "https://nextgen.monotype-uat.com" },
];

const JOB_KEY = "audit-generate-job";
const UI_JOB_KEY = "audit-generate-ui-job";

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
  title,
}: {
  label: string;
  present?: boolean;
  data?: Record<string, unknown> | null;
  title?: string;
}) {
  const [open, setOpen] = useState(false);
  if (!present && !data) return <span className="muted">—</span>;
  return (
    <div className="event-json-cell">
      <button type="button" className="link-btn" onClick={() => setOpen(true)}>
        {present ? "✓" : "—"} {label}
      </button>
      {open && (
        <div className="modal-backdrop" onClick={() => setOpen(false)} role="presentation">
          <div
            className="modal-card event-json-modal"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-label={`${label} JSON`}
          >
            <div className="modal-head">
              <strong>
                {title || label} JSON
              </strong>
              <button type="button" className="link-btn" onClick={() => setOpen(false)}>
                close ✕
              </button>
            </div>
            <div className="event-json-modal-body">
              {data ? (
                <JsonTree data={data} defaultOpen={false} />
              ) : (
                <span className="muted">No JSON captured</span>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/** Compact touchpoint for status/CSV: activateFamily(global), activateFamily(list), … */
function shortTouchpoint(touch?: string | null): string {
  if (!touch) return "";
  const t = touch.toLowerCase().replace(/\//g, " ").replace(/>/g, " ").replace(/\s+/g, " ").trim();
  if ((t.includes("search") && t.includes("discover")) || t.includes("discover") || t.includes("browse") || t === "global") {
    if (!t.includes("list") && !t.includes("project")) return "global";
  }
  if (t.includes("favourite") || t.includes("favorite")) return "favourite";
  if (t.includes("project") && t.includes("list")) return "project_list";
  if (t === "project" || t.startsWith("project ")) return "project";
  if (t.includes("list") || t.includes("fontlist")) return "list";
  return t.replace(/\s+/g, "_") || "global";
}

function isPlaceholderScenario(operation?: string | null, touchpoint?: string | null): boolean {
  for (const part of [operation, touchpoint]) {
    if (!part) continue;
    const s = String(part).trim();
    if (!s) continue;
    const low = s.toLowerCase();
    if (["op", "touch", "operation", "touchpoint", "uuid", "value", "your-uuid"].includes(low)) {
      return true;
    }
    // Only angle-bracket templates (<op>), not touchpoints like "Project > List"
    if (/<[^>]+>/.test(s)) return true;
  }
  return false;
}

function scenarioDisplayName(
  operation: string,
  touchpoint?: string | null,
  opts?: { ui?: boolean; be?: boolean },
): string {
  const short = shortTouchpoint(touchpoint);
  let base = short ? `${operation}(${short})` : operation;
  for (const legacy of ["(ui)", "(be)", "(UI)", "(BE)"]) {
    if (base.endsWith(legacy)) base = base.slice(0, -legacy.length);
  }
  if (opts?.ui) return `${base}(UI)`;
  if (opts?.be) return `${base}(BE)`;
  return base;
}

type ScenarioRow = GenerateScenarioStatus;

/** Collapse Search/Family/Discovery + Discovery/Browse into one row per short label. */
function dedupeScenarioRows(scenarios: ScenarioRow[]): ScenarioRow[] {
  const byKey = new Map<string, ScenarioRow>();
  for (const s of scenarios) {
    if (isPlaceholderScenario(s.operation, s.touchpoint)) continue;
    const ui = String(s.source || "").toLowerCase() === "ui" || String((s as { kind?: string }).kind || "").includes("ui");
    const key = scenarioDisplayName(s.operation, s.touchpoint, { ui, be: !ui });
    const prev = byKey.get(key);
    if (!prev) {
      byKey.set(key, { ...s, label: key });
      continue;
    }
    // Prefer the row that actually landed raw/enrich JSON
    const prevScore = (prev.raw ? 2 : 0) + (prev.enriched ? 1 : 0) + (prev.status === "PASS" ? 1 : 0);
    const nextScore = (s.raw ? 2 : 0) + (s.enriched ? 1 : 0) + (s.status === "PASS" ? 1 : 0);
    if (nextScore > prevScore) byKey.set(key, { ...s, label: key });
  }
  return [...byKey.values()];
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
                          <span>{shortTouchpoint(o.touchpoint) || o.touchpoint || o.label}</span>
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

export default function GeneratePage({
  onCompareCompleted,
}: {
  onCompareCompleted?: (jobId: string) => void;
} = {}) {
  const [available, setAvailable] = useState<string[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [skipPassed, setSkipPassed] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [pipeline, setPipeline] = useState<PipelineConfig | null>(null);
  const [token, setToken] = useState<TokenStatus | null>(null);
  const [tokenBusy, setTokenBusy] = useState(false);
  const [credOpen, setCredOpen] = useState(false);
  const [credBusy, setCredBusy] = useState(false);
  const [credError, setCredError] = useState("");
  const [credForm, setCredForm] = useState({
    username: "",
    password: "",
    org: "",
    gcid: "",
  });
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
  const [verifyCtx, setVerifyCtx] = useState<VerifyInUiContext | null>(null);
  const [targetBusy, setTargetBusy] = useState(false);
  const [queueDraft, setQueueDraft] = useState({ raw: "", enriched: "" });
  const [queueBusy, setQueueBusy] = useState(false);
  const [queueOpen, setQueueOpen] = useState(false);
  const [enrichPick, setEnrichPick] = useState<string[]>([]);
  const [exportPick, setExportPick] = useState<string[]>([]);
  const [compareBusy, setCompareBusy] = useState(false);
  const [enrichDiff, setEnrichDiff] = useState<{
    labelA: string;
    labelB: string;
    dataA: unknown;
    dataB: unknown;
  } | null>(null);
  const [uiTriggerOpen, setUiTriggerOpen] = useState(false);
  const [uiJob, setUiJob] = useState<UiTriggerJob | null>(null);
  const [uiManualCid, setUiManualCid] = useState("");
  const [uiBusy, setUiBusy] = useState(false);
  const logRef = useRef<HTMLPreElement>(null);
  const uiLogRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    fetchOperations().then((r) => setAvailable(r.operations));
    fetchPipelineConfig().then((p) => {
      setPipeline(p);
      setQueueDraft({
        raw: p.raw_queue || "",
        enriched: p.enriched_queue || "",
      });
    });
    fetchTokenStatus().then(setToken).catch(() => {});
    fetchCoverage().then(setCoverage).catch(() => {});
    fetchCategories().then(setCategories).catch(() => {});
    fetchOperationSources().then(setSources).catch(() => {});
    fetchOperationStats().then(setOpStats).catch(() => {});
  }, []);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [job?.logs]);

  useEffect(() => {
    if (uiLogRef.current) uiLogRef.current.scrollTop = uiLogRef.current.scrollHeight;
  }, [uiJob?.logs]);

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

  function chipLabel(id: string): string {
    const known = labelById.get(id);
    if (known) return known;
    // Collapse Search/Family/Discovery → activateFamily(global) for stale selections
    const sep = id.indexOf("::");
    if (sep > 0) {
      return scenarioDisplayName(id.slice(0, sep), id.slice(sep + 2));
    }
    return id;
  }

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

  function openCredentialsEditor() {
    const c = token?.credentials;
    setCredForm({
      username: c?.username || token?.email || "",
      password: "",
      org: c?.org || token?.org || "",
      gcid: c?.gcid || token?.gcid || "",
    });
    setCredError("");
    setCredOpen(true);
  }

  async function onApplyCredentials(e: FormEvent) {
    e.preventDefault();
    setCredBusy(true);
    setCredError("");
    try {
      const next = await applyTokenCredentials({
        username: credForm.username.trim(),
        password: credForm.password,
        org: credForm.org.trim() || undefined,
        gcid: credForm.gcid.trim() || undefined,
      });
      setToken(next);
      setCredOpen(false);
    } catch (err) {
      setCredError(err instanceof Error ? err.message : String(err));
    } finally {
      setCredBusy(false);
    }
  }

  async function onTargetChange(target: string) {
    setTargetBusy(true);
    setError("");
    try {
      const next = await setPipelineTarget(target);
      setPipeline(next);
      setQueueDraft({
        raw: next.raw_queue || "",
        enriched: next.enriched_queue || "",
      });
      setToken(await fetchTokenStatus());
    } catch (e) {
      setError(String(e));
    } finally {
      setTargetBusy(false);
    }
  }

  async function onSaveQueues() {
    setQueueBusy(true);
    setError("");
    try {
      const next = await setPipelineQueues({
        raw_queue: queueDraft.raw.trim(),
        enriched_queue: queueDraft.enriched.trim(),
      });
      setPipeline(next);
      setQueueDraft({
        raw: next.raw_queue || "",
        enriched: next.enriched_queue || "",
      });
      setQueueOpen(false);
    } catch (e) {
      setError(String(e));
    } finally {
      setQueueBusy(false);
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

  const uiPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const activateUiJob = useCallback((next: UiTriggerJob) => {
    setUiJob(next);
    localStorage.setItem(UI_JOB_KEY, next.id);
  }, []);

  const closeUiSession = useCallback(async () => {
    if (uiPollRef.current) {
      clearInterval(uiPollRef.current);
      uiPollRef.current = null;
    }
    const id = uiJob?.id;
    setUiBusy(false);
    setUiJob(null);
    setUiManualCid("");
    localStorage.removeItem(UI_JOB_KEY);
    if (!id) return;
    try {
      await cancelGenerateInUi(id);
    } catch {
      /* panel already closed locally */
    }
  }, [uiJob?.id]);

  const pollUiJob = useCallback((id: string) => {
    if (uiPollRef.current) clearInterval(uiPollRef.current);
    uiPollRef.current = setInterval(async () => {
      try {
        const res = await refreshGenerateInUi(id);
        setUiJob(res.job);
        // refresh endpoint already auto-finalizes when cids exist (no long poll)
        if (res.job.verification?.generate_run_saved) {
          if (uiPollRef.current) clearInterval(uiPollRef.current);
          uiPollRef.current = null;
          setUiBusy(false);
          try {
            const last = await fetchLastGenerateRun();
            if (last.ok && last.report) {
              setLastRun(last.report);
              setShowLastRun(true);
            }
          } catch {
            /* ignore */
          }
          return;
        }
        if (res.job.status === "failed" || res.job.status === "cancelled") {
          if (uiPollRef.current) clearInterval(uiPollRef.current);
          uiPollRef.current = null;
          setUiBusy(false);
        }
      } catch {
        /* keep polling through transient CasePilot blips */
      }
    }, 4000);
  }, []);

  useEffect(() => {
    const savedId = localStorage.getItem(UI_JOB_KEY);
    if (!savedId) return;
    fetchGenerateInUi(savedId)
      .then((j) => {
        setUiJob(j);
        if (
          j.status === "queued" ||
          j.status === "running" ||
          j.status === "pending_agent" ||
          (j.verification?.ready && !j.verification?.generate_run_saved)
        ) {
          pollUiJob(j.id);
        }
      })
      .catch(() => localStorage.removeItem(UI_JOB_KEY));
    return () => {
      if (uiPollRef.current) clearInterval(uiPollRef.current);
    };
  }, [pollUiJob]);

  useEffect(() => {
    if (!uiJob) return;
    if (
      uiJob.status === "queued" ||
      uiJob.status === "running" ||
      uiJob.status === "pending_agent" ||
      (uiJob.verification?.ready && !uiJob.verification?.generate_run_saved)
    ) {
      pollUiJob(uiJob.id);
    }
  }, [uiJob?.id, uiJob?.status, uiJob?.verification?.ready, uiJob?.verification?.generate_run_saved, pollUiJob]);

  async function onUiManualCid() {
    if (!uiJob?.id || !uiManualCid.trim()) return;
    setUiBusy(true);
    setError("");
    try {
      const op =
        uiJob.selection?.[0]?.operation ||
        uiJob.verification?.operations?.[0] ||
        "";
      const touch = uiJob.selection?.[0]?.touchpoint || "";
      const res = await recordGenerateInUiResults(uiJob.id, [
        {
          correlation_id: uiManualCid.trim(),
          operation: op,
          touchpoint: touch || undefined,
        },
      ]);
      activateUiJob(res.job);
      setUiManualCid("");
      // Auto-verify after paste (no extra click)
      const verified = await verifyGenerateInUi(uiJob.id);
      activateUiJob(verified.job);
      if (verified.ok) {
        const last = await fetchLastGenerateRun();
        setLastRun(last.ok && last.report ? last.report : null);
        setShowLastRun(true);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setUiBusy(false);
    }
  }

  async function onContinueUiVerify() {
    if (!uiJob) return;
    setUiBusy(true);
    setError("");
    try {
      const res = await verifyGenerateInUi(uiJob.id);
      activateUiJob(res.job);
      if (!res.ok) {
        const msg =
          (res.job?.agent as { last_error?: string } | undefined)?.last_error ||
          "Verification not ready — paste correlation_id first";
        setError(msg);
        return;
      }
      // Same Generation Status panel as API generate
      const last = await fetchLastGenerateRun();
      setLastRun(last.ok && last.report ? last.report : null);
      setShowLastRun(true);
      if (!last.ok) {
        setError(last.detail || "Verification finished but Generation Status report missing");
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setUiBusy(false);
    }
  }

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
  const statusScenarios = useMemo(
    () => dedupeScenarioRows(runReport?.scenarios || []),
    [runReport?.scenarios],
  );

  /** Rows shown in Generation Status with Pick checkboxes (scenarios preferred, else operations). */
  type StatusRow = {
    key: string;
    operation: string;
    touchpoint?: string | null;
    status: string;
    remark: string;
    raw?: boolean;
    enriched?: boolean;
    raw_event?: Record<string, unknown> | null;
    enriched_event?: Record<string, unknown> | null;
    scenario_id?: string;
    xCorrelationId?: string | null;
    source?: string;
  };

  const statusRows: StatusRow[] = useMemo(() => {
    if (statusScenarios.length > 0) {
      const uiRun = Boolean(runReport?.source === "generate_in_ui");
      return statusScenarios.map((s) => {
        const ui = String(s.source || "").toLowerCase() === "ui" || uiRun;
        const key = s.label || scenarioDisplayName(s.operation, s.touchpoint, { ui, be: !ui });
        return {
          key,
          operation: s.operation,
          touchpoint: s.touchpoint,
          status: String(s.status || "").toUpperCase() || "N/A",
          remark: s.remark || s.error || "—",
          raw: s.raw,
          enriched: s.enriched,
          raw_event: s.raw_event,
          enriched_event: s.enriched_event,
          scenario_id: s.scenario_id,
          xCorrelationId: s.xCorrelationId,
          source: s.source,
        };
      });
    }
    return (runReport?.operations || []).map((o, index) => {
      const ui =
        o.ui_status ||
        (o.status === "success"
          ? "PASS"
          : o.status === "no_correlation"
            ? "N/A"
            : o.status === "fail" || o.status === "failed" || o.trigger_error
              ? "FAIL"
              : String(o.status || "N/A").toUpperCase());
      const status = String(ui).toUpperCase();
      return {
        key: o.operation || `op-${index}`,
        operation: o.operation,
        status: status === "SUCCESS" ? "PASS" : status,
        remark: o.remark || o.trigger_error || o.status || "—",
        raw: o.raw,
        enriched: o.enriched,
        raw_event: o.raw_event,
        enriched_event: o.enriched_event,
        xCorrelationId: o.xCorrelationId,
      };
    });
  }, [statusScenarios, runReport?.operations, runReport?.source]);

  const scenarioSummary = useMemo(() => {
    if (!statusRows.length) return null;
    const pass = statusRows.filter((s) => s.status === "PASS").length;
    const fail = statusRows.filter((s) => s.status === "FAIL").length;
    const na = statusRows.length - pass - fail;
    return { pass, fail, na, total: statusRows.length };
  }, [statusRows]);
  const scenariosWithLanding = statusRows.filter((s) => s.raw || s.enriched).length;

  const enrichCandidates = useMemo(() => {
    return statusRows
      .filter((s) => s.enriched_event)
      .map((s) => ({
        key: s.key,
        label: s.key,
        data: s.enriched_event as Record<string, unknown>,
      }));
  }, [statusRows]);

  function toggleEnrichPick(key: string) {
    setEnrichPick((prev) => {
      if (prev.includes(key)) return prev.filter((k) => k !== key);
      if (prev.length >= 2) return [prev[1], key];
      return [...prev, key];
    });
  }

  function openEnrichDiff() {
    if (enrichPick.length !== 2) return;
    const a = enrichCandidates.find((c) => c.key === enrichPick[0]);
    const b = enrichCandidates.find((c) => c.key === enrichPick[1]);
    if (!a || !b) return;
    setEnrichDiff({ labelA: a.label, labelB: b.label, dataA: a.data, dataB: b.data });
  }

  const uiSelection = useMemo(() => {
    const ids = [...selected];
    if (!ids.length) return [];
    return ids.map((id) => {
      const cat = sources?.catalog?.find((c) => c.id === id);
      return {
        id,
        operation: cat?.operation || id.split("::")[0] || id,
        touchpoint: cat?.touchpoint ?? null,
        label: cat?.label || chipLabel(id),
      };
    });
  }, [selected, sources, labelById]);

  function jsonCell(data: unknown): string {
    if (!data) return "";
    try {
      return JSON.stringify(data);
    } catch {
      return "";
    }
  }

  function toggleExportPick(key: string) {
    setExportPick((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key],
    );
  }

  function selectExportByStatus(statuses: string[]) {
    const want = new Set(statuses.map((s) => s.toUpperCase()));
    const keys = statusRows.filter((s) => want.has(s.status)).map((s) => s.key);
    setExportPick(keys);
  }

  function exportStatusCsv() {
    if (!runReport) return;
    const rows: string[][] = [];
    const headers = isValidateMode
      ? ["operation", "raw", "enrich", "status", "remark"]
      : ["operation", "raw", "enrich"];
    const picked = new Set(exportPick);
    const onlyPicked = picked.size > 0;
    const source = statusRows.length > 0 ? statusRows : [];
    for (const s of source) {
      if (onlyPicked && !picked.has(s.key)) continue;
      const base = [s.key, jsonCell(s.raw_event), jsonCell(s.enriched_event)];
      rows.push(isValidateMode ? [...base, s.status || "", s.remark || ""] : base);
    }
    downloadCsv(`generate-status-${isValidateMode ? "validate" : "generate"}.csv`, headers, rows);
  }

  async function compareSelectedFromStatus() {
    if (!statusRows.length) {
      setError("No generation status rows to compare.");
      return;
    }
    const picked = new Set(exportPick);
    const pool = statusRows.filter((s) => {
      if (picked.size && !picked.has(s.key)) return false;
      return s.status === "PASS" && (s.raw || s.enriched);
    });
    const ops = [...new Set(pool.map((s) => s.operation).filter(Boolean))];
    if (!ops.length) {
      setError(
        picked.size
          ? "No selected PASS rows with raw/enrich. Tick PASS rows first, or clear pick and try again."
          : "No PASS rows with raw/enrich to compare.",
      );
      return;
    }
    setCompareBusy(true);
    setError("");
    try {
      const job = await startCompare(ops);
      onCompareCompleted?.(job.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setCompareBusy(false);
    }
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
        <label className="inline-control">
          Environment
          <select
            value={pipeline?.target || "pp"}
            disabled={targetBusy || running}
            onChange={(e) => onTargetChange(e.target.value)}
          >
            {(pipeline?.available_targets?.length
              ? pipeline.available_targets
              : DEFAULT_TARGETS
            ).map((t) => (
              <option key={t.id} value={t.id}>
                {t.label}
              </option>
            ))}
          </select>
        </label>
        {(pipeline?.nextgen_url ||
          DEFAULT_TARGETS.find((t) => t.id === (pipeline?.target || "pp"))?.url) && (
          <a
            href={
              pipeline?.nextgen_url ||
              DEFAULT_TARGETS.find((t) => t.id === (pipeline?.target || "pp"))?.url
            }
            target="_blank"
            rel="noreferrer"
            className="context-link"
          >
            Open NextGen ↗
          </a>
        )}
        {pipeline?.mongo_db && (
          <span className="muted" title={pipeline.mongo_url_host || ""}>
            Mongo · {pipeline.mongo_db}
          </span>
        )}
        {pipeline && !pipeline.error && (
          <>
            <details
              className="queue-details"
              open={queueOpen}
              onToggle={(e) => setQueueOpen((e.target as HTMLDetailsElement).open)}
            >
              <summary>User / queues</summary>
              <div className="queue-details-body">
                <label className="inline-control">
                  Raw
                  <input
                    value={queueDraft.raw}
                    disabled={queueBusy || running}
                    onChange={(e) => setQueueDraft((d) => ({ ...d, raw: e.target.value }))}
                    spellCheck={false}
                  />
                </label>
                <label className="inline-control">
                  Enriched
                  <input
                    value={queueDraft.enriched}
                    disabled={queueBusy || running}
                    onChange={(e) => setQueueDraft((d) => ({ ...d, enriched: e.target.value }))}
                    spellCheck={false}
                  />
                </label>
                <button
                  type="button"
                  disabled={
                    queueBusy ||
                    running ||
                    !queueDraft.raw.trim() ||
                    !queueDraft.enriched.trim() ||
                    (queueDraft.raw === (pipeline.raw_queue || "") &&
                      queueDraft.enriched === (pipeline.enriched_queue || ""))
                  }
                  onClick={onSaveQueues}
                >
                  {queueBusy ? "Saving…" : "Save queues"}
                </button>
              </div>
            </details>
            <span className={pipeline.ingestion_running ? "ok" : "warn"}>
              ● ingestion {pipeline.ingestion_running ? "running" : "stopped"}
            </span>
            {pipeline.queue_warning && (
              <span className="warn" title={pipeline.queue_warning}>
                ⚠ queues: {pipeline.queue_environment?.toUpperCase()}
              </span>
            )}
          </>
        )}
        {pipeline?.error && (
          <span className="error small" title={pipeline.error}>
            Pipeline config error — env switch still available
          </span>
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
        {token && (
          <span className={`compact-token ${!token.present || token.expired ? "warn" : "ok"}`}>
            ● token {!token.present ? "missing" : token.expired ? "expired" : "valid"}
            {token.expires_in_hours != null && !token.expired ? ` ${token.expires_in_hours}h` : ""}
            {token.email ? ` · ${token.email}` : ""}
            <button type="button" className="link-btn" disabled={tokenBusy} onClick={onRefreshToken}>
              {tokenBusy ? "…" : "refresh"}
            </button>
            <button type="button" className="link-btn" onClick={openCredentialsEditor}>
              edit
            </button>
          </span>
        )}
      </div>

      {credOpen && (
        <div className="modal-backdrop" role="presentation" onClick={() => !credBusy && setCredOpen(false)}>
          <div
            className="modal-card token-cred-modal"
            role="dialog"
            aria-label="Edit OAuth credentials"
            onClick={(e) => e.stopPropagation()}
          >
            <header className="modal-head">
              <h3>Bearer credentials</h3>
              <button type="button" className="link-btn" disabled={credBusy} onClick={() => setCredOpen(false)}>
                close
              </button>
            </header>
            <p className="muted small">
              Generate a fresh Bearer via OAuth password grant. Just username + password is enough —
              org &amp; gcid are read back from the token's JWT claims and drive actor validation and
              x-correlation-id scoping. Only set org/gcid below to force a specific organisation.
            </p>
            <form className="token-cred-form" onSubmit={onApplyCredentials}>
              <label>
                Username / email
                <input
                  value={credForm.username}
                  onChange={(e) => setCredForm((f) => ({ ...f, username: e.target.value }))}
                  autoComplete="username"
                  required
                />
              </label>
              <label>
                Password
                <input
                  type="password"
                  value={credForm.password}
                  onChange={(e) => setCredForm((f) => ({ ...f, password: e.target.value }))}
                  autoComplete="current-password"
                  required
                  placeholder={token?.credentials?.has_password === "1" ? "••••••••" : ""}
                />
              </label>
              <label>
                Org <span className="muted">(optional)</span>
                <input
                  value={credForm.org}
                  onChange={(e) => setCredForm((f) => ({ ...f, org: e.target.value }))}
                  placeholder="auto from token"
                />
              </label>
              <label>
                GCID <span className="muted">(optional)</span>
                <input
                  value={credForm.gcid}
                  onChange={(e) => setCredForm((f) => ({ ...f, gcid: e.target.value }))}
                  placeholder="auto from token"
                />
              </label>
              {credError && <p className="error small">{credError}</p>}
              <div className="modal-actions">
                <button type="button" disabled={credBusy} onClick={() => setCredOpen(false)}>
                  Cancel
                </button>
                <button type="submit" className="primary" disabled={credBusy}>
                  {credBusy ? "Generating…" : "Generate token"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      <OpListModal state={listModal} onClose={() => setListModal(null)} />

      <div className="generate-filter-row">
        <div className="generate-filter-left">
          <OperationDropdown
            options={visibleOperations}
            selected={selected}
            onToggle={toggle}
            onSelectAll={() => setSelected(new Set(visibleOperations.map((i) => i.id)))}
            onClear={() => setSelected(new Set())}
          />
        </div>
        <div className="generate-filter-right">
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
      </div>

      {selected.size > 0 && (
        <div className="selected-chips">
          {[...selected].map((id) => (
            <span key={id} className="chip selected chip-with-edit">
              <button type="button" className="chip-edit" title="Edit & send payload" onClick={() => setEditItem(id)}>
                ✎
              </button>
              <button type="button" className="chip-remove" onClick={() => toggle(id)}>
                {chipLabel(id)} ✕
              </button>
            </span>
          ))}
        </div>
      )}

      {editItem && (
        <PayloadEditor
          itemId={editItem}
          label={chipLabel(editItem)}
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
        <button
          type="button"
          className="primary outline"
          disabled={running || selected.size === 0}
          title={
            selected.size === 0
              ? "Select at least one scenario"
              : "Trigger via CasePilot UI — then verify here with correlation-id"
          }
          onClick={() => setUiTriggerOpen(true)}
        >
          Generate in UI
        </button>
        <button type="button" className="primary outline" disabled={lastRunBusy} onClick={onShowLastRun}>
          {lastRunBusy ? "Loading…" : "Generation Status"}
        </button>
        {job && <span className={`status-pill ${job.status}`}>{job.status}</span>}
      </div>

      {error && <p className="error">{error}</p>}

      {uiTriggerOpen && (
        <GenerateInUiModal
          selection={uiSelection}
          onClose={() => setUiTriggerOpen(false)}
          onActive={(j) => {
            activateUiJob(j);
            if (j.status === "queued" || j.status === "running") pollUiJob(j.id);
          }}
        />
      )}

      {uiJob && (
        <details className="operation-summary-details generation-log-details" open>
          <summary style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span>
              UI trigger log · Job {uiJob.id.slice(0, 8)}
              {" · "}
              <span className={`status-pill ${uiJob.status}`}>{uiJob.status}</span>
              {uiJob.verification?.ready ? " · correlation ready" : ""}
              {uiJob.verification?.generate_run_saved ? " · Generation Status saved" : ""}
              {" · stays open after browser closes"}
            </span>
            <button
              type="button"
              className="link-btn"
              style={{ marginLeft: "auto" }}
              disabled={uiBusy}
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                void closeUiSession();
              }}
              title="Stop polling and dismiss this Generate-in-UI session"
            >
              Close session ✕
            </button>
          </summary>
          {!!(uiJob.agent as { last_error?: string } | undefined)?.last_error && (
            <p className="error small">
              {(uiJob.agent as { last_error?: string }).last_error}
            </p>
          )}
          <div className="log-box generation-log-box">
            <pre ref={uiLogRef} className="job-logs">
              {(uiJob.logs || []).join("\n") || "Waiting for CasePilot…"}
            </pre>
          </div>
          {(uiJob.results || []).length > 0 && (
            <div className="mongo-status generation-status-actions" style={{ marginTop: 8 }}>
              <strong>Captured correlation_id(s)</strong>
              <ul className="muted small">
                {(uiJob.results || [])
                  .filter((r) => {
                    const c = String(r.correlation_id || "");
                    return (
                      /^[0-9a-fA-F-]{36}$/.test(c) &&
                      !c.toLowerCase().includes("your-uuid")
                    );
                  })
                  .map((r, i) => (
                    <li key={`${r.correlation_id}-${i}`}>
                      <code>{r.correlation_id}</code>
                      {r.operation
                        ? ` · ${scenarioDisplayName(r.operation, r.touchpoint, { ui: true })}`
                        : ""}
                    </li>
                  ))}
              </ul>
              <p className="muted small">
                Verification runs automatically after CasePilot finishes — Generation Status opens
                with raw/enrich. Use Continue only if auto-verify did not run.
              </p>
            </div>
          )}
          <div className="mongo-status generation-status-actions" style={{ marginTop: 8, gap: 8 }}>
            <input
              value={uiManualCid}
              onChange={(e) => setUiManualCid(e.target.value)}
              placeholder="Paste correlation-id from DevTools response header"
              style={{ minWidth: 280 }}
            />
            <button type="button" disabled={uiBusy || !uiManualCid.trim()} onClick={onUiManualCid}>
              Save correlation_id
            </button>
            <button
              type="button"
              className="primary"
              disabled={
                uiBusy ||
                !(uiJob.verification?.ready || (uiJob.results || []).some((r) => r.correlation_id))
              }
              onClick={onContinueUiVerify}
              title="Look up Mongo raw/enrich by correlation_id and open Generation Status"
            >
              {uiBusy ? "Verifying…" : "Continue verification"}
            </button>
            <button
              type="button"
              disabled={uiBusy}
              onClick={async () => {
                setUiBusy(true);
                try {
                  const res = await refreshGenerateInUi(uiJob.id);
                  activateUiJob(res.job);
                } catch (e) {
                  setError(String(e));
                } finally {
                  setUiBusy(false);
                }
              }}
            >
              Refresh CasePilot
            </button>
            <button type="button" disabled={uiBusy} onClick={() => void closeUiSession()}>
              Close session
            </button>
          </div>
          <p className="muted small" style={{ marginTop: 8 }}>
            Auto-mapped TestRail ids → Send. When CasePilot finishes we auto-capture all{" "}
            <code>correlation-id</code>s (including createProject / list helpers) and open Generation
            Status with raw + enrich. Paste below only if the agent omitted AUDIT_RESULT.
          </p>
        </details>
      )}

      {job && (
        <details className="operation-summary-details generation-log-details" open>
          <summary>
            Live generation log · Job {job.id.slice(0, 8)}
            {!!job.params.validate ? " · generate + validate" : ""}
            {" · "}
            <span className={`status-pill ${job.status}`}>{job.status}</span>
            {job.error ? ` · ${job.error}` : ""}
            {job.result?.exit_code !== undefined ? ` · exit ${job.result.exit_code}` : ""}
          </summary>
          <div className="log-box generation-log-box">
            <pre ref={logRef} className="job-logs">{job.logs.join("\n") || "Waiting for logs…"}</pre>
          </div>
        </details>
      )}

      {runReport && (job?.status === "completed" || job?.status === "failed" || showLastRun) && (
        <div className="generate-run-status">
          <details className="operation-summary-details" open>
            <summary>
              Generation Status
              {isValidateMode ? " · generate + validate" : " · generate only"}
              {isValidateMode && (
                <>
                  {" · "}
                  PASS: {scenarioSummary?.pass ?? runReport.summary?.pass ?? runReport.summary?.success ?? 0}
                  {" · "}
                  FAIL: {scenarioSummary?.fail ?? runReport.summary?.fail ?? runReport.summary?.needs_work ?? 0}
                  {" · "}
                  N/A: {scenarioSummary?.na ?? runReport.summary?.na ?? 0}
                  {(scenarioSummary?.total ?? runReport.summary?.total) != null &&
                    ` / ${scenarioSummary?.total ?? runReport.summary?.total}`}
                </>
              )}
              {!isValidateMode && (
                <>
                  {" · "}
                  scenarios {statusRows.length}
                  {" · "}
                  landed {scenariosWithLanding}
                </>
              )}
            </summary>
            <div className="mongo-status generation-status-actions">
              <button type="button" className="link-btn" onClick={exportStatusCsv}>
                Export CSV{exportPick.length ? ` (${exportPick.length})` : " (all)"}
              </button>
              <button type="button" className="link-btn" onClick={() => selectExportByStatus(["FAIL", "N/A"])}>
                Select FAIL/N/A
              </button>
              <button type="button" className="link-btn" onClick={() => selectExportByStatus(["PASS"])}>
                Select PASS
              </button>
              <button
                type="button"
                className="link-btn"
                onClick={() => setExportPick(statusRows.map((r) => r.key))}
              >
                Select all
              </button>
              {exportPick.length > 0 && (
                <button type="button" className="link-btn" onClick={() => setExportPick([])}>
                  clear ({exportPick.length})
                </button>
              )}
              <button
                type="button"
                className="link-btn"
                disabled={compareBusy || statusRows.length === 0}
                onClick={compareSelectedFromStatus}
                title="Compare selected PASS rows (or all PASS if none selected) → Result tab"
              >
                {compareBusy ? "Comparing…" : "Compare selected PASS → Result"}
              </button>
              {enrichCandidates.length >= 2 && (
                <>
                  <span className="muted small">Pick 2 for enrich leaf-diff</span>
                  <button
                    type="button"
                    className="link-btn"
                    disabled={enrichPick.length !== 2}
                    onClick={openEnrichDiff}
                  >
                    Diff enrich ({enrichPick.length}/2)
                  </button>
                  {enrichPick.length > 0 && (
                    <button type="button" className="link-btn" onClick={() => setEnrichPick([])}>
                      clear diff
                    </button>
                  )}
                </>
              )}
            </div>
            {exportPick.length > 0 && (
              <p className="muted small" style={{ margin: "4px 0 8px" }}>
                {exportPick.length} selected — Export CSV uses this set; Compare uses PASS among selection
                (or all PASS if you clear selection).
              </p>
            )}
            {statusRows.length === 0 ? (
              <p className="warn small">No status rows yet — run Generate first.</p>
            ) : (
              <div className="result-table-wrap compact-table-wrap generate-status-table">
                <table className="result-table scenario-status-table">
                  <thead>
                    <tr>
                      <th title="Select for CSV export / compare">
                        <input
                          type="checkbox"
                          checked={
                            statusRows.length > 0 &&
                            statusRows.every((r) => exportPick.includes(r.key))
                          }
                          onChange={(e) => {
                            if (e.target.checked) {
                              setExportPick(statusRows.map((r) => r.key));
                            } else {
                              setExportPick([]);
                            }
                          }}
                          aria-label="Select all rows"
                        />
                      </th>
                      <th title="Select for enrich JSON leaf diff">Diff</th>
                      <th>Operation</th>
                      <th>Raw</th>
                      <th>Enrich</th>
                      {isValidateMode && (
                        <>
                          <th>Status</th>
                          <th>Remark</th>
                        </>
                      )}
                      <th>UI</th>
                    </tr>
                  </thead>
                  <tbody>
                    {statusRows.map((s) => {
                      const canDiff = Boolean(s.enriched_event);
                      return (
                        <tr key={`${s.key}-${s.xCorrelationId || ""}`}>
                          <td>
                            <input
                              type="checkbox"
                              checked={exportPick.includes(s.key)}
                              onChange={() => toggleExportPick(s.key)}
                              aria-label={`Select ${s.key}`}
                            />
                          </td>
                          <td>
                            {canDiff ? (
                              <input
                                type="checkbox"
                                checked={enrichPick.includes(s.key)}
                                onChange={() => toggleEnrichPick(s.key)}
                                aria-label={`Diff ${s.key}`}
                              />
                            ) : (
                              <span className="muted">—</span>
                            )}
                          </td>
                          <td>
                            <code className="scenario-op-name">{s.key}</code>
                          </td>
                          <td>
                            <EventJsonCell
                              label="raw"
                              present={s.raw}
                              data={s.raw_event}
                              title={s.key}
                            />
                          </td>
                          <td>
                            <EventJsonCell
                              label="enrich"
                              present={s.enriched}
                              data={s.enriched_event}
                              title={s.key}
                            />
                          </td>
                          {isValidateMode && (
                            <>
                              <td>
                                <span
                                  className={`status-pill ${
                                    s.status === "PASS"
                                      ? "completed"
                                      : s.status === "N/A"
                                        ? "pending"
                                        : "failed"
                                  }`}
                                >
                                  {s.status}
                                </span>
                              </td>
                              <td className="remark-cell">{s.remark}</td>
                            </>
                          )}
                          <td>
                            <button
                              type="button"
                              className="link-btn"
                              onClick={() =>
                                setVerifyCtx({
                                  operation: s.operation,
                                  touchpoint: s.touchpoint ?? undefined,
                                  scenarioId: s.scenario_id,
                                  correlationId: s.xCorrelationId || undefined,
                                })
                              }
                            >
                              Verify in UI
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </details>
          {verifyCtx && (
            <VerifyInUiModal context={verifyCtx} onClose={() => setVerifyCtx(null)} />
          )}
          {enrichDiff && (
            <EnrichDiffModal
              labelA={enrichDiff.labelA}
              labelB={enrichDiff.labelB}
              dataA={enrichDiff.dataA}
              dataB={enrichDiff.dataB}
              onClose={() => setEnrichDiff(null)}
            />
          )}
        </div>
      )}
    </section>
  );
}
