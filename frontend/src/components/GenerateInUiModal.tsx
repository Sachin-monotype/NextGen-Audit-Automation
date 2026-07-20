import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import {
  fetchCasepilotStatus,
  fetchUiTestrailMap,
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

function formatCasepilotError(raw: string): string {
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
  const closedRef = useRef(false);

  function requestClose() {
    closedRef.current = true;
    setBusy(false);
    onClose();
  }

  useEffect(() => {
    setRows(initialRows);
  }, [initialRows]);

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
    if (missingCase) {
      setError("Each scenario needs a TestRail case id.");
      return;
    }
    closedRef.current = false;
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
          ? `Perform ${scenarioTitle(rows[0])} in NextGen UI`
          : `Perform ${rows.length} selected scenarios in NextGen UI`;
      const res = await startGenerateInUi({
        selection: payloadSelection,
        test_case_id: caseIds,
        cta_text: cta,
        notes: rows
          .filter((r) => r.notes.trim())
          .map((r) => `${scenarioTitle(r)}: ${r.notes.trim()}`)
          .join("\n"),
        dispatch: true,
      });
      if (closedRef.current) return;
      onActive?.(res.job);
      if (!["queued", "running", "completed"].includes(String(res.job.status))) {
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
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={requestClose} role="presentation">
      <div
        className="modal-card generate-ui-modal generate-ui-modal-wide"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Generate in UI"
      >
        <div className="modal-head">
          <strong>Generate in UI</strong>
          <button type="button" className="link-btn" onClick={requestClose}>
            close ✕
          </button>
        </div>
        <p className="muted small">
          One row per event (FDC-14091 TestRail map). Edit case id or details, then Send.
          CasePilot opens the <strong>currently selected Environment</strong> NextGen URL
          (PP / QA / UAT) — change Environment on Generate before sending.
        </p>
        <p className={`small ${mcpOk ? "ok" : mcpOk === false ? "error" : "muted"}`}>
          {mcpDetail || "Checking CasePilot…"}
        </p>

        <form onSubmit={onSubmit} className="token-cred-form">
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
              disabled={busy || missingCase || mcpOk === false}
            >
              {busy ? "Sending…" : `Send ${rows.length} to CasePilot`}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
