import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import MultiSelect from "../components/MultiSelect";
import VerifyInUiModal, { type VerifyInUiContext } from "../components/VerifyInUiModal";
import {
  fetchCategories,
  fetchComparableOperations,
  fetchFailureSummary,
  fetchJob,
  fetchJobs,
  fetchLatestResults,
  deleteLatestResult,
  exportComparisonExcel,
  type CategoryReport,
  type ComparableOperation,
  type ComparisonRow,
  type FailureSummary,
  type Job,
  type LatestComparisonItem,
} from "../api";

type Props = {
  initialJobId: string | null;
  /** When set, show/limit the coverage list to just these compared operations. */
  highlightOperations?: string[] | null;
};

type ViewMode = "cards" | "list";
type SourceMode = "latest" | "job";
type TrackStatus = "unreviewed" | "covered" | "needs_enhancement";

const RESULT_MODE_KEY = "audit_result_mode";
const RESULT_VIEW_KEY = "audit_result_field_view";
const TRACK_KEY = "audit_result_coverage_track";

function statusClass(s: string) {
  if (s === "PASS") return "pass";
  if (s === "FAIL") return "fail";
  if (s === "SKIP") return "skip";
  return "na";
}

function displayField(row: ComparisonRow): string {
  return row.field || row.field_path.split(".").pop() || row.field_path;
}

/** Match highlight keys across bare vs scenario names (activateFamily ↔ activateFamily(global)). */
function operationMatchesHighlight(operation: string, highlight: Set<string>): boolean {
  if (!highlight.size) return true;
  if (highlight.has(operation)) return true;
  const base = operation.split("(", 1)[0];
  for (const h of highlight) {
    if (h === base || operation.startsWith(`${h}(`) || h.startsWith(`${base}(`)) return true;
    const hBase = h.split("(", 1)[0];
    if (base === hBase) return true;
  }
  return false;
}

/** Top-level enrich JSON sections — same order as the resolver envelope. */
type EnvelopeSection = {
  key: string;
  label: string;
  rows: ComparisonRow[];
};

const ENVELOPE_ORDER = [
  "event",
  "source",
  "subject",
  "subject.enrichedSnapshot",
  "actor",
  "actor.enrichedSnapshot",
] as const;

function envelopeKey(path: string): (typeof ENVELOPE_ORDER)[number] {
  if (path.startsWith("actor.enrichedSnapshot.")) return "actor.enrichedSnapshot";
  if (path.startsWith("actor.")) return "actor";
  if (path.startsWith("subject.enrichedSnapshot.")) return "subject.enrichedSnapshot";
  if (path.startsWith("subject.")) return "subject";
  if (path.startsWith("source.")) return "source";
  return "event";
}

function envelopeLabel(key: string): string {
  switch (key) {
    case "event":
      return "event (envelope)";
    case "source":
      return "source";
    case "subject":
      return "subject";
    case "subject.enrichedSnapshot":
      return "subject.enrichedSnapshot";
    case "actor":
      return "actor";
    case "actor.enrichedSnapshot":
      return "actor.enrichedSnapshot";
    default:
      return key;
  }
}

/** Nested branch under enrichedSnapshot (customer, user, fontDetails[0], …). */
function snapshotBranch(path: string): string {
  const prefixes = ["subject.enrichedSnapshot.", "actor.enrichedSnapshot."] as const;
  for (const p of prefixes) {
    if (path.startsWith(p)) {
      const rest = path.slice(p.length);
      const m = rest.match(/^([^.]+(?:\[\d+\])?)/);
      return m?.[1] || rest;
    }
  }
  return "";
}

function enrichPathSortKey(path: string): string {
  const section = ENVELOPE_ORDER.indexOf(envelopeKey(path));
  const branch = snapshotBranch(path);
  return `${String(section).padStart(2, "0")}|${branch}|${path}`;
}

function groupByEnvelope(rows: ComparisonRow[]): EnvelopeSection[] {
  const sorted = [...rows].sort((a, b) =>
    enrichPathSortKey(a.field_path).localeCompare(enrichPathSortKey(b.field_path)),
  );
  const map = new Map<string, ComparisonRow[]>();
  for (const row of sorted) {
    const key = envelopeKey(row.field_path);
    const list = map.get(key) ?? [];
    list.push(row);
    map.set(key, list);
  }
  return ENVELOPE_ORDER.filter((k) => map.has(k)).map((k) => ({
    key: k,
    label: envelopeLabel(k),
    rows: map.get(k)!,
  }));
}

function groupBySnapshotBranch(rows: ComparisonRow[]): { branch: string; rows: ComparisonRow[] }[] {
  const map = new Map<string, ComparisonRow[]>();
  for (const row of rows) {
    const branch = snapshotBranch(row.field_path) || "(root)";
    const list = map.get(branch) ?? [];
    list.push(row);
    map.set(branch, list);
  }
  return [...map.entries()].map(([branch, branchRows]) => ({ branch, rows: branchRows }));
}

