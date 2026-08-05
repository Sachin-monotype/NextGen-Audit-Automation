import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import VerifyInUiModal, { type VerifyInUiContext } from "../components/VerifyInUiModal";
import {
  fetchCategories,
  fetchComparableOperations,
  fetchFailureSummary,
  fetchEnrichedSample,
  fetchJob,
  fetchJobs,
  fetchLatestResults,
  fetchPipelineConfig,
  deleteLatestResult,
  exportComparisonExcel,
  refreshStoredComparisons,
  type CategoryReport,
  type ComparableOperation,
  type ComparisonRow,
  type FailureSummary,
  type Job,
  type LatestComparisonItem,
} from "../api";
import {
  compareScenarioDiscriminators,
  compareScenarioStructure,
  compareScenarioValues,
  structureDiffSummary,
  type ScenarioStructureRow,
  type ScenarioValueRow,
} from "../utils/scenarioCompare";

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

/** Split ``activateFamily(global)(app)`` / ``…(BE)`` → base + scenario label. */
function splitEventScenario(operation: string): { base: string; scenario: string } {
  const raw = String(operation || "").trim();
  let op = raw;
  const tags: string[] = [];
  for (const suffix of ["(BE)", "(UI)", "(be)", "(ui)", "(app)", "(APP)", "(web)", "(WEB)"]) {
    if (op.endsWith(suffix)) {
      tags.push(suffix.replace(/[()]/g, "").toUpperCase());
      op = op.slice(0, -suffix.length);
      break;
    }
  }
  const m = op.match(/^([^(]+)\((.+)\)$/);
  if (m) {
    const scenario = tags.length ? `${m[2]} · ${tags.join(" · ")}` : m[2];
    return { base: m[1], scenario };
  }
  return { base: op, scenario: tags.join(" · ") || "default" };
}

type CoverageRow = {
  operation: string;
  category: string;
  comparedAt: string;
  track: TrackStatus;
  passed: number;
  failed: number;
  skipped: number;
  na: number;
};

type CoverageEventGroup = {
  base: string;
  scenarios: CoverageRow[];
  category: string;
  comparedAt: string;
  passed: number;
  failed: number;
  skipped: number;
  na: number;
};

type CoverageSortKey = "event" | "scenario" | "compared" | "fail";

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

type ScenarioCompareMode = "values" | "structure" | "discriminators";

/** Short resource / "table" name from the source_api path for the Result label. */
function sourceResource(row: ComparisonRow): string {
  const api = (row.source_api || "").toLowerCase();
  const sys = (row.source_system || "").toLowerCase();
  if (!api && !sys) return "";
  if (sys === "graphql" || sys === "trigger" || api.includes("graphql")) return "mutation";
  if (api.includes("/users") || api.includes("idpuserid")) return "users";
  if (api.includes("/profiles") || api.includes("profiles")) return "profiles";
  if (api.includes("/roles") || api.includes("role")) return "roles";
  if (api.includes("/teams") || api.includes("team")) return "teams";
  if (api.includes("/customers") || api.includes("customer")) return "customers";
  if (api.includes("/variations") || api.includes("variations")) return "variations";
  if (api.includes("/styles") || api.includes("styles")) return "styles";
  if (api.includes("/asset") || api.includes("ams") || api.includes("assets")) return "assets";
  if (api.includes("jwt") || api.includes("bearer") || api.includes("token")) return "jwt";
  if (api.includes("raw event") || api.startsWith("raw>") || sys === "raw") return "raw";
  if (api.includes("resolver") || api.includes("enricher") || api.includes("derived")) return "resolver";
  // Fallback: last path segment that looks like a resource
  const parts = api.replace(/\?.*$/, "").split("/").filter(Boolean);
  const last = parts[parts.length - 1] || "";
  if (last && !last.includes("{") && last.length < 24) return last;
  return "";
}

function sourceLabel(row: ComparisonRow): string {
  const resource = sourceResource(row);
  const sys = row.source_system === "Trigger" ? "GraphQL" : row.source_system;
  if (resource) return `Source (${sys}) · ${resource}`;
  return `Source (${sys})`;
}

function rowMatchesFieldSearch(row: ComparisonRow, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return (
    row.field_path.toLowerCase().includes(q) ||
    (row.field || "").toLowerCase().includes(q) ||
    (row.value_in_enriched || "").toLowerCase().includes(q) ||
    (row.value_in_source || "").toLowerCase().includes(q) ||
    (row.notes || "").toLowerCase().includes(q)
  );
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
  /** PP / QA / UAT — Results store is per audit target so stores never mix. */
  const [resultsTarget, setResultsTarget] = useState("qa");
  const [availableTargets, setAvailableTargets] = useState<string[]>(["qa", "pp", "uat"]);
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
  const [fieldSearch, setFieldSearch] = useState("");
  const [scenarioCompareOps, setScenarioCompareOps] = useState<string[] | null>(null);
  const [scenarioCompareMode, setScenarioCompareMode] = useState<ScenarioCompareMode>("values");
  const [scenarioCompareBusy, setScenarioCompareBusy] = useState(false);
  const [scenarioCompareError, setScenarioCompareError] = useState("");
  const [scenarioValueRows, setScenarioValueRows] = useState<ScenarioValueRow[]>([]);
  const [scenarioStructureRows, setScenarioStructureRows] = useState<ScenarioStructureRow[]>([]);
  const [scenarioDiscriminatorRows, setScenarioDiscriminatorRows] = useState<ScenarioStructureRow[]>([]);
  const [scenarioStructureFilter, setScenarioStructureFilter] = useState("");
  const [scenarioShowAllDiffs, setScenarioShowAllDiffs] = useState(false);
  /**
   * Scenario expand state. ``none`` / ``all`` are sticky global modes so Hide/Show
   * is not undone by data refreshes. ``custom`` uses ``eventGroupsOpen``.
   */
  const [scenarioExpandMode, setScenarioExpandMode] = useState<"none" | "all" | "custom">("none");
  const [eventGroupsOpen, setEventGroupsOpen] = useState<Set<string>>(() => new Set());
  const [coverageSort, setCoverageSort] = useState<{ key: CoverageSortKey; dir: "asc" | "desc" }>({
    key: "compared",
    dir: "desc",
  });

  async function refreshAllInStore() {
    setRefreshError("");
    setRefreshAllBusy(true);
    try {
      const res = await refreshStoredComparisons(undefined, resultsTarget);
      setRefreshJobId(res.job.id);
      setActiveId(res.job.id);
      setSourceMode("job");
      loadLatest();
    } catch (e) {
      setRefreshError(String(e));
    } finally {
      setRefreshAllBusy(false);
    }
  }

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
    fetchLatestResults(resultsTarget)
      .then((data) => {
        setLatest(data);
        if (data.audit_target) setResultsTarget(data.audit_target);
        if (data.available_targets?.length) setAvailableTargets(data.available_targets);
      })
      .catch(() => setLatest(null));
  }, [resultsTarget]);

  useEffect(() => {
    fetchPipelineConfig()
      .then((cfg) => {
        const t = (cfg.target || "qa").toLowerCase();
        if (t) setResultsTarget(t);
      })
      .catch(() => {});
  }, []);

  const [deletingOp, setDeletingOp] = useState<string | null>(null);
  /** Bulk-select operations in the coverage table for deletion. */
  const [selectedOps, setSelectedOps] = useState<Set<string>>(new Set());
  const [exportBusy, setExportBusy] = useState(false);
  const [refreshJobId, setRefreshJobId] = useState<string | null>(null);
  const [refreshAllBusy, setRefreshAllBusy] = useState(false);
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
    const fromBase = metaByOp.get(base);
    if (fromBase) return fromBase;
    return metaByOp.get(`${base}(BE)`) ?? metaByOp.get(`${base}(UI)`);
  }

  /** Drop ``op(UI)`` / bare ``op(BE)`` when the unlabeled scenario is also listed. */
  function dedupeCoverageRows<T extends { operation: string }>(rows: T[]): T[] {
    const byBase = new Map<string, T>();
    for (const row of rows) {
      const ui = row.operation.match(/^(.+)\(UI\)$/i);
      const be = row.operation.match(/^([^(]+)\(BE\)$/);
      const base = ui ? ui[1] : be ? be[1] : row.operation;
      const prev = byBase.get(base);
      if (!prev) {
        byBase.set(base, {
          ...row,
          operation: ui ? base : row.operation,
        });
        continue;
      }
      const canon = { ...row, operation: ui ? base : row.operation };
      if (!ui && !be && (/\(UI\)$/i.test(prev.operation) || /\(BE\)$/.test(prev.operation))) {
        byBase.set(base, canon);
      }
    }
    return [...byBase.values()];
  }

  function categoryForOperation(operation: string): string {
    if (byOperation[operation]) return byOperation[operation];
    const base = operation.includes("(") ? operation.split("(", 1)[0] : operation;
    if (byOperation[base]) return byOperation[base];
    return metaForOperation(operation)?.category || "—";
  }

  const scopedRows = useMemo(() => {
    return rows.filter((r) => {
      if (filterOp && !r.operation.toLowerCase().includes(filterOp.toLowerCase())) return false;
      if (filterCategory !== "all" && categoryForOperation(r.operation) !== filterCategory) return false;
      return true;
    });
  }, [rows, filterOp, filterCategory, byOperation, metaByOp]);

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
    return dedupeCoverageRows(
      [...map.entries()].map(([operation, opRows]) => {
        const summary = summarizeOp(opRows);
        const status: TrackStatus = track[operation] || "unreviewed";
        return {
          operation,
          category: categoryForOperation(operation),
          comparedAt: comparedAtByOp.get(operation) || "",
          track: status,
          ...summary,
        };
      }),
    );
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

  const coverageGroups = useMemo((): CoverageEventGroup[] => {
    const byBase = new Map<string, CoverageRow[]>();
    for (const r of coverageRows as CoverageRow[]) {
      const { base } = splitEventScenario(r.operation);
      const list = byBase.get(base) ?? [];
      list.push(r);
      byBase.set(base, list);
    }
    const groups: CoverageEventGroup[] = [];
    for (const [base, scenarios] of byBase) {
      const sortScenario = (a: CoverageRow, b: CoverageRow) => {
        const sa = splitEventScenario(a.operation).scenario;
        const sb = splitEventScenario(b.operation).scenario;
        const byName = sa.localeCompare(sb);
        if (coverageSort.key === "scenario") {
          return coverageSort.dir === "asc" ? byName : -byName;
        }
        if (coverageSort.key === "compared") {
          const byDate = (a.comparedAt || "").localeCompare(b.comparedAt || "");
          const ordered = coverageSort.dir === "asc" ? byDate : -byDate;
          return ordered !== 0 ? ordered : byName;
        }
        if (coverageSort.key === "fail") {
          const byFail = a.failed - b.failed;
          const ordered = coverageSort.dir === "asc" ? byFail : -byFail;
          return ordered !== 0 ? ordered : byName;
        }
        return byName;
      };
      scenarios.sort(sortScenario);
      groups.push({
        base,
        scenarios,
        category: scenarios[0]?.category || "—",
        comparedAt: scenarios.reduce(
          (best, s) => ((s.comparedAt || "") > best ? s.comparedAt || "" : best),
          "",
        ),
        passed: scenarios.reduce((n, s) => n + s.passed, 0),
        failed: scenarios.reduce((n, s) => n + s.failed, 0),
        skipped: scenarios.reduce((n, s) => n + s.skipped, 0),
        na: scenarios.reduce((n, s) => n + s.na, 0),
      });
    }
    groups.sort((a, b) => {
      let cmp = 0;
      if (coverageSort.key === "event") {
        cmp = a.base.localeCompare(b.base);
      } else if (coverageSort.key === "scenario") {
        const sa = splitEventScenario(a.scenarios[0]?.operation || "").scenario;
        const sb = splitEventScenario(b.scenarios[0]?.operation || "").scenario;
        cmp = sa.localeCompare(sb) || a.base.localeCompare(b.base);
      } else if (coverageSort.key === "fail") {
        cmp = a.failed - b.failed;
      } else {
        cmp = (a.comparedAt || "").localeCompare(b.comparedAt || "");
      }
      if (cmp === 0) cmp = a.base.localeCompare(b.base);
      return coverageSort.dir === "asc" ? cmp : -cmp;
    });
    return groups;
  }, [coverageRows, coverageSort]);

  function isScenarioGroupOpen(base: string, multi: boolean): boolean {
    if (!multi) return false;
    if (scenarioExpandMode === "all") return true;
    if (scenarioExpandMode === "none") return false;
    return eventGroupsOpen.has(base);
  }

  function showAllScenarios() {
    setScenarioExpandMode("all");
    setEventGroupsOpen(new Set());
  }

  function hideAllScenarios() {
    setScenarioExpandMode("none");
    setEventGroupsOpen(new Set());
  }

  function toggleScenarioGroup(base: string, allBases: string[]) {
    if (scenarioExpandMode === "all") {
      const next = new Set(allBases.filter((b) => b !== base));
      setScenarioExpandMode("custom");
      setEventGroupsOpen(next);
      return;
    }
    if (scenarioExpandMode === "none") {
      setScenarioExpandMode("custom");
      setEventGroupsOpen(new Set([base]));
      return;
    }
    setEventGroupsOpen((prev) => {
      const next = new Set(prev);
      if (next.has(base)) next.delete(base);
      else next.add(base);
      return next;
    });
  }

  function toggleSelectOp(op: string) {
    setSelectedOps((prev) => {
      const next = new Set(prev);
      if (next.has(op)) next.delete(op);
      else next.add(op);
      return next;
    });
  }

  async function openScenarioCompare(ops: string[]) {
    if (ops.length < 2 || !latest?.items) return;
    setScenarioCompareOps(ops);
    setScenarioCompareMode("discriminators");
    setScenarioCompareError("");
    setScenarioCompareBusy(true);
    setScenarioShowAllDiffs(false);
    setScenarioStructureFilter("");
    try {
      const enrichedByOp: Record<string, unknown | null> = {};
      for (const op of ops) {
        try {
          const sample = await fetchEnrichedSample(op);
          enrichedByOp[op] = sample.enriched;
        } catch {
          enrichedByOp[op] = null;
        }
      }
      setScenarioValueRows(compareScenarioValues(ops, latest.items, enrichedByOp));
      setScenarioStructureRows(compareScenarioStructure(ops, enrichedByOp as Record<string, unknown>));
      setScenarioDiscriminatorRows(compareScenarioDiscriminators(ops, enrichedByOp));
    } catch (e) {
      setScenarioCompareError(String(e));
      setScenarioValueRows([]);
      setScenarioStructureRows([]);
      setScenarioDiscriminatorRows([]);
    } finally {
      setScenarioCompareBusy(false);
    }
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

  /** Unique event bases vs scenario variants shown in the coverage table. */
  const eventGroupCounts = useMemo(() => {
    const events = new Set<string>();
    for (const r of coverageRows) {
      events.add(r.operation.split("(", 1)[0] || r.operation);
    }
    return { events: events.size, scenarios: coverageRows.length };
  }, [coverageRows]);

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
      rows.filter((r) =>
        /unreachable|vpn|cloudflare|timed out|connection/i.test(r.notes || ""),
      ).length,
    [rows],
  );
  const authFailCount = useMemo(
    () =>
      rows.filter((r) =>
        /401 |unauthorized|403 |forbidden|discovery token missing|typesense\/middleware not queried/i.test(
          r.notes || "",
        ),
      ).length,
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
    const q = fieldSearch.trim();
    const filtered = q ? opRows.filter((r) => rowMatchesFieldSearch(r, q)) : opRows;
    return [...filtered].sort((a, b) =>
      enrichPathSortKey(a.field_path).localeCompare(enrichPathSortKey(b.field_path)),
    );
  }

  function renderFieldCards(opRows: ComparisonRow[]) {
    const rows = sortedOpRows(opRows);
    if (!rows.length) {
      return <p className="muted">No fields match your search.</p>;
    }
    return (
      <div className="result-fields">
        {groupByEnvelope(rows).map((section) => {
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
    if (!rows.length) {
      return <p className="muted">No fields match your search.</p>;
    }
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
    setFieldSearch("");
    setCoverageOutcome("all");
    setFilterCategory("all");
    setExpanded(new Set());
    setScenarioExpandMode("none");
    setEventGroupsOpen(new Set());
    if (scrollRestoreY != null) {
      requestAnimationFrame(() => {
        window.scrollTo({ top: scrollRestoreY, behavior: "auto" });
        setScrollRestoreY(null);
      });
    }
  }

  function toggleCoverageSort(key: CoverageSortKey) {
    setCoverageSort((prev) => {
      if (prev.key === key) {
        return { key, dir: prev.dir === "asc" ? "desc" : "asc" };
      }
      return { key, dir: key === "compared" || key === "fail" ? "desc" : "asc" };
    });
  }

  function sortHeaderLabel(key: CoverageSortKey, label: string) {
    if (coverageSort.key !== key) return label;
    return `${label} ${coverageSort.dir === "asc" ? "↑" : "↓"}`;
  }

  return (
    <section className="panel">
      <header className="panel-head panel-head-row">
        <div>
          <h2>Comparison results</h2>
          <p>Field-level source vs enriched validation.</p>
        </div>
        <div className="result-head-actions">
          {(coverageRows.length > 0 || filtered.length > 0) && (
            <button
              type="button"
              className="primary"
              disabled={exportBusy}
              title={
                selectedOps.size > 0
                  ? `Export ${selectedOps.size} selected operation(s) — one sheet each`
                  : filterOp
                    ? `Export ${filterOp}`
                    : "Export all visible operations — one sheet per operation"
              }
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
              {exportBusy
                ? "Exporting…"
                : selectedOps.size > 0
                  ? `Download Excel (${selectedOps.size})`
                  : "Download Excel"}
            </button>
          )}
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
        </div>
      </header>

      <div className="filter-row compare-filter-row">
        <label className="filter-field">
          <span>audit env</span>
          <select
            value={resultsTarget}
            onChange={(e) => setResultsTarget(e.target.value)}
            title="Show Results for this audit target (PP / QA / UAT stores are separate)"
          >
            {availableTargets.map((t) => (
              <option key={t} value={t}>
                {t.toUpperCase()}
              </option>
            ))}
          </select>
        </label>
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
          <span>category</span>
          <select value={filterCategory} onChange={(e) => setFilterCategory(e.target.value)}>
            <option value="all">All categories</option>
            {(categories?.categories ?? []).map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </label>
        <label className="filter-field">
          <span>field status</span>
          <select value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)}>
            <option value="all">All</option>
            <option value="PASS">PASS</option>
            <option value="FAIL">FAIL</option>
            <option value="SKIP">SKIP</option>
          </select>
        </label>
        <div className="filter-actions results-toolbar-actions">
          <button
            type="button"
            className="primary"
            disabled={refreshAllBusy || !latest?.count}
            onClick={() => void refreshAllInStore()}
            title={`Re-run Compare for every operation in the ${resultsTarget.toUpperCase()} store`}
          >
            {refreshAllBusy
              ? "Re-comparing…"
              : `Re-compare (${resultsTarget.toUpperCase()})`}
          </button>
          <button
            type="button"
            className="primary outline"
            disabled={failureLogBusy}
            onClick={openFailureLog}
          >
            {failureLogBusy
              ? "Loading…"
              : failureLog
                ? `Failures (${failureLog.total_fail_rows})`
                : "Failure log"}
          </button>
          <button type="button" onClick={clearFilters}>Clear</button>
        </div>
      </div>

      {refreshError && <p className="error">{refreshError}</p>}

      {unreachableCount > 0 && (
        <div className="banner warn">
          <strong>
            {unreachableCount} field(s) could not be validated because the source APIs (CMS /
            UMS / Discovery / AMS) were unreachable (network / VPN / Cloudflare).
          </strong>{" "}
          These are marked <b>N/A</b>, not failures. Connect to VPN, then re-run Compare.
        </div>
      )}

      {authFailCount > 0 && (
        <div className="banner error">
          <strong>
            {authFailCount} field(s) failed source auth (401/403) — often Discovery/Typesense
            with an M2M or expired Bearer.
          </strong>{" "}
          These are marked <b>FAIL</b>. Refresh a user SSO / password-grant token
          (``DISCOVERY_BEARER_TOKEN`` / ``NEXTGEN_BEARER_TOKEN``), then re-run Compare.
        </div>
      )}

      {job?.error && sourceMode === "job" && <p className="error">{job.error}</p>}

      {allCoverageRows.length === 0 && (
        <div className="actions" style={{ marginBottom: 12 }}>
          <button
            type="button"
            className="primary outline"
            disabled={failureLogBusy}
            onClick={openFailureLog}
          >
            {failureLogBusy ? "Loading…" : "Failure log"}
          </button>
        </div>
      )}

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

      {scenarioCompareOps && scenarioCompareOps.length >= 2 && (
        <div
          className="modal-backdrop"
          onClick={() => setScenarioCompareOps(null)}
          role="presentation"
        >
          <div
            className="modal-card scenario-compare-modal"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
          >
            <div className="modal-head">
              <strong>Scenario comparison</strong>
              <span className="muted"> · {scenarioCompareOps.join(" vs ")}</span>
              <button type="button" className="link-btn" onClick={() => setScenarioCompareOps(null)}>
                close ✕
              </button>
            </div>
            <div className="result-view-toggle" role="group" aria-label="Scenario compare mode">
              <button
                type="button"
                className={scenarioCompareMode === "discriminators" ? "active" : ""}
                onClick={() => setScenarioCompareMode("discriminators")}
              >
                Scenario keys
              </button>
              <button
                type="button"
                className={scenarioCompareMode === "values" ? "active" : ""}
                onClick={() => setScenarioCompareMode("values")}
              >
                All values
              </button>
              <button
                type="button"
                className={scenarioCompareMode === "structure" ? "active" : ""}
                onClick={() => setScenarioCompareMode("structure")}
              >
                JSON structure
              </button>
            </div>
            {scenarioCompareError && <p className="error">{scenarioCompareError}</p>}
            {scenarioCompareBusy && <p className="muted">Loading enriched JSON for scenario diff…</p>}
            {!scenarioCompareBusy && scenarioCompareMode === "discriminators" && (
              <>
                <p className="muted" style={{ marginBottom: 8 }}>
                  Touchpoint-specific fields from <code>subject.metadata.input</code> (listIds, listType,
                  projectIds, …) — what makes list vs global vs project different.
                  {scenarioDiscriminatorRows.length > 0
                    ? ` ${scenarioDiscriminatorRows.length} difference(s).`
                    : " No differences found — check enriched samples loaded."}
                </p>
                <div className="result-table-wrap compact-table-wrap">
                  <table className="result-table">
                    <thead>
                      <tr>
                        <th>Metadata input path</th>
                        {scenarioCompareOps.map((op) => (
                          <th key={op}>{op}</th>
                        ))}
                        <th>Diff</th>
                      </tr>
                    </thead>
                    <tbody>
                      {scenarioDiscriminatorRows.length === 0 ? (
                        <tr>
                          <td colSpan={scenarioCompareOps.length + 2} className="muted">
                            No scenario-key differences — enriched samples may be missing metadata.input.
                          </td>
                        </tr>
                      ) : (
                        scenarioDiscriminatorRows.map((r) => (
                          <tr key={r.path} className={r.kind === "value" ? "fail" : "skip"}>
                            <td><code>{r.path}</code></td>
                            {scenarioCompareOps.map((op) => (
                              <td key={op} className="result-list-value">
                                {r.presence[op] ? (
                                  <code>{r.values[op]}</code>
                                ) : (
                                  <span className="badge na">—</span>
                                )}
                              </td>
                            ))}
                            <td>
                              <span className={`badge ${r.kind === "value" ? "fail" : "skip"}`}>
                                {r.kind === "value" ? "VALUE" : "ONLY ONE"}
                              </span>
                            </td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              </>
            )}
            {!scenarioCompareBusy && scenarioCompareMode === "values" ? (
              <>
                <label className="filter-field" style={{ marginBottom: 8, display: "flex", gap: 12, alignItems: "center" }}>
                  <span>filter</span>
                  <input
                    value={fieldSearch}
                    onChange={(e) => setFieldSearch(e.target.value)}
                    placeholder="attribute or value…"
                  />
                  <label style={{ display: "flex", gap: 6, alignItems: "center", whiteSpace: "nowrap" }}>
                    <input
                      type="checkbox"
                      checked={scenarioShowAllDiffs}
                      onChange={(e) => setScenarioShowAllDiffs(e.target.checked)}
                    />
                    show matching rows too
                  </label>
                </label>
                <div className="result-table-wrap compact-table-wrap">
                  <table className="result-table">
                    <thead>
                      <tr>
                        <th>Field path</th>
                        {scenarioCompareOps.map((op) => (
                          <th key={op}>{op}</th>
                        ))}
                        <th>Match</th>
                      </tr>
                    </thead>
                    <tbody>
                      {scenarioValueRows
                        .filter((r) => scenarioShowAllDiffs || !r.same)
                        .filter((r) => !fieldSearch.trim() || rowMatchesFieldSearch(
                          {
                            operation: "",
                            field: "",
                            field_path: r.field_path,
                            node: "",
                            sub_node: "",
                            layer: "",
                            source_system: "",
                            value_in_source: "",
                            value_in_enriched: Object.values(r.values).join(" "),
                            match_status: r.same ? "PASS" : "FAIL",
                            notes: "",
                            routing_key: "",
                          },
                          fieldSearch,
                        ))
                        .map((r) => (
                          <tr key={r.field_path} className={r.same ? "pass" : "fail"}>
                            <td>
                              <code>{r.field_path}</code>
                              {r.discriminator && (
                                <span className="badge skip" style={{ marginLeft: 6 }}>KEY</span>
                              )}
                            </td>
                            {scenarioCompareOps.map((op) => (
                              <td key={op} className="result-list-value"><code>{r.values[op]}</code></td>
                            ))}
                            <td><span className={`badge ${r.same ? "pass" : "fail"}`}>{r.same ? "SAME" : "DIFF"}</span></td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              </>
            ) : null}
            {!scenarioCompareBusy && scenarioCompareMode === "structure" ? (
              <>
                <label className="filter-field" style={{ marginBottom: 8, display: "flex", gap: 12, alignItems: "center" }}>
                  <span>filter paths</span>
                  <input
                    value={scenarioStructureFilter}
                    onChange={(e) => setScenarioStructureFilter(e.target.value)}
                    placeholder="e.g. metadata.input.listIds…"
                  />
                  <label style={{ display: "flex", gap: 6, alignItems: "center", whiteSpace: "nowrap" }}>
                    <input
                      type="checkbox"
                      checked={!scenarioShowAllDiffs}
                      onChange={(e) => setScenarioShowAllDiffs(!e.target.checked)}
                    />
                    scenario keys only
                  </label>
                </label>
                {(() => {
                  const summary = structureDiffSummary(scenarioStructureRows);
                  return (
                    <p className="muted" style={{ marginBottom: 8 }}>
                      {scenarioStructureRows.length} structural diff(s) · {summary.discriminators} scenario
                      key(s) · deep metadata.result trees hidden
                    </p>
                  );
                })()}
                <div className="result-table-wrap compact-table-wrap">
                  <table className="result-table">
                    <thead>
                      <tr>
                        <th>JSON path</th>
                        {scenarioCompareOps.map((op) => (
                          <th key={op}>{op}</th>
                        ))}
                        <th>Diff</th>
                      </tr>
                    </thead>
                    <tbody>
                      {scenarioStructureRows
                        .filter((r) => {
                          if (!scenarioShowAllDiffs && !r.discriminator) return false;
                          const q = scenarioStructureFilter.trim().toLowerCase();
                          if (!q) return true;
                          return r.path.toLowerCase().includes(q);
                        })
                        .slice(0, 500)
                        .map((r) => (
                          <tr key={r.path} className={r.kind === "value" ? "fail" : "skip"}>
                            <td>
                              <code>{r.path}</code>
                              {r.discriminator && (
                                <span className="badge skip" style={{ marginLeft: 6 }}>KEY</span>
                              )}
                            </td>
                            {scenarioCompareOps.map((op) => (
                              <td key={op} className="result-list-value">
                                {r.presence[op] ? (
                                  <code>{r.values[op]}</code>
                                ) : (
                                  <span className="badge na">—</span>
                                )}
                              </td>
                            ))}
                            <td>
                              <span className={`badge ${r.kind === "value" ? "fail" : "skip"}`}>
                                {r.kind === "value" ? "VALUE" : "MISSING"}
                              </span>
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
                <p className="muted">
                  Normalized paths ([*] indexes). Deep GQL response trees under metadata.result.families.nodes
                  are hidden — use Scenario keys tab for listIds / listType / projectIds.
                </p>
              </>
            ) : null}
          </div>
        </div>
      )}

      {allCoverageRows.length > 0 && (
        <div className="coverage-panel" ref={coverageListRef}>
          <div className="coverage-head">
            <div>
              <h3>Results</h3>
              <p className="muted">
                {eventGroupCounts.events} events · {eventGroupCounts.scenarios} scenarios ·{" "}
                {coverageTotals.passed} pass · {coverageTotals.failed} fail · {coverageTotals.skipped} skip
                <span className="coverage-track-summary">
                  {" "}· track {coverageTotals.covered} covered / {coverageTotals.needs} enhance /{" "}
                  {coverageTotals.unreviewed} open
                </span>
              </p>
            </div>
            <div className="coverage-toolbar">
              <label className="filter-field coverage-op-search">
                <span>Search</span>
                <input
                  value={filterOp}
                  onChange={(e) => setFilterOp(e.target.value)}
                  placeholder="Event or scenario…"
                  aria-label="Search operations in coverage table"
                />
              </label>
              <label className="filter-field">
                <span>Show</span>
                <select
                  value={coverageOutcome}
                  onChange={(e) =>
                    setCoverageOutcome(e.target.value as "all" | "pass" | "failed" | "partial")
                  }
                >
                  <option value="all">All ({coverageCounts.all})</option>
                  <option value="pass">Passed ({coverageCounts.pass})</option>
                  <option value="failed">Failed ({coverageCounts.failed})</option>
                  <option value="partial">Partial / skip ({coverageCounts.partial})</option>
                </select>
              </label>
              {scenarioExpandMode === "all" ||
              (scenarioExpandMode === "custom" && eventGroupsOpen.size > 0) ? (
                <button
                  type="button"
                  className="link-btn"
                  onClick={hideAllScenarios}
                  title="Collapse all scenario rows"
                >
                  Hide scenarios
                </button>
              ) : (
                <button
                  type="button"
                  className="link-btn"
                  onClick={showAllScenarios}
                  title="Expand scenarios under every event"
                >
                  Show scenarios
                </button>
              )}
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
          <div className="coverage-bulk-actions">
            <label className="coverage-select-shown">
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
              <span className="muted">Select shown</span>
            </label>
            {selectedOps.size >= 1 && (
              <button
                type="button"
                className="primary outline"
                disabled={exportBusy}
                onClick={() => {
                  setExportBusy(true);
                  void exportComparisonExcel([...selectedOps])
                    .catch((e) => setRefreshError(String(e)))
                    .finally(() => setExportBusy(false));
                }}
              >
                {exportBusy ? "Exporting…" : `Excel (${selectedOps.size})`}
              </button>
            )}
            {selectedOps.size >= 2 && (
              <button
                type="button"
                className="primary outline"
                onClick={() => void openScenarioCompare([...selectedOps])}
              >
                Compare {selectedOps.size} scenarios
              </button>
            )}
          </div>
          <div className="result-table-wrap compact-table-wrap">
            <table className="result-table coverage-table coverage-grouped">
              <thead>
                <tr>
                  <th className="coverage-col-narrow" title="Select"></th>
                  <th>
                    <button
                      type="button"
                      className="coverage-sort-btn"
                      onClick={() => toggleCoverageSort("event")}
                    >
                      {sortHeaderLabel("event", "Event")}
                    </button>
                  </th>
                  <th>
                    <button
                      type="button"
                      className="coverage-sort-btn"
                      onClick={() => toggleCoverageSort("scenario")}
                    >
                      {sortHeaderLabel("scenario", "Scenario")}
                    </button>
                  </th>
                  <th>Category</th>
                  <th>
                    <button
                      type="button"
                      className="coverage-sort-btn"
                      onClick={() => toggleCoverageSort("compared")}
                    >
                      {sortHeaderLabel("compared", "Compared")}
                    </button>
                  </th>
                  <th className="num">Pass</th>
                  <th className="num">
                    <button
                      type="button"
                      className="coverage-sort-btn"
                      onClick={() => toggleCoverageSort("fail")}
                    >
                      {sortHeaderLabel("fail", "Fail")}
                    </button>
                  </th>
                  <th className="num">Skip</th>
                  <th className="coverage-col-narrow" title="Actions"></th>
                </tr>
              </thead>
              <tbody>
                {coverageGroups.length === 0 && (
                  <tr>
                    <td colSpan={9} className="muted">
                      No operations match this filter.
                    </td>
                  </tr>
                )}
                {coverageGroups.map((g) => {
                  const multi = g.scenarios.length > 1;
                  const open = isScenarioGroupOpen(g.base, multi);
                  const allBases = coverageGroups.filter((x) => x.scenarios.length > 1).map((x) => x.base);
                  const allSelected = g.scenarios.every((s) => selectedOps.has(s.operation));
                  const someSelected = g.scenarios.some((s) => selectedOps.has(s.operation));

                  const renderScenarioRow = (r: CoverageRow, nested: boolean) => {
                    const { base, scenario } = splitEventScenario(r.operation);
                    return (
                      <tr
                        key={r.operation}
                        className={`${r.failed ? "fail" : r.skipped ? "skip" : "pass"}${nested ? " coverage-scenario-row" : ""}`}
                      >
                        <td className="coverage-col-narrow">
                          <input
                            type="checkbox"
                            checked={selectedOps.has(r.operation)}
                            onChange={() => toggleSelectOp(r.operation)}
                            aria-label={`Select ${r.operation}`}
                          />
                        </td>
                        <td>
                          {nested ? null : (
                            <button
                              type="button"
                              className="coverage-event-name"
                              onClick={() => openOperationDetail(r.operation)}
                            >
                              {base}
                            </button>
                          )}
                        </td>
                        <td>
                          <button
                            type="button"
                            className={`coverage-scenario-name${nested ? " nested" : ""}`}
                            onClick={() => openOperationDetail(r.operation)}
                            title={r.operation}
                          >
                            {scenario}
                          </button>
                        </td>
                        <td>{r.category}</td>
                        <td className="coverage-compared" title={r.comparedAt || undefined}>
                          {r.comparedAt ? new Date(r.comparedAt).toLocaleString() : "—"}
                        </td>
                        <td className="num">
                          <button
                            type="button"
                            className="badge-btn"
                            title="Show PASS fields"
                            onClick={() => openOperationDetail(r.operation, "PASS")}
                          >
                            <span className="badge pass">{r.passed}</span>
                          </button>
                        </td>
                        <td className="num">
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
                        <td className="num">
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
                              title="More actions"
                            >
                              ···
                            </summary>
                            <div className="coverage-track-menu-body">
                              <button type="button" onClick={() => setTrackStatus(r.operation, "covered")}>
                                Mark covered
                              </button>
                              <button type="button" onClick={() => setTrackStatus(r.operation, "needs_enhancement")}>
                                Needs enhancement
                              </button>
                              {r.track !== "unreviewed" && (
                                <button type="button" onClick={() => setTrackStatus(r.operation, "unreviewed")}>
                                  Reset status
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
                    );
                  };

                  if (!multi) {
                    return renderScenarioRow(g.scenarios[0], false);
                  }

                  return (
                    <Fragment key={g.base}>
                      <tr className="coverage-event-row">
                        <td className="coverage-col-narrow">
                          <input
                            type="checkbox"
                            checked={allSelected}
                            ref={(el) => {
                              if (el) el.indeterminate = someSelected && !allSelected;
                            }}
                            onChange={(e) => {
                              setSelectedOps((prev) => {
                                const next = new Set(prev);
                                if (e.target.checked) g.scenarios.forEach((s) => next.add(s.operation));
                                else g.scenarios.forEach((s) => next.delete(s.operation));
                                return next;
                              });
                            }}
                            aria-label={`Select all scenarios for ${g.base}`}
                          />
                        </td>
                        <td>
                          <div className="coverage-op-cell event">
                            <button
                              type="button"
                              className="op-group-expand"
                              aria-expanded={open}
                              aria-label={open ? `Hide scenarios for ${g.base}` : `Show scenarios for ${g.base}`}
                              onClick={() => toggleScenarioGroup(g.base, allBases)}
                            >
                              {open ? "▾" : "▸"}
                            </button>
                            <button
                              type="button"
                              className="coverage-event-name"
                              onClick={() => toggleScenarioGroup(g.base, allBases)}
                            >
                              {g.base}
                            </button>
                          </div>
                        </td>
                        <td>
                          <button
                            type="button"
                            className="coverage-scenario-toggle"
                            onClick={() => toggleScenarioGroup(g.base, allBases)}
                          >
                            {open ? `Hide ${g.scenarios.length}` : `${g.scenarios.length} scenarios`}
                          </button>
                        </td>
                        <td>{g.category}</td>
                        <td className="coverage-compared" title={g.comparedAt || undefined}>
                          {g.comparedAt ? new Date(g.comparedAt).toLocaleString() : "—"}
                        </td>
                        <td className="num">
                          <span className="badge pass">{g.passed}</span>
                        </td>
                        <td className="num">
                          <span className={`badge ${g.failed ? "fail" : "na"}`}>{g.failed}</span>
                        </td>
                        <td className="num">
                          <span className={`badge ${g.skipped ? "skip" : "na"}`}>{g.skipped}</span>
                        </td>
                        <td className="coverage-col-narrow">
                          {g.scenarios.length >= 2 && (
                            <button
                              type="button"
                              className="link-btn"
                              title="Compare scenarios for this event"
                              onClick={() => void openScenarioCompare(g.scenarios.map((s) => s.operation))}
                            >
                              Compare
                            </button>
                          )}
                        </td>
                      </tr>
                      {open && g.scenarios.map((s) => renderScenarioRow(s, true))}
                    </Fragment>
                  );
                })}
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
                <label className="filter-field result-field-search">
                  <span>search</span>
                  <input
                    value={fieldSearch}
                    onChange={(e) => setFieldSearch(e.target.value)}
                    placeholder="attribute or value…"
                  />
                </label>
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
                return (
                  <section key={operation} className="result-group" id={`op-${operation}`}>
                    <button type="button" className="result-group-head" onClick={() => toggleGroup(operation)}>
                      <span className="result-group-title">
                        <span className={`result-status-dot ${failCount ? "fail" : "pass"}`} aria-hidden />
                        {operation}
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
