import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import AttributeEditor from "../components/AttributeEditor";
import MultiSelect from "../components/MultiSelect";
import {
  fetchCategories,
  fetchComparableOperations,
  fetchJob,
  startCompare,
  deleteLatestResult,
  type CategoryReport,
  type ComparableOperation,
  type Job,
} from "../api";

type Props = {
  /** Persist compared ops; optional navigation to Result when user clicks Open results. */
  onCompareCompleted: (jobId: string, operations?: string[], navigateToResult?: boolean) => void;
  /** Compare job id started elsewhere (Generation Status) that this page should show live. */
  adoptJobId?: string | null;
  /** Called once adoptJobId is consumed so App can clear the handoff. */
  onAdoptConsumed?: () => void;
};

const JOB_KEY = "audit_compare_job_id";

function jobOperations(job: Job | null): string[] {
  const fromResult = job?.result?.operations;
  if (Array.isArray(fromResult) && fromResult.length) return fromResult as string[];
  const fromRows = [
    ...new Set(
      ((job?.result?.rows ?? []) as { operation?: string }[])
        .map((r) => r.operation)
        .filter(Boolean),
    ),
  ] as string[];
  if (fromRows.length) return fromRows;
  const ops = job?.params?.operations;
  return Array.isArray(ops) ? (ops as string[]) : [];
}