/** Short resource / "table" name from the source_api path for the Result label. */
function sourceResource(row: ComparisonRow): string {
  const api = (row.source_api || "").toLowerCase();
  if (!api) return "";
  if (api.includes("/users") || api.includes("idpuserid")) return "users";
  if (api.includes("/profiles") || api.includes("profiles")) return "profiles";
  if (api.includes("/roles") || api.includes("role")) return "roles";
  if (api.includes("/teams") || api.includes("team")) return "teams";
  if (api.includes("/customers") || api.includes("customer")) return "customers";
  if (api.includes("/variations") || api.includes("variations")) return "variations";
  if (api.includes("/styles") || api.includes("styles")) return "styles";
  if (api.includes("/asset") || api.includes("ams") || api.includes("assets")) return "assets";
  if (api.includes("jwt") || api.includes("bearer") || api.includes("token")) return "jwt";
  if (api.includes("raw")) return "raw";
  if (api.includes("resolver") || api.includes("enricher") || api.includes("derived")) return "resolver";
  // Fallback: last path segment that looks like a resource
  const parts = api.replace(/\?.*$/, "").split("/").filter(Boolean);
  const last = parts[parts.length - 1] || "";
  if (last && !last.includes("{") && last.length < 24) return last;
  return "";
}

function sourceLabel(row: ComparisonRow): string {
  const resource = sourceResource(row);
  if (resource) return `Source (${row.source_system}) · ${resource}`;
  return `Source (${row.source_system})`;
}

