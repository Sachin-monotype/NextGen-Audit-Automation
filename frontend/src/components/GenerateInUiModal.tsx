import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import {
  fetchCasepilotStatus,
  fetchUiTestrailMap,
  listGenerateInUi,
  startGenerateInUi,
  type UiTriggerJob,
  type UiTriggerSelectionItem,
} from "../api";

type Props = {
  selection: UiTriggerSelectionItem[];
  onClose: () => void;
  onActive?: (job: UiTriggerJob) => void;
};

type ScenarioRow = UiTriggerSelectionItem & {
  test_case_id: string;
  notes: string;
};

const TESTRAIL_CASE_URL = "https://type.testrail.com/index.php?/cases/view/";

function shortTouch(touch?: string | null): string {
  const t = (touch || "").toLowerCase().replace(/\//g, " ").replace(/>/g, " ").replace(/\s+/g, " ").trim();
  if (t.includes("project") && t.includes("list")) return "project_list";
  if (t.includes("favourite") || t.includes("favorite")) return "favourite";
  if (t === "project" || t.startsWith("project ")) return "project";
  if (t.includes("list") || t.includes("fontlist")) return "list";
  if (t.includes("discover") || t.includes("browse") || t.includes("search") || t === "global" || !t) {
    return "global";
  }
  return t.replace(/\s+/g, "_") || "global";
}

function scenarioTitle(s: UiTriggerSelectionItem): string {
  return s.label || (s.touchpoint ? `${s.operation}(${shortTouch(s.touchpoint)})` : s.operation);
}

function resolveCaseId(
  s: UiTriggerSelectionItem,
  byKey: Record<string, number>,
  byLabel: Record<string, number>,
): number | undefined {
  if (s.id && byKey[s.id]) return byKey[s.id];
  const label = (s.label || "").toLowerCase().replace(/\s+/g, "");
  if (label && byLabel[label]) return byLabel[label];
  const soft = (s.label || "").toLowerCase();
  if (soft && byLabel[soft]) return byLabel[soft];
  if (s.operation && s.touchpoint) {
    const key = `${s.operation}::${s.touchpoint}`;
    if (byKey[key]) return byKey[key];
  }
  if (s.operation && byKey[s.operation]) return byKey[s.operation];
  const short = shortTouch(s.touchpoint);
  const alias = `${s.operation}(${short})`.toLowerCase();
  if (byLabel[alias]) return byLabel[alias];
  return undefined;
}

function isElectronAppSelection(selection: UiTriggerSelectionItem[]): boolean {
  return selection.some((s) => {
    const touch = (s.touchpoint || "").toLowerCase().replace(/_/g, " ");
    if (touch.includes("desktop app") || touch === "desktop ui") return true;
    if (touch.includes("desktop") && touch.includes("ui")) return true;
    if (s.id?.startsWith("ingress:plugin_")) return false;
    if (s.id?.startsWith("ingress:app_")) return true;
    return false;
  });
}
  const low = raw.toLowerCase();
  if (
    low.includes("ip_banned") ||
    low.includes("blocked your ip") ||
    low.includes("error 1006")
  ) {
    return (
      "CasePilot Cloudflare blocked this machine's IP (Error 1006). " +
      "Ask CasePilot/Cloudflare admins to unblock you, use corporate VPN, " +
      "and do not keep retrying Send — retries worsen the ban. " +
      "This is not a TestRail/recipe issue."
    );
  }
  if (low.includes("session not found") || low.includes("session affinity") || low.includes("session expired")) {
    return (
      "CasePilot MCP briefly lost session affinity (Cloudflare LB). " +
      "The client auto-retries with a fresh session — click Send again if this still shows. " +
      "Not a TestRail/step problem."
    );
  }
  return raw;
}

/**
 * One-to-one Generate in UI: each event has its own TestRail id (linked) + details box.
 * Case ids come from FDC-14091 map (C73303503…).
 */
