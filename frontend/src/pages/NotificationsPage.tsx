import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import {
  createNotificationCycle,
  fetchNotificationView,
  getNotificationExcelDownloadUrl,
  patchNotificationResult,
  type NotificationItem,
  type NotificationView,
} from "../api";

type Props = {
  defaultEnv?: string;
};

const ENVS = ["uat", "qa", "pp", "beta"] as const;

type SortKey = "id" | "event" | "category" | "status" | "channels";

const STATUS_RANK: Record<string, number> = {
  "": 0,
  Open: 0,
  Fail: 1,
  Blocked: 2,
  "N/A": 3,
  Pass: 4,
};

function ChannelPills({ item }: { item: NotificationItem }) {
  const ch = item.channels || { in_app: false, email: false, push: false };
  const pills: { key: string; label: string; title: string }[] = [];
  if (ch.in_app) pills.push({ key: "in_app", label: "App", title: "In-App" });
  if (ch.email) pills.push({ key: "email", label: "Email", title: "Email" });
  if (ch.push) pills.push({ key: "push", label: "Push", title: "Push" });
  if (pills.length === 0) return <span className="muted">—</span>;
  return (
    <span className="notif-channels">
      {pills.map((p) => (
        <span key={p.key} className="ch on" title={p.title}>
          {p.label}
        </span>
      ))}
    </span>
  );
}

function isTested(item: NotificationItem): boolean {
  return Boolean(String(item.status || "").trim());
}

function channelSortKey(item: NotificationItem): string {
  const ch = item.channels || {};
  return [ch.in_app && "a", ch.email && "e", ch.push && "p"].filter(Boolean).join("") || "z";
}

function SortHeader({
  label,
  column,
  sortKey,
  sortDir,
  onSort,
}: {
  label: string;
  column: SortKey;
  sortKey: SortKey;
  sortDir: "asc" | "desc";
  onSort: (key: SortKey) => void;
}) {
  const active = sortKey === column;
  return (
    <th>
      <button type="button" className={`notif-th-btn ${active ? "active" : ""}`} onClick={() => onSort(column)}>
        {label}
        <span className="notif-sort-ind" aria-hidden>
          {active ? (sortDir === "asc" ? " ▲" : " ▼") : ""}
        </span>
      </button>
    </th>
  );
}