function formatComparedAt(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function loadTrack(): Record<string, TrackStatus> {
  try {
    const raw = localStorage.getItem(TRACK_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Record<string, TrackStatus>;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function summarizeOp(rows: ComparisonRow[]) {
  let passed = 0;
  let failed = 0;
  let skipped = 0;
  let na = 0;
  for (const r of rows) {
    if (r.match_status === "PASS") passed += 1;
    else if (r.match_status === "FAIL") failed += 1;
    else if (r.match_status === "SKIP") skipped += 1;
    else na += 1;
  }
  return { passed, failed, skipped, na, total: rows.length };
}

export default function ResultsPage({ initialJobId, highlightOperations }: Props) {
  const [sourceMode, setSourceMode] = useState<SourceMode>(() => {
    const stored = localStorage.getItem(RESULT_MODE_KEY);
    return stored === "job" ? "job" : "latest";
  });
  const [latest, setLatest] = useState<{
    operations: string[];
    items: LatestComparisonItem[];
    rows: ComparisonRow[];
    count: number;
  } | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [activeId, setActiveId] = useState<string | null>(initialJobId);
  const [job, setJob] = useState<Job | null>(null);
  const [filterOp, setFilterOp] = useState("");
  const [verifyCtx, setVerifyCtx] = useState<VerifyInUiContext | null>(null);
  const [filterStatus, setFilterStatus] = useState("all");
  /** Coverage table: fully pass / has fails / skips only (partial). */
  const [coverageOutcome, setCoverageOutcome] = useState<"all" | "pass" | "failed" | "partial">("all");
  const [filterCategory, setFilterCategory] = useState("all");
  const [filterEnv, setFilterEnv] = useState<string[]>([]);
  const [filterService, setFilterService] = useState<string[]>([]);
  const [categories, setCategories] = useState<CategoryReport | null>(null);
  const [opMeta, setOpMeta] = useState<ComparableOperation[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [viewMode, setViewMode] = useState<ViewMode>(() => {
    const stored = localStorage.getItem(RESULT_VIEW_KEY);
    return stored === "list" ? "list" : "cards";
  });

  useEffect(() => {
    localStorage.setItem(RESULT_VIEW_KEY, viewMode);
  }, [viewMode]);
  const [track, setTrack] = useState<Record<string, TrackStatus>>(loadTrack);
  const [scrollRestoreY, setScrollRestoreY] = useState<number | null>(null);
  const coverageListRef = useRef<HTMLDivElement | null>(null);
  const detailAnchorRef = useRef<HTMLDivElement | null>(null);
  const [failureLog, setFailureLog] = useState<FailureSummary | null>(null);
  const [failureLogBusy, setFailureLogBusy] = useState(false);
  const [showFailureLog, setShowFailureLog] = useState(false);

  async function openFailureLog() {
    setFailureLogBusy(true);
    setShowFailureLog(true);
    try {
      setFailureLog(await fetchFailureSummary());
    } catch {
      setFailureLog({ total_fail_rows: 0, groups: [], error: "Could not load failure summary" });
    } finally {
      setFailureLogBusy(false);
    }
  }

  const loadLatest = useCallback(() => {
    fetchLatestResults()
      .then(setLatest)
      .catch(() => setLatest(null));
  }, []);

  const [deletingOp, setDeletingOp] = useState<string | null>(null);
  /** Bulk-select operations in the coverage table for deletion. */
  const [selectedOps, setSelectedOps] = useState<Set<string>>(new Set());
  const [exportBusy, setExportBusy] = useState(false);
  const [refreshJobId, setRefreshJobId] = useState<string | null>(null);
  const [refreshError, setRefreshError] = useState("");
  /** When Compare hands us the compared ops, limit the coverage list to those. */
  const [highlightActive, setHighlightActive] = useState(false);
  const highlightSet = useMemo(
    () => new Set((highlightOperations ?? []).filter(Boolean)),
    [highlightOperations],
  );
  useEffect(() => {
    if (highlightSet.size) {
      setHighlightActive(true);
      setSourceMode("latest");
    }
  }, [highlightSet]);

  const onDeleteResult = useCallback(
    async (operation: string) => {
      if (!window.confirm(`Delete stored result for "${operation}"?`)) return;
      setDeletingOp(operation);
      try {
        await deleteLatestResult(operation);
        if (filterOp === operation) clearFilters();
        loadLatest();
      } catch {
        /* surfaced via reload */
      } finally {
        setDeletingOp(null);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [filterOp, loadLatest],
  );

  useEffect(() => {
    fetchCategories().then(setCategories).catch(() => {});
    fetchComparableOperations()
      .then((r) => setOpMeta(r.items ?? []))
      .catch(() => {});
    loadLatest();
  }, [loadLatest]);

  useEffect(() => {
    localStorage.setItem(RESULT_MODE_KEY, sourceMode);
  }, [sourceMode]);

  useEffect(() => {
    localStorage.setItem(TRACK_KEY, JSON.stringify(track));
  }, [track]);

  useEffect(() => {
    fetchJobs().then((r) => {
      const compareJobs = r.jobs.filter((j) => j.kind === "compare" || j.params?.validate);
      setJobs(compareJobs);
      if (initialJobId) setActiveId(initialJobId);
      else if (!activeId && compareJobs.length) setActiveId(compareJobs[0].id);
    }).catch(() => {});
  }, [initialJobId]);

  // Refresh coverage while a long compare is writing progressive snapshots.
  useEffect(() => {
    if (sourceMode !== "latest") return;
    const ms = refreshJobId ? 3000 : 8000;
    const t = setInterval(() => {
      loadLatest();
    }, ms);
    return () => clearInterval(t);
  }, [sourceMode, loadLatest, refreshJobId]);

  useEffect(() => {
    if (!activeId) return;
    let stop = false;
    async function poll() {
      try {
        const j = await fetchJob(activeId!);
        if (!stop) {
          setJob(j);
          if (j.status === "completed" || j.status === "failed") {
            loadLatest();
            if (refreshJobId && j.id === refreshJobId) setRefreshJobId(null);
            return;
          }
        }
      } catch {
        return;
      }
      if (!stop) setTimeout(poll, 1500);
    }
    poll();
    return () => { stop = true; };
  }, [activeId, loadLatest, refreshJobId]);

  useEffect(() => {
    if (initialJobId) setActiveId(initialJobId);
  }, [initialJobId]);

  const jobRows: ComparisonRow[] = job?.result?.rows ?? job?.result?.validation?.rows ?? [];
  const rows: ComparisonRow[] = sourceMode === "latest" ? (latest?.rows ?? []) : jobRows;

  const comparedAtByOp = useMemo(() => {
    const m = new Map<string, string>();
    for (const item of latest?.items ?? []) m.set(item.operation, item.compared_at);
    return m;
  }, [latest]);

  const byOperation = categories?.by_operation ?? {};

  const metaByOp = useMemo(() => {
    const m = new Map<string, ComparableOperation>();
    for (const item of opMeta) m.set(item.operation, item);
    return m;
  }, [opMeta]);

  function metaForOperation(operation: string): ComparableOperation | undefined {
    const direct = metaByOp.get(operation);
    if (direct) return direct;
    const base = operation.includes("(") ? operation.split("(", 1)[0] : operation;
    return metaByOp.get(base);
  }

  function categoryForOperation(operation: string): string {
    if (byOperation[operation]) return byOperation[operation];
    const base = operation.includes("(") ? operation.split("(", 1)[0] : operation;
    if (byOperation[base]) return byOperation[base];
    return metaForOperation(operation)?.category || "—";
  }

  const environmentOptions = useMemo(
    () => [...new Set(opMeta.map((i) => i.environment).filter(Boolean))].sort(),
    [opMeta],
  );
  const serviceOptions = useMemo(
    () => [...new Set(opMeta.map((i) => i.service).filter(Boolean))].sort(),
    [opMeta],
  );

  const scopedRows = useMemo(() => {
    return rows.filter((r) => {
      if (filterOp && !r.operation.toLowerCase().includes(filterOp.toLowerCase())) return false;
      if (filterCategory !== "all" && categoryForOperation(r.operation) !== filterCategory) return false;
      if (filterEnv.length || filterService.length) {
        const meta = metaForOperation(r.operation);
        if (filterEnv.length && (!meta || !filterEnv.includes(meta.environment))) return false;
        if (filterService.length && (!meta || !filterService.includes(meta.service))) return false;
      }
      return true;
    });
  }, [rows, filterOp, filterCategory, filterEnv, filterService, byOperation, metaByOp]);

  const filtered = useMemo(() => {
    if (filterStatus === "all") return scopedRows;
    return scopedRows.filter((r) => r.match_status === filterStatus);
  }, [scopedRows, filterStatus]);

  const grouped = useMemo(() => {
    const map = new Map<string, ComparisonRow[]>();
    for (const row of filtered) {
      const list = map.get(row.operation) ?? [];
      list.push(row);
      map.set(row.operation, list);
    }
    return [...map.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [filtered]);

  /** Full per-op rollup (ignores field status filter) so coverage filters stay accurate. */
  const allCoverageRows = useMemo(() => {
    const map = new Map<string, ComparisonRow[]>();
    for (const row of scopedRows) {
      const list = map.get(row.operation) ?? [];
      list.push(row);
      map.set(row.operation, list);
    }
    return [...map.entries()]
      .map(([operation, opRows]) => {
        const summary = summarizeOp(opRows);
        const meta = metaForOperation(operation);
        const status: TrackStatus = track[operation] || "unreviewed";
        return {
          operation,
          category: categoryForOperation(operation),
          environment: meta?.environment || "—",
          service: meta?.service || "—",
          comparedAt: comparedAtByOp.get(operation) || "",
          track: status,
          ...summary,
        };
      })
      // Newest run first; fall back to operation name when timestamps tie.
      .sort((a, b) => {
        const byDate = (b.comparedAt || "").localeCompare(a.comparedAt || "");
        return byDate !== 0 ? byDate : a.operation.localeCompare(b.operation);
      });
  }, [scopedRows, metaByOp, byOperation, comparedAtByOp, track]);

  const coverageRows = useMemo(() => {
    return allCoverageRows.filter((r) => {
      if (highlightActive && highlightSet.size && !operationMatchesHighlight(r.operation, highlightSet)) {
        return false;
      }
      if (coverageOutcome === "pass") return r.failed === 0 && r.skipped === 0;
      if (coverageOutcome === "failed") return r.failed > 0;
      if (coverageOutcome === "partial") return r.failed === 0 && r.skipped > 0;
      return true;
    });
  }, [allCoverageRows, coverageOutcome, highlightActive, highlightSet]);

  function toggleSelectOp(op: string) {
    setSelectedOps((prev) => {
      const next = new Set(prev);
      if (next.has(op)) next.delete(op);
      else next.add(op);
      return next;
    });
  }

  const coverageCounts = useMemo(() => {
    let pass = 0;
    let failed = 0;
    let partial = 0;
    for (const r of allCoverageRows) {
      if (r.failed > 0) failed += 1;
      else if (r.skipped > 0) partial += 1;
      else pass += 1;
    }
    return { pass, failed, partial, all: allCoverageRows.length };
  }, [allCoverageRows]);

  const coverageTotals = useMemo(() => {
    return coverageRows.reduce(
      (acc, r) => {
        acc.ops += 1;
        acc.passed += r.passed;
        acc.failed += r.failed;
        acc.skipped += r.skipped;
        acc.na += r.na;
        if (r.track === "covered") acc.covered += 1;
        if (r.track === "needs_enhancement") acc.needs += 1;
        if (r.track === "unreviewed") acc.unreviewed += 1;
        return acc;
      },
      { ops: 0, passed: 0, failed: 0, skipped: 0, na: 0, covered: 0, needs: 0, unreviewed: 0 },
    );
  }, [coverageRows]);

  const unreachableCount = useMemo(
    () =>
      rows.filter((r) => /unreachable|vpn|forbidden|cloudflare|timed out|connection/i.test(r.notes || "")).length,
    [rows],
  );

  function toggleGroup(op: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      const closing = next.has(op);
      if (closing) next.delete(op);
      else next.add(op);
      if (closing && scrollRestoreY != null) {
        requestAnimationFrame(() => {
          window.scrollTo({ top: scrollRestoreY, behavior: "auto" });
          setScrollRestoreY(null);
        });
      }
      return next;
    });
  }

  function openOperationDetail(op: string, status?: string) {
    setScrollRestoreY(window.scrollY);
    setFilterOp(op);
    setFilterStatus(status && status !== "all" ? status : "all");
    setExpanded(new Set([op]));
    requestAnimationFrame(() => {
      detailAnchorRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  function sortedOpRows(opRows: ComparisonRow[]): ComparisonRow[] {
    return [...opRows].sort((a, b) =>
      enrichPathSortKey(a.field_path).localeCompare(enrichPathSortKey(b.field_path)),
    );
  }

  function renderFieldCards(opRows: ComparisonRow[]) {
    return (
      <div className="result-fields">
        {groupByEnvelope(opRows).map((section) => {
          const useBranches =
            section.key === "subject.enrichedSnapshot" ||
            section.key === "actor.enrichedSnapshot";
          const branches = useBranches
            ? groupBySnapshotBranch(section.rows)
            : [{ branch: "", rows: section.rows }];
          return (
            <div key={section.key} className="result-envelope-section">
              <h4 className="result-envelope-title">{section.label}</h4>
              {branches.map(({ branch, rows: branchRows }) => (
                <div key={branch || "_"} className="result-snapshot-branch">
                  {branch ? <h5 className="result-branch-title">{branch}</h5> : null}
                  {branchRows.map((r, i) => (
                    <article
                      key={`${r.field_path}-${i}`}
                      className={`result-field-card ${statusClass(r.match_status)}`}
                    >
                      <div className="result-field-head">
                        <div>
                          <span className="field-name">{displayField(r)}</span>
                          <span className="field-json-path" title="Enrich JSON path">
                            {r.field_path}
                          </span>
                        </div>
                        <span className={`badge ${statusClass(r.match_status)}`}>
                          {r.match_status}
                        </span>
                      </div>
                      <div className="result-field-compare">
                        <div className="value-box enriched">
                          <span className="value-label">Enriched</span>
                          <code>{r.value_in_enriched || "—"}</code>
                        </div>
                        <div className="value-box source">
                          <span className="value-label" title={r.source_api || undefined}>
                            {sourceLabel(r)}
                          </span>
                          <code>{r.value_in_source || "—"}</code>
                        </div>
                      </div>
                      {r.notes && <p className="field-notes">{r.notes}</p>}
                    </article>
                  ))}
                </div>
              ))}
            </div>
          );
        })}
      </div>
    );
  }

  function renderFieldList(opRows: ComparisonRow[]) {
    const rows = sortedOpRows(opRows);
    return (
      <div className="result-table-wrap result-list-wrap">
        <table className="result-table result-list-table">
          <thead>
            <tr>
              <th className="result-list-num">#</th>
              <th>Enriched JSON path</th>
              <th>Enriched</th>
              <th>Source</th>
              <th>Source value</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={`${r.field_path}-${i}`} className={statusClass(r.match_status)}>
                <td className="result-list-num">{i + 1}</td>
                <td className="result-list-path" title={r.source_api || undefined}>
                  <code>{r.field_path}</code>
                </td>
                <td className="result-list-value">
                  <code>{r.value_in_enriched || "—"}</code>
                </td>
                <td className="result-list-source" title={r.source_api || undefined}>
                  {sourceLabel(r)}
                </td>
                <td className="result-list-value">
                  <code>{r.value_in_source || "—"}</code>
                </td>
                <td>
                  <span className={`badge ${statusClass(r.match_status)}`}>{r.match_status}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  function setTrackStatus(op: string, status: TrackStatus) {
    setTrack((prev) => {
      const next = { ...prev };
      if (status === "unreviewed") delete next[op];
      else next[op] = status;
      return next;
    });
  }

  function clearFilters() {
    setFilterOp("");
    setFilterStatus("all");
    setCoverageOutcome("all");
    setFilterCategory("all");
    setFilterEnv([]);
    setFilterService([]);
    setExpanded(new Set());
    if (scrollRestoreY != null) {
      requestAnimationFrame(() => {
        window.scrollTo({ top: scrollRestoreY, behavior: "auto" });
        setScrollRestoreY(null);
      });
    }
  }

  return (
    <section className="panel">
      <header className="panel-head panel-head-row">
        <div>
          <h2>Comparison results</h2>
          <p>Field-level source vs enriched validation.</p>
        </div>
        <div className="result-head-actions">
          {filterOp && (
            <div className="filter-actions inline-actions">
              <button type="button" onClick={() => setExpanded(new Set(grouped.map(([op]) => op)))}>
                Expand all
              </button>
              <button type="button" onClick={() => setExpanded(new Set())}>
                Collapse all
              </button>
            </div>
          )}
          {filtered.length > 0 && (
            <button
              type="button"
              className="primary"
              disabled={exportBusy}
              onClick={() => {
                setExportBusy(true);
                const ops =
                  selectedOps.size > 0
                    ? [...selectedOps]
                    : filterOp
                      ? [filterOp]
                      : coverageRows.map((r) => r.operation);
                void exportComparisonExcel(ops)
                  .catch((e) => setRefreshError(String(e)))
                  .finally(() => setExportBusy(false));
              }}
            >
              {exportBusy ? "Exporting…" : "Download Excel"}
            </button>
          )}
        </div>
      </header>

      <div className="filter-row compare-filter-row">
        <label className="filter-field">
          <span>view</span>
          <select
            value={sourceMode}
            onChange={(e) => setSourceMode(e.target.value as SourceMode)}
          >
            <option value="latest">Latest per operation</option>
            <option value="job">Single job run</option>
          </select>
        </label>
        {sourceMode === "job" && (
          <label className="filter-field">
            <span>job</span>
            <select value={activeId ?? ""} onChange={(e) => setActiveId(e.target.value)}>
              {jobs.map((j) => (
                <option key={j.id} value={j.id}>
                  {j.id.slice(0, 8)} — {j.status} — {(j.params.operations as string[])?.join(", ") ?? "all"}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="filter-field">
          <span>operation</span>
          <input value={filterOp} onChange={(e) => setFilterOp(e.target.value)} placeholder="activateFamily" />
        </label>
        <label className="filter-field">
          <span>category</span>
          <select value={filterCategory} onChange={(e) => setFilterCategory(e.target.value)}>
            <option value="all">All categories</option>
            {(categories?.categories ?? []).map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </label>
        <MultiSelect
          label="environment"
          options={environmentOptions}
          selected={filterEnv}
          onChange={setFilterEnv}
        />
        <MultiSelect
          label="service"
          options={serviceOptions}
          selected={filterService}
          onChange={setFilterService}
        />
        <label className="filter-field">
          <span>status</span>
          <select value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)}>
            <option value="all">All</option>
            <option value="PASS">PASS</option>
            <option value="FAIL">FAIL</option>
            <option value="SKIP">SKIP</option>
          </select>
        </label>
        <div className="filter-actions">
          <button type="button" onClick={clearFilters}>Clear</button>
        </div>
      </div>

      {sourceMode === "latest" && (
        <p className="result-source-banner">
          Showing the <strong>latest stored comparison for each operation</strong>
          {latest?.count ? ` (${latest.count} operation${latest.count === 1 ? "" : "s"})` : ""}.
          {refreshJobId ? (
            <> Compare in progress… snapshots update here as each operation finishes.</>
          ) : (
            <> Run Compare from the Compare tab; results appear here automatically.</>
          )}
        </p>
      )}

      {refreshError && <p className="error">{refreshError}</p>}

      {unreachableCount > 0 && (
        <div className="banner warn">
          <strong>{unreachableCount} field(s) could not be validated because the source APIs
          (CMS / UMS / Discovery / AMS) were unreachable or forbidden.</strong> These are marked <b>N/A</b> / <b>SKIP</b>, not
          failures. Connect to VPN, verify AMS headers via the curl below in your notes, then re-run Compare.
        </div>
      )}

      {job?.error && sourceMode === "job" && <p className="error">{job.error}</p>}

      <div className="actions" style={{ marginBottom: 12 }}>
        <button type="button" className="primary outline" disabled={failureLogBusy} onClick={openFailureLog}>
          {failureLogBusy ? "Loading…" : "Failure log"}
        </button>
        <span className="muted">
          Common FAIL patterns across stored comparisons · count + mongo query / curl to investigate
        </span>
      </div>

      {showFailureLog && failureLog && (
        <div className="modal-backdrop" onClick={() => setShowFailureLog(false)} role="presentation">
          <div className="modal-card" onClick={(e) => e.stopPropagation()} role="dialog">
            <div className="modal-head">
              <strong>Failure log</strong>
              <span className="muted">
                · {failureLog.total_fail_rows} FAIL row{failureLog.total_fail_rows === 1 ? "" : "s"}
                {failureLog.operations_with_fails != null
                  ? ` · ${failureLog.operations_with_fails} ops`
                  : ""}
              </span>
              <button type="button" className="link-btn" onClick={() => setShowFailureLog(false)}>
                close ✕
              </button>
            </div>
            {failureLog.error && <p className="error">{failureLog.error}</p>}
            {(failureLog.groups || []).length === 0 ? (
              <p className="muted">No FAIL rows in the latest comparison store.</p>
            ) : (
              <div className="result-table-wrap compact-table-wrap">
                <table className="result-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Pattern</th>
                      <th>Field</th>
                      <th>Source</th>
                      <th>Ops</th>
                      <th>Investigate</th>
                    </tr>
                  </thead>
                  <tbody>
                    {failureLog.groups.map((g) => (
                      <tr key={g.key}>
                        <td><strong>{g.count}</strong></td>
                        <td><code>{g.pattern}</code></td>
                        <td><code>{g.field_path}</code></td>
                        <td>{g.source_system}</td>
                        <td className="muted" title={g.operations.join(", ")}>
                          {g.operations.slice(0, 4).join(", ")}
                          {g.operations.length > 4 ? ` +${g.operations.length - 4}` : ""}
                        </td>
                        <td>
                          {g.sample_notes && <div className="muted">{g.sample_notes}</div>}
                          {g.mongo_query && (
                            <pre className="failure-log-pre">{g.mongo_query}</pre>
                          )}
                          {g.curl && (
                            <pre className="failure-log-pre">{g.curl}</pre>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {allCoverageRows.length > 0 && (
        <div className="coverage-panel" ref={coverageListRef}>
          <div className="coverage-head">
            <div>
              <h3>Operations coverage</h3>
              <p className="muted">
                {coverageTotals.ops} shown · {coverageTotals.passed} pass · {coverageTotals.failed} fail ·{" "}
                {coverageTotals.skipped} skip
                <span className="coverage-track-summary">
                  {" "}· track {coverageTotals.covered} covered / {coverageTotals.needs} enhance /{" "}
                  {coverageTotals.unreviewed} open
                </span>
              </p>
            </div>
            <div className="coverage-outcome-filters" role="group" aria-label="Coverage outcome filter">
              {(
                [
                  ["all", `All (${coverageCounts.all})`],
                  ["pass", `Fully pass (${coverageCounts.pass})`],
                  ["failed", `Failed (${coverageCounts.failed})`],
                  ["partial", `Partial / skip (${coverageCounts.partial})`],
                ] as const
              ).map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  className={coverageOutcome === id ? "chip active" : "chip"}
                  onClick={() => setCoverageOutcome(id)}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          {highlightActive && highlightSet.size > 0 && (
            <div className="banner" style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <span>
                Showing the <strong>{coverageRows.length}</strong> operation
                {coverageRows.length === 1 ? "" : "s"} you just compared.
              </span>
              <button type="button" className="link-btn" onClick={() => setHighlightActive(false)}>
                Show all results
              </button>
            </div>
          )}
          <div className="coverage-bulk-actions" style={{ display: "flex", alignItems: "center", gap: 12, margin: "4px 0 8px" }}>
            <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <input
                type="checkbox"
                checked={coverageRows.length > 0 && coverageRows.every((r) => selectedOps.has(r.operation))}
                ref={(el) => {
                  if (el) {
                    const some = coverageRows.some((r) => selectedOps.has(r.operation));
                    const all = coverageRows.length > 0 && coverageRows.every((r) => selectedOps.has(r.operation));
                    el.indeterminate = some && !all;
                  }
                }}
                onChange={(e) => {
                  setSelectedOps((prev) => {
                    const next = new Set(prev);
                    if (e.target.checked) coverageRows.forEach((r) => next.add(r.operation));
                    else coverageRows.forEach((r) => next.delete(r.operation));
                    return next;
                  });
                }}
              />
              <span className="muted">select shown</span>
            </label>
          </div>
          <div className="result-table-wrap compact-table-wrap">
            <table className="result-table coverage-table">
              <thead>
                <tr>
                  <th className="coverage-col-narrow" title="Select for delete"></th>
                  <th>Operation</th>
                  <th>Category</th>
                  <th>Env</th>
                  <th>Service</th>
                  <th>Compared</th>
                  <th>PASS</th>
                  <th>FAIL</th>
                  <th>SKIP</th>
                  <th className="coverage-col-narrow" title="Review status">✓</th>
                </tr>
              </thead>
              <tbody>
                {coverageRows.length === 0 && (
                  <tr>
                    <td colSpan={10} className="muted">
                      No operations match this coverage filter.
                    </td>
                  </tr>
                )}
                {coverageRows.map((r) => (
                  <tr key={r.operation} className={r.failed ? "fail" : r.skipped ? "skip" : "pass"}>
                    <td className="coverage-col-narrow">
                      <input
                        type="checkbox"
                        checked={selectedOps.has(r.operation)}
                        onChange={() => toggleSelectOp(r.operation)}
                        aria-label={`Select ${r.operation}`}
                      />
                    </td>
                    <td>
                      <button
                        type="button"
                        className="link-btn coverage-op-link"
                        onClick={() => openOperationDetail(r.operation)}
                      >
                        {r.operation}
                      </button>
                    </td>
                    <td>{r.category}</td>
                    <td>{r.environment}</td>
                    <td>{r.service}</td>
                    <td className="coverage-compared" title={r.comparedAt || undefined}>
                      {r.comparedAt ? formatComparedAt(r.comparedAt) : "—"}
                    </td>
                    <td>
                      <button
                        type="button"
                        className="badge-btn"
                        title="Show PASS fields"
                        onClick={() => openOperationDetail(r.operation, "PASS")}
                      >
                        <span className="badge pass">{r.passed}</span>
                      </button>
                    </td>
                    <td>
                      <button
                        type="button"
                        className="badge-btn"
                        title="Show FAIL fields"
                        disabled={!r.failed}
                        onClick={() => openOperationDetail(r.operation, "FAIL")}
                      >
                        <span className={`badge ${r.failed ? "fail" : "na"}`}>{r.failed}</span>
                      </button>
                    </td>
                    <td>
                      <button
                        type="button"
                        className="badge-btn"
                        title="Show SKIP fields"
                        disabled={!r.skipped}
                        onClick={() => openOperationDetail(r.operation, "SKIP")}
                      >
                        <span className={`badge ${r.skipped ? "skip" : "na"}`}>{r.skipped}</span>
                      </button>
                    </td>
                    <td className="coverage-col-narrow">
                      <details className="coverage-track-menu">
                        <summary
                          className={`track-pill track-mini track-${r.track}`}
                          title="Set review status"
                        >
                          {r.track === "covered" ? "✓" : r.track === "needs_enhancement" ? "!" : "·"}
                        </summary>
                        <div className="coverage-track-menu-body">
                          <button type="button" onClick={() => setTrackStatus(r.operation, "covered")}>
                            Covered
                          </button>
                          <button type="button" onClick={() => setTrackStatus(r.operation, "needs_enhancement")}>
                            Needs enhancement
                          </button>
                          {r.track !== "unreviewed" && (
                            <button type="button" onClick={() => setTrackStatus(r.operation, "unreviewed")}>
                              Reset
                            </button>
                          )}
                          <button
                            type="button"
                            className="danger"
                            disabled={deletingOp === r.operation}
                            onClick={() => onDeleteResult(r.operation)}
                          >
                            {deletingOp === r.operation ? "Deleting…" : "Delete result"}
                          </button>
                        </div>
                      </details>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div ref={detailAnchorRef} className="result-detail-anchor">
        {filterOp ? (
          <>
            <div className="result-detail-bar">
              <span>
                Field details for <strong>{filterOp}</strong>
              </span>
              <div className="result-detail-bar-actions">
                <button
                  type="button"
                  className="primary outline"
                  onClick={() => setVerifyCtx({ operation: filterOp })}
                >
                  Verify in UI
                </button>
                <div className="result-view-toggle" role="group" aria-label="Field detail view">
                  <button
                    type="button"
                    className={viewMode === "cards" ? "active" : ""}
                    onClick={() => setViewMode("cards")}
                  >
                    Cards
                  </button>
                  <button
                    type="button"
                    className={viewMode === "list" ? "active" : ""}
                    onClick={() => setViewMode("list")}
                  >
                    List
                  </button>
                </div>
                <button type="button" className="link-btn" onClick={clearFilters}>
                  ← Back to coverage list
                </button>
                <button
                  type="button"
                  className="danger"
                  disabled={deletingOp === filterOp}
                  onClick={() => void onDeleteResult(filterOp)}
                >
                  {deletingOp === filterOp ? "Deleting…" : "Delete result"}
                </button>
              </div>
            </div>
            {verifyCtx && (
              <VerifyInUiModal context={verifyCtx} onClose={() => setVerifyCtx(null)} />
            )}
            <div className="result-groups">
              {grouped.map(([operation, opRows]) => {
                const open = expanded.has(operation);
                const failCount = opRows.filter((r) => r.match_status === "FAIL").length;
                const total = opRows.length;
                const comparedAt = comparedAtByOp.get(operation);
                return (
                  <section key={operation} className="result-group" id={`op-${operation}`}>
                    <button type="button" className="result-group-head" onClick={() => toggleGroup(operation)}>
                      <span className="result-group-title">
                        <span className={`result-status-dot ${failCount ? "fail" : "pass"}`} aria-hidden />
                        {operation}
                        {comparedAt && sourceMode === "latest" && (
                          <span className="result-group-compared" title={comparedAt}>
                            compared {formatComparedAt(comparedAt)}
                          </span>
                        )}
                      </span>
                      <span className="result-group-meta">
                        <span className="result-group-fields">{total} field{total === 1 ? "" : "s"}</span>
                        {failCount > 0 && <span className="result-group-attention">{failCount} to review</span>}
                        <span className="chevron">{open ? "▾" : "▸"}</span>
                      </span>
                    </button>
                    {open && (viewMode === "list" ? renderFieldList(opRows) : renderFieldCards(opRows))}
                  </section>
                );
              })}
              {!filtered.length && (
                <p className="muted">No field rows for this operation.</p>
              )}
            </div>
          </>
        ) : (
          <p className="muted result-detail-hint">
            Click an operation name in the coverage table to open its field-by-field comparison.
          </p>
        )}
      </div>
    </section>
  );
}