export default function ComparePage({ onCompareCompleted, adoptJobId, onAdoptConsumed }: Props) {
  const [items, setItems] = useState<ComparableOperation[]>([]);
  const [categories, setCategories] = useState<CategoryReport | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("all");
  const [envFilter, setEnvFilter] = useState<string[]>([]);
  const [serviceFilter, setServiceFilter] = useState<string[]>([]);
  const [draftSearch, setDraftSearch] = useState("");
  const [draftCategory, setDraftCategory] = useState("all");
  const [draftEnv, setDraftEnv] = useState<string[]>([]);
  const [draftService, setDraftService] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [error, setError] = useState("");
  const [activeJob, setActiveJob] = useState<Job | null>(null);
  const [fieldPathsByOp, setFieldPathsByOp] = useState<Record<string, string[]>>({});
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    fetchComparableOperations().then((r) => setItems(r.items ?? []));
    fetchCategories().then(setCategories).catch(() => {});
  }, []);

  const pollJob = useCallback(
    (id: string) => {
      if (pollRef.current) clearInterval(pollRef.current);
      let misses = 0;
      pollRef.current = setInterval(async () => {
        try {
          const j = await fetchJob(id);
          misses = 0;
          setActiveJob(j);
          if (j.status === "completed" || j.status === "failed") {
            if (pollRef.current) clearInterval(pollRef.current);
            pollRef.current = null;
            setBusy(false);
          }
        } catch {
          misses += 1;
          if (misses >= 3) {
            if (pollRef.current) clearInterval(pollRef.current);
            pollRef.current = null;
            setBusy(false);
            localStorage.removeItem(JOB_KEY);
          }
        }
      }, 1500);
    },
    [onCompareCompleted],
  );

  // Restore last compare job across tab switches / refresh.
  useEffect(() => {
    const savedId = localStorage.getItem(JOB_KEY);
    if (!savedId) return;
    fetchJob(savedId)
      .then((j) => {
        setActiveJob(j);
        if (j.status === "running" || j.status === "pending") {
          setBusy(true);
          pollJob(j.id);
        }
      })
      .catch(() => localStorage.removeItem(JOB_KEY));
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [pollJob]);

  // Adopt a compare job started from Generation Status → show it live here.
  useEffect(() => {
    if (!adoptJobId) return;
    fetchJob(adoptJobId)
      .then((j) => {
        setActiveJob(j);
        setError("");
        onAdoptConsumed?.();
        if (j.status === "running" || j.status === "pending") {
          setBusy(true);
          pollJob(j.id);
        } else {
          setBusy(false);
        }
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adoptJobId, pollJob]);

  const environmentOptions = useMemo(
    () => [...new Set(items.map((i) => i.environment).filter(Boolean))].sort(),
    [items],
  );

  const serviceOptions = useMemo(
    () => [...new Set(items.map((i) => i.service).filter(Boolean))].sort(),
    [items],
  );

  const visible = useMemo(() => {
    let list = items;
    if (category !== "all") {
      list = list.filter((i) => i.category === category);
    }
    if (envFilter.length) {
      list = list.filter((i) => envFilter.includes(i.environment));
    }
    if (serviceFilter.length) {
      list = list.filter((i) => serviceFilter.includes(i.service));
    }
    const q = search.toLowerCase();
    if (q) list = list.filter((i) => i.operation.toLowerCase().includes(q));
    return list;
  }, [items, category, envFilter, serviceFilter, search]);

  function applyFilters() {
    setCategory(draftCategory);
    setEnvFilter(draftEnv);
    setServiceFilter(draftService);
    setSearch(draftSearch);
    setSelected(new Set());
  }

  function clearFilters() {
    setDraftCategory("all");
    setDraftEnv([]);
    setDraftService([]);
    setDraftSearch("");
    setCategory("all");
    setEnvFilter([]);
    setServiceFilter([]);
    setSearch("");
    setSelected(new Set());
  }

  function toggle(op: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(op)) next.delete(op);
      else next.add(op);
      return next;
    });
  }

  function selectAllVisible() {
    setSelected(new Set(visible.map((i) => i.operation)));
  }

  function clearAll() {
    setSelected(new Set());
  }

  async function runCompare() {
    if (!selected.size) return;
    setBusy(true);
    setError("");
    setActiveJob(null);
    try {
      // Only send field filters for ops that were explicitly edited
      const filtered: Record<string, string[]> = {};
      for (const op of selected) {
        if (fieldPathsByOp[op]?.length) filtered[op] = fieldPathsByOp[op];
      }
      const job = await startCompare(
        [...selected],
        Object.keys(filtered).length ? filtered : undefined,
      );
      localStorage.setItem(JOB_KEY, job.id);
      setActiveJob(job);
      pollJob(job.id);
    } catch (e) {
      setError(String(e));
      setBusy(false);
    }
  }

  const jobRunning =
    busy || activeJob?.status === "running" || activeJob?.status === "pending";

  return (
    <section className="panel">
      <header className="panel-head">
        <h2>Compare enriched vs source</h2>
        <p>Operations with both raw and enriched samples in MongoDB.</p>
      </header>

      {error && <p className="error">{error}</p>}

      {jobRunning && (
        <div className="banner warn">
          <strong>Compare is still running</strong> — you can switch tabs; logs stay here.
          Results update per operation as each finishes.
        </div>
      )}

      <div className="filter-row compare-filter-row">
        <label className="filter-field">
          <span>category</span>
          <select
            value={draftCategory}
            onChange={(e) => setDraftCategory(e.target.value)}
          >
            <option value="all">All categories ({items.length})</option>
            {(categories?.categories ?? []).map((c) => {
              const n = items.filter((i) => i.category === c).length;
              return (
                <option key={c} value={c}>
                  {c}
                  {n ? ` (${n})` : ""}
                </option>
              );
            })}
          </select>
        </label>
        <MultiSelect
          label="environment"
          options={environmentOptions}
          selected={draftEnv}
          onChange={setDraftEnv}
        />
        <MultiSelect
          label="service"
          options={serviceOptions}
          selected={draftService}
          onChange={setDraftService}
        />
        <label className="filter-field">
          <span>search</span>
          <input
            placeholder="Operation name…"
            value={draftSearch}
            onChange={(e) => setDraftSearch(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") applyFilters();
            }}
          />
        </label>
        <div className="filter-actions">
          <button type="button" className="primary" onClick={applyFilters}>
            Apply
          </button>
          <button type="button" onClick={clearFilters}>
            Clear
          </button>
        </div>
      </div>

      <div className="compare-toolbar">
        <button
          type="button"
          className="primary"
          disabled={!selected.size || jobRunning}
          onClick={runCompare}
        >
          {jobRunning ? "Comparing…" : `Compare ${selected.size} selected`}
        </button>
        <AttributeEditor
          operations={[...selected]}
          value={fieldPathsByOp}
          onChange={setFieldPathsByOp}
        />
        <button type="button" onClick={selectAllVisible} disabled={jobRunning}>
          Select all
        </button>
        <button type="button" onClick={clearAll} disabled={jobRunning}>
          Clear
        </button>
        {selected.size > 0 && (
          <button
            type="button"
            className="danger"
            disabled={deleteBusy || jobRunning}
            onClick={() => {
              const ops = [...selected];
              if (!window.confirm(`Delete stored result(s) for ${ops.length} operation(s)?`)) return;
              setDeleteBusy(true);
              void (async () => {
                try {
                  for (const op of ops) {
                    await deleteLatestResult(op);
                  }
                  setSelected(new Set());
                } catch (e) {
                  setError(String(e));
                } finally {
                  setDeleteBusy(false);
                }
              })();
            }}
          >
            {deleteBusy ? "Deleting…" : `Delete stored result (${selected.size})`}
          </button>
        )}
        <span className="muted">
          {visible.length} shown · {selected.size} selected
        </span>
      </div>

      {activeJob && (
        <div className="compare-live-panel">
          <div className="compare-live-head">
            <span className={`status-pill ${activeJob.status}`}>{activeJob.status}</span>
            <strong>Live comparison</strong>
            <span className="muted mono">job {activeJob.id.slice(0, 8)}</span>
            {(activeJob.status === "completed" || activeJob.status === "failed") && (
              <button
                type="button"
                className="primary"
                onClick={() => onCompareCompleted(activeJob.id, jobOperations(activeJob), true)}
              >
                Open results
              </button>
            )}
          </div>
          <pre className="job-logs">
            {(activeJob.logs ?? []).slice(-60).join("\n") || "Starting…"}
          </pre>
          {activeJob.error && <p className="error">{activeJob.error}</p>}
          {activeJob.status === "completed" && (
            <p className="muted">
              Comparison finished — open Results to review pass / fail / skip.
            </p>
          )}
        </div>
      )}

      <div className="op-list compare-op-list">
        {visible.map((item) => (
          <div
            key={item.operation}
            className="op-row"
            role="button"
            tabIndex={0}
            onClick={() => !jobRunning && toggle(item.operation)}
            onKeyDown={(e) => {
              if (!jobRunning && (e.key === "Enter" || e.key === " ")) {
                e.preventDefault();
                toggle(item.operation);
              }
            }}
          >
            <input
              type="checkbox"
              checked={selected.has(item.operation)}
              disabled={jobRunning}
              onChange={() => toggle(item.operation)}
              onClick={(e) => e.stopPropagation()}
            />
            <span className="op-row-main">
              <span className="op-row-name">{item.operation}</span>
              <span className="muted op-row-meta">
                {[item.category, item.environment, item.service]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
            </span>
          </div>
        ))}
        {visible.length === 0 && (
          <p className="muted compare-empty">
            No pairable operations match the current filters. Adjust category /
            environment / service and click Apply.
          </p>
        )}
      </div>
    </section>
  );
}