export default function NotificationsPage({ defaultEnv = "uat" }: Props) {
  const [env, setEnv] = useState(defaultEnv);
  const [cycleId, setCycleId] = useState<string | null>(null);
  const [view, setView] = useState<NotificationView | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [channelFilter, setChannelFilter] = useState<"all" | "in_app" | "email" | "push">("all");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [retesting, setRetesting] = useState<Record<string, true>>({});
  const [savingId, setSavingId] = useState<string | null>(null);
  const [newCycleName, setNewCycleName] = useState("");
  const [drafts, setDrafts] = useState<Record<string, Partial<NotificationItem>>>({});
  const [sortKey, setSortKey] = useState<SortKey>("id");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  const refresh = useCallback(async (nextCycle?: string | null) => {
    setLoading(true);
    setError("");
    try {
      const data = await fetchNotificationView(env, nextCycle ?? cycleId);
      setView(data);
      if (data.cycle?.id) setCycleId(data.cycle.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [env, cycleId]);

  useEffect(() => {
    void refresh(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- load catalog when env changes
  }, [env]);

  const items = view?.items || [];

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const list = items.filter((it) => {
      if (category !== "all" && it.category_id !== category) return false;
      if (statusFilter === "empty" && String(it.status || "").trim()) return false;
      if (statusFilter !== "all" && statusFilter !== "empty" && it.status !== statusFilter) return false;
      if (channelFilter !== "all" && !it.channels?.[channelFilter]) return false;
      if (!q) return true;
      const hay = `${it.id} ${it.event} ${it.trigger} ${it.expected} ${it.actual} ${it.comments}`.toLowerCase();
      return hay.includes(q);
    });

    const dir = sortDir === "asc" ? 1 : -1;
    return [...list].sort((a, b) => {
      let cmp = 0;
      if (sortKey === "status") {
        cmp =
          (STATUS_RANK[a.status || ""] ?? 0) - (STATUS_RANK[b.status || ""] ?? 0) ||
          (a.status || "").localeCompare(b.status || "");
      } else if (sortKey === "channels") {
        cmp = channelSortKey(a).localeCompare(channelSortKey(b));
      } else if (sortKey === "category") {
        cmp = (a.category || "").localeCompare(b.category || "") || (a.id || "").localeCompare(b.id || "");
      } else if (sortKey === "event") {
        cmp = (a.event || "").localeCompare(b.event || "");
      } else {
        cmp = (a.id || "").localeCompare(b.id || "", undefined, { numeric: true });
      }
      return cmp * dir;
    });
  }, [items, search, category, statusFilter, channelFilter, sortKey, sortDir]);

  const stats = useMemo(() => {
    const pass = items.filter((i) => i.status === "Pass").length;
    const fail = items.filter((i) => i.status === "Fail").length;
    const blocked = items.filter((i) => i.status === "Blocked").length;
    const filled = items.filter((i) => i.status || i.actual || i.comments).length;
    return { total: items.length, pass, fail, blocked, filled, open: items.length - filled };
  }, [items]);

  function draftFor(item: NotificationItem): NotificationItem {
    return { ...item, ...(drafts[item.id] || {}) };
  }

  function setDraft(id: string, patch: Partial<NotificationItem>) {
    setDrafts((prev) => ({ ...prev, [id]: { ...(prev[id] || {}), ...patch } }));
  }

  function toggleSort(key: SortKey) {
    if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortKey(key);
      setSortDir(key === "status" ? "asc" : "asc");
    }
  }

  function startRetest(id: string) {
    setRetesting((prev) => ({ ...prev, [id]: true }));
    setExpanded(id);
  }

  function cancelRetest(id: string) {
    setRetesting((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
    setDrafts((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
  }

  async function saveItem(item: NotificationItem) {
    if (!cycleId) {
      setError("Create or select a cycle before saving actuals.");
      return;
    }
    const d = draftFor(item);
    setSavingId(item.id);
    setError("");
    try {
      await patchNotificationResult({
        env,
        cycle_id: cycleId,
        item_id: item.id,
        status: d.status || "",
        actual: d.actual || "",
        comments: d.comments || "",
        how_to_notes: d.how_to_notes || "",
      });
      setDrafts((prev) => {
        const next = { ...prev };
        delete next[item.id];
        return next;
      });
      setRetesting((prev) => {
        const next = { ...prev };
        delete next[item.id];
        return next;
      });
      await refresh(cycleId);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSavingId(null);
    }
  }

  async function handleCreateCycle(nameOverride?: string) {
    const name =
      (nameOverride ?? newCycleName).trim() ||
      `${env.toUpperCase()} ${new Date().toLocaleDateString()}`;
    setLoading(true);
    setError("");
    try {
      const res = await createNotificationCycle({ env, name, seed_from_catalog: true });
      const id = res.cycle?.id || null;
      setCycleId(id);
      setNewCycleName("");
      setDrafts({});
      setRetesting({});
      await refresh(id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  const hasCycle = Boolean(cycleId);
  const excelHref = cycleId ? getNotificationExcelDownloadUrl(env, cycleId) : null;

  return (
    <div className="notif-page">
      <header className="page-header">
        <div>
          <h2>Notification Test Guide</h2>
          <p className="muted">
            Checklist of expected notifications. Start a cycle for an env, record what you actually
            saw, then download Excel. Already-tested rows stay locked until you Retest.
          </p>
        </div>
        {excelHref ? (
          <a className="primary" href={excelHref} download>
            Download Excel
          </a>
        ) : (
          <button type="button" className="primary outline" disabled title="Start or select a cycle first">
            Download Excel
          </button>
        )}
      </header>

      <ol className="notif-howto">
        <li>
          <strong>Pick env</strong> (UAT / QA / PP / Beta)
        </li>
        <li>
          <strong>Start a cycle</strong> — unlocks untested rows
        </li>
        <li>
          <strong>Open a row</strong>, trigger the event, fill Actual + Status, Save
        </li>
        <li>
          <strong>Retest</strong> to edit a row that already has a status; then <strong>Download Excel</strong>
        </li>
      </ol>

      {error && <p className="error">{error}</p>}
      {view?.catalog?.error && <p className="error">{view.catalog.error}</p>}

      {!hasCycle && (
        <div className="notif-start-banner">
          <div>
            <strong>Fields are locked until you start a cycle.</strong>
            <p className="muted">
              A cycle is one test pass for this env (e.g. “Beta Sprint 12”). Creating one unlocks
              Status, Actual notification, and Comments on untested rows.
            </p>
          </div>
          <button
            type="button"
            className="primary"
            disabled={loading}
            onClick={() => void handleCreateCycle(`${env.toUpperCase()} ${new Date().toLocaleDateString()}`)}
          >
            {loading ? "Starting…" : "Start testing"}
          </button>
        </div>
      )}

      <div className="notif-toolbar">
        <label>
          Env
          <select
            value={env}
            onChange={(e) => {
              setEnv(e.target.value);
              setCycleId(null);
              setDrafts({});
              setRetesting({});
              setExpanded(null);
            }}
          >
            {ENVS.map((e) => (
              <option key={e} value={e}>
                {e.toUpperCase()}
              </option>
            ))}
          </select>
        </label>

        <label>
          Cycle
          <select
            value={cycleId || ""}
            onChange={(e) => {
              const id = e.target.value || null;
              setCycleId(id);
              setDrafts({});
              setRetesting({});
              setExpanded(null);
              void refresh(id);
            }}
          >
            <option value="">— select or start a cycle —</option>
            {(view?.cycles || [])
              .filter((c) => !c.env || c.env === env)
              .map((c) => (
                <option key={c.id || c.name || ""} value={c.id || ""}>
                  {c.name} {c.filled_count != null ? `(${c.filled_count} filled)` : ""}
                </option>
              ))}
          </select>
        </label>

        <div className="notif-new-cycle">
          <input
            type="text"
            placeholder="Optional cycle name"
            value={newCycleName}
            onChange={(e) => setNewCycleName(e.target.value)}
          />
          <button type="button" className="primary" onClick={() => void handleCreateCycle()} disabled={loading}>
            New cycle
          </button>
        </div>

        {excelHref ? (
          <a className="primary outline" href={excelHref} download>
            Download Excel
          </a>
        ) : null}
      </div>

      {hasCycle && view?.cycle?.name && (
        <p className="notif-active-cycle muted">
          Editing cycle <strong>{view.cycle.name}</strong> on <strong>{env.toUpperCase()}</strong> —
          untested rows are editable; tested rows need <strong>Retest</strong>.
        </p>
      )}

      <div className="notif-stats">
        <span>{stats.total} events</span>
        <span className="ok">{stats.pass} pass</span>
        <span className="bad">{stats.fail} fail</span>
        <span className="warn">{stats.blocked} blocked</span>
        <span>{stats.filled} filled</span>
        <span className="muted">{stats.open} open</span>
      </div>

      <div className="notif-filters">
        <input
          type="search"
          placeholder="Search event, trigger, expected…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="all">All categories</option>
          {(view?.catalog.categories || []).map((c) => (
            <option key={c.id} value={c.id}>
              {c.id}: {c.label}
            </option>
          ))}
        </select>
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="all">All statuses</option>
          <option value="empty">Not tested</option>
          {(view?.catalog.statuses || []).filter(Boolean).map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <select
          value={channelFilter}
          onChange={(e) => setChannelFilter(e.target.value as typeof channelFilter)}
        >
          <option value="all">All channels</option>
          <option value="in_app">In-App</option>
          <option value="email">Email</option>
          <option value="push">Push</option>
        </select>
      </div>

      {loading && !view && <p className="muted">Loading catalog…</p>}

      <div className="notif-table-wrap">
        <table className="notif-table">
          <thead>
            <tr>
              <SortHeader label="ID" column="id" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} />
              <SortHeader label="Event" column="event" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} />
              <SortHeader label="Category" column="category" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} />
              <SortHeader label="Channels" column="channels" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} />
              <SortHeader label="Status" column="status" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} />
              <th>Actual</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((raw) => {
              const item = draftFor(raw);
              const open = expanded === item.id;
              const tested = isTested(raw);
              const editing = hasCycle && (!tested || Boolean(retesting[item.id]));
              const statusLabel = item.status || "Open";

              return (
                <Fragment key={item.id}>
                  <tr
                    className={`notif-tr ${tested ? "tested" : ""} ${item.status === "Pass" ? "pass" : ""} ${item.status === "Fail" ? "fail" : ""} ${open ? "open" : ""}`}
                  >
                    <td className="notif-id">{item.id}</td>
                    <td className="notif-event-cell">{item.event}</td>
                    <td className="muted">{item.category_id || "—"}</td>
                    <td>
                      <ChannelPills item={item} />
                    </td>
                    <td>
                      <span className={`notif-status-pill ${item.status ? item.status.toLowerCase() : "open"}`}>
                        {statusLabel}
                      </span>
                    </td>
                    <td className="notif-actual-preview muted" title={item.actual || ""}>
                      {item.actual ? (item.actual.length > 80 ? `${item.actual.slice(0, 80)}…` : item.actual) : "—"}
                    </td>
                    <td className="notif-actions-cell">
                      <button
                        type="button"
                        className="link-btn"
                        onClick={() => setExpanded(open ? null : item.id)}
                      >
                        {open ? "Hide" : "View"}
                      </button>
                      {hasCycle && tested && !retesting[item.id] && (
                        <button type="button" className="link-btn" onClick={() => startRetest(item.id)}>
                          Retest
                        </button>
                      )}
                      {hasCycle && !tested && (
                        <button
                          type="button"
                          className="link-btn"
                          onClick={() => setExpanded(item.id)}
                        >
                          Test
                        </button>
                      )}
                    </td>
                  </tr>
                  {open && (
                    <tr className="notif-detail-row">
                      <td colSpan={7}>
                        <div className="notif-card-body">
                          {tested && !editing && (
                            <p className="notif-locked-note muted">
                              Already tested — read-only. Click <strong>Retest</strong> to change status / actuals.
                            </p>
                          )}

                          <div className="notif-meta">
                            <div>
                              <strong>Category</strong>
                              <p>{item.category}</p>
                            </div>
                            {item.permission && (
                              <div>
                                <strong>Permission</strong>
                                <p className="mono">{item.permission}</p>
                              </div>
                            )}
                            {item.recipients && (
                              <div>
                                <strong>Recipients</strong>
                                <p>{item.recipients}</p>
                              </div>
                            )}
                          </div>

                          <div className="notif-guide">
                            <strong>How to trigger</strong>
                            <p>{item.how_to || item.trigger || "—"}</p>
                            <textarea
                              placeholder={
                                editing
                                  ? "Optional tester notes / step-by-step for this cycle…"
                                  : tested
                                    ? "Retest to edit notes"
                                    : "Start a cycle to add notes"
                              }
                              value={item.how_to_notes || ""}
                              disabled={!editing}
                              onChange={(e) => setDraft(item.id, { how_to_notes: e.target.value })}
                            />
                          </div>

                          <div className="notif-expected">
                            <strong>Expected notification</strong>
                            <pre>{item.expected || "—"}</pre>
                          </div>

                          <div className="notif-actuals">
                            <label>
                              Status
                              <select
                                value={item.status || ""}
                                disabled={!editing}
                                onChange={(e) => setDraft(item.id, { status: e.target.value })}
                              >
                                {(view?.catalog.statuses || ["", "Pass", "Fail", "Blocked", "N/A"]).map((s) => (
                                  <option key={s || "empty"} value={s}>
                                    {s || "—"}
                                  </option>
                                ))}
                              </select>
                            </label>
                            <label className="grow">
                              Actual notification
                              <textarea
                                value={item.actual || ""}
                                disabled={!editing}
                                placeholder={
                                  editing
                                    ? "Paste what you saw in the product…"
                                    : tested
                                      ? "Retest to edit"
                                      : "Start a cycle to edit"
                                }
                                onChange={(e) => setDraft(item.id, { actual: e.target.value })}
                                rows={3}
                              />
                            </label>
                            <label className="grow">
                              Comments
                              <textarea
                                value={item.comments || ""}
                                disabled={!editing}
                                placeholder={
                                  editing
                                    ? "Bugs, links, notes…"
                                    : tested
                                      ? "Retest to edit"
                                      : "Start a cycle to edit"
                                }
                                onChange={(e) => setDraft(item.id, { comments: e.target.value })}
                                rows={2}
                              />
                            </label>
                          </div>

                          <div className="notif-actions">
                            {!hasCycle && (
                              <span className="muted">
                                Click <strong>Start testing</strong> above to unlock untested rows.
                              </span>
                            )}
                            {tested && retesting[item.id] && (
                              <button type="button" className="link-btn" onClick={() => cancelRetest(item.id)}>
                                Cancel retest
                              </button>
                            )}
                            <button
                              type="button"
                              className="primary"
                              disabled={!editing || savingId === item.id}
                              onClick={() => void saveItem(item)}
                            >
                              {savingId === item.id ? "Saving…" : retesting[item.id] ? "Save retest" : "Save"}
                            </button>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
        {!loading && filtered.length === 0 && (
          <p className="muted">No notification events match the current filters.</p>
        )}
      </div>
    </div>
  );
}