export default function GenerateInUiModal({ selection, onClose, onActive }: Props) {
  const [byKey, setByKey] = useState<Record<string, number>>({});
  const [byLabel, setByLabel] = useState<Record<string, number>>({});
  const [mapReady, setMapReady] = useState(false);

  const initialRows = useMemo<ScenarioRow[]>(
    () =>
      selection.map((s) => {
        const cid = mapReady ? resolveCaseId(s, byKey, byLabel) : undefined;
        return {
          ...s,
          test_case_id: cid ? String(cid) : "",
          notes: "",
        };
      }),
    [selection, byKey, byLabel, mapReady],
  );

  const [rows, setRows] = useState<ScenarioRow[]>(initialRows);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [mcpOk, setMcpOk] = useState<boolean | null>(null);
  const [mcpDetail, setMcpDetail] = useState("");
  const electronApp = useMemo(() => isElectronAppSelection(selection), [selection]);
  const [browserMode, setBrowserMode] = useState<"headed" | "headless">(
    electronApp ? "headed" : "headless",
  );
  /** default = PP cap / CASEPILOT_MAX_PARALLEL; 1 = serial */
  const [parallelMode, setParallelMode] = useState<"default" | "1" | "2" | "3" | "4">(
    selection.length > 1 ? "1" : "default",
  );
  const [defaultParallel, setDefaultParallel] = useState<number | null>(null);
  const closedRef = useRef(false);
  const submitRef = useRef(false);
  const [activeUiJobId, setActiveUiJobId] = useState<string | null>(null);

  function requestClose() {
    closedRef.current = true;
    setBusy(false);
    onClose();
  }

  useEffect(() => {
    setRows(initialRows);
  }, [initialRows]);

  useEffect(() => {
    if (selection.length > 1) {
      setParallelMode("1");
    }
  }, [selection.length]);

  useEffect(() => {
    let cancelled = false;
    listGenerateInUi()
      .then((data) => {
        if (cancelled) return;
        const active = (data.jobs || []).find((j) =>
          ["queued", "running", "pending_agent"].includes(String(j.status || "")),
        );
        setActiveUiJobId(active?.id || null);
      })
      .catch(() => {
        if (!cancelled) setActiveUiJobId(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchUiTestrailMap()
      .then((m) => {
        if (cancelled) return;
        setByKey(m.by_key || {});
        setByLabel(m.by_label || {});
        setMapReady(true);
      })
      .catch(() => {
        if (!cancelled) setMapReady(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchCasepilotStatus()
      .then((s) => {
        if (cancelled) return;
        setMcpOk(Boolean(s.ok && s.configured));
        const envDefault =
          s.default_max_parallel != null && Number.isFinite(Number(s.default_max_parallel))
            ? Number(s.default_max_parallel)
            : null;
        setDefaultParallel(envDefault);
        const online = s.connectors?.online;
        const email = s.connection_info?.email || s.preflight?.email || "";
        const err = s.error ? formatCasepilotError(String(s.error)) : "";
        setMcpDetail(
          s.ok
            ? `CasePilot connected${email ? ` (${email})` : ""}${online != null ? ` · connector online=${online}` : ""}`
            : err || "CasePilot unreachable — check CASEPILOT_API_KEY",
        );
      })
      .catch((err) => {
        if (cancelled) return;
        setMcpOk(false);
        setMcpDetail(formatCasepilotError(err instanceof Error ? err.message : String(err)));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function updateRow(index: number, patch: Partial<ScenarioRow>) {
    setRows((prev) => prev.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  }

  const missingCase = rows.some((r) => !r.test_case_id.trim());

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (submitRef.current || busy) return;
    if (missingCase) {
      setError("Each scenario needs a TestRail case id.");
      return;
    }
    closedRef.current = false;
    submitRef.current = true;
    setBusy(true);
    setError("");
    try {
      const payloadSelection = rows.map((r) => ({
        id: r.id,
        operation: r.operation,
        touchpoint: r.touchpoint,
        label: r.label,
        test_case_id: r.test_case_id.trim(),
        notes: r.notes.trim(),
      }));
      const caseIds = rows.map((r) => r.test_case_id.trim()).join(", ");
      const cta =
        rows.length === 1
          ? electronApp
            ? `Perform ${scenarioTitle(rows[0])} in Monotype Connect`
            : `Perform ${scenarioTitle(rows[0])} in NextGen UI`
          : electronApp
            ? `Perform ${rows.length} Monotype Connect scenarios`
            : `Perform ${rows.length} selected scenarios in NextGen UI`;
      const maxParallel =
        parallelMode === "default" ? undefined : Number(parallelMode);
      const res = await startGenerateInUi({
        selection: payloadSelection,
        test_case_id: caseIds,
        cta_text: cta,
        notes: rows
          .filter((r) => r.notes.trim())
          .map((r) => `${scenarioTitle(r)}: ${r.notes.trim()}`)
          .join("\n"),
        dispatch: true,
        headless: browserMode === "headless",
        max_parallel: maxParallel,
        extra: {
          headless: browserMode === "headless",
          browser_mode: browserMode,
          ...(electronApp ? { app_type: "electron" } : {}),
          ...(maxParallel != null ? { max_parallel: maxParallel } : {}),
        },
      });
      if (closedRef.current) return;
      onActive?.(res.job);
      if (!["queued", "running", "completed", "pending_agent"].includes(String(res.job.status))) {
        const msg = formatCasepilotError(
          (res.job?.agent as { last_error?: string } | undefined)?.last_error ||
            "CasePilot send failed",
        );
        setError(msg);
        return;
      }
      onClose();
    } catch (err) {
      if (closedRef.current) return;
      setError(formatCasepilotError(err instanceof Error ? err.message : String(err)));
    } finally {
      submitRef.current = false;
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={requestClose} role="presentation">
      <div
        className="modal-card generate-ui-modal generate-ui-modal-wide"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Generate from UI"
      >
        <div className="modal-head">
          <strong>Generate from UI</strong>
          <button type="button" className="link-btn" onClick={requestClose}>
            close ✕
          </button>
        </div>
        <p className="muted small">
          {electronApp ? (
            <>
              Monotype Connect <strong>Electron</strong> mode — CasePilot launches{" "}
              <code>/Applications/Monotype Connect.app</code> (or{" "}
              <code>CASEPILOT_ELECTRON_APP_PATH</code>). xCorrelationId is harvested from
              Connect service logs after the run. Use <strong>headed</strong> to watch the app.
            </>
          ) : (
            <>
              One row per event (FDC-14091 TestRail map). Edit case id or details, then Send.
              CasePilot opens the <strong>currently selected Environment</strong> NextGen URL
              (PP / QA / UAT) — change Environment on Generate before sending.
            </>
          )}
        </p>
        <p className={`small ${mcpOk ? "ok" : mcpOk === false ? "error" : "muted"}`}>
          {mcpDetail || "Checking CasePilot…"}
          {mcpOk ? "" : mcpOk === null ? " (Send works while status loads)" : ""}
        </p>
        {activeUiJobId ? (
          <p className="small warn">
            Another Generate-in-UI session is active ({activeUiJobId.slice(0, 8)}). Sending will
            stop that batch on the connector and start this one. Use Close session on the log
            panel if you want to cancel without starting a new run.
          </p>
        ) : null}
        {rows.length > 1 && !electronApp ? (
          <p className="muted small">
            Multi-event batch: set <strong>Parallel browsers</strong> to 2–4 to run cases concurrently
            on CasePilot (~10 min for 5). Serial (1) runs one-after-another (~8 min/case).
          </p>
        ) : null}

        <form onSubmit={onSubmit} className="token-cred-form">
          <label style={{ display: "block", marginBottom: 12, maxWidth: 360 }}>
            {electronApp ? "Electron mode" : "Browser mode"}
            <select
              value={browserMode}
              onChange={(e) => setBrowserMode(e.target.value as "headed" | "headless")}
              disabled={busy}
              style={{ display: "block", width: "100%", marginTop: 4 }}
            >
              <option value="headed">
                {electronApp ? "Headed (visible Connect app) — recommended" : "Headed (visible browser)"}
              </option>
              <option value="headless">
                {electronApp ? "Headless (no visible window)" : "Headless (no browser window) — default"}
              </option>
            </select>
          </label>

          {!electronApp ? (
          <label style={{ display: "block", marginBottom: 12, maxWidth: 360 }}>
            Parallel browsers
            <select
              value={parallelMode}
              onChange={(e) =>
                setParallelMode(e.target.value as "default" | "1" | "2" | "3" | "4")
              }
              disabled={busy}
              style={{ display: "block", width: "100%", marginTop: 4 }}
            >
              <option value="default">
                Default
                {defaultParallel != null ? ` (${defaultParallel} from env)` : " (CasePilot PP cap)"}
              </option>
              <option value="1">Serial — one browser at a time</option>
              <option value="2">2 at a time</option>
              <option value="3">3 at a time</option>
              <option value="4">4 at a time</option>
            </select>
            <span className="muted small" style={{ display: "block", marginTop: 4 }}>
              Clamped by CasePilot PP <code>MCP_MAX_PARALLEL_JOBS</code>. Each parallel job uses its
              own browser + login. Electron/desktop tests stay serial on the connector.
            </span>
          </label>
          ) : null}

          <div className="generate-ui-scenario-list">
            {rows.map((r, i) => {
              const digits = r.test_case_id.replace(/\D/g, "");
              const trUrl = digits ? `${TESTRAIL_CASE_URL}${digits}` : "";
              return (
                <div key={r.id || `${r.operation}-${r.touchpoint}-${i}`} className="generate-ui-scenario-row">
                  <div className="generate-ui-scenario-head">
                    <code className="generate-ui-event-name">{scenarioTitle(r)}</code>
                    {r.touchpoint ? <span className="muted small">{r.touchpoint}</span> : null}
                  </div>
                  <label>
                    TestRail case id
                    <div className="generate-ui-case-row">
                      <input
                        value={r.test_case_id}
                        onChange={(e) => updateRow(i, { test_case_id: e.target.value })}
                        placeholder="e.g. 73303503"
                        required
                        autoFocus={i === 0}
                      />
                      {trUrl ? (
                        <a href={trUrl} target="_blank" rel="noreferrer" className="link-btn" title={trUrl}>
                          open C{digits}
                        </a>
                      ) : (
                        <span className="muted small">no link yet</span>
                      )}
                    </div>
                  </label>
                  <label>
                    Extra details (optional)
                    <textarea
                      rows={2}
                      value={r.notes}
                      onChange={(e) => updateRow(i, { notes: e.target.value })}
                      placeholder="Hints for this event only"
                    />
                  </label>
                </div>
              );
            })}
          </div>

          {error && <p className="error small">{error}</p>}
          <div className="modal-actions">
            <button type="button" onClick={requestClose}>
              {busy ? "Close / abort" : "Cancel"}
            </button>
            <button
              type="submit"
              className="primary"
              disabled={busy || missingCase}
              title={
                mcpOk === false
                  ? "CasePilot status check failed — Send still retries MCP sessions automatically"
                  : undefined
              }
            >
              {busy ? "Sending…" : `Send ${rows.length} to CasePilot`}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
