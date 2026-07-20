import { useEffect, useMemo, useState, type FormEvent } from "react";
import {
  fetchCasepilotStatus,
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

/** FDC-00001 / FDC-14091 TestRail map — keep in sync with python/audit_validator/ui_testrail_map.py */
const FDC_CASE_BY_ID: Record<string, number> = {
  "activateFamily::Discovery/Browse (global)": 73300131,
  "activateFamily::List (FONTLIST)": 73300132,
  "activateFamily::Favourite": 73300133,
  "activateFamily::Project": 73300134,
  "activateFamily::Project > List": 73300135,
  "deactivateFamilies::Discovery/Browse (global)": 73300136,
  "activateStyle::Discovery/Browse (global)": 73300137,
  createProject: 73300138,
  "createProject::Discovery/Browse (global)": 73300138,
  addFavoriteFamilies: 73300139,
  "addFavoriteFamilies::Favourite": 73300139,
  dismissNotification: 73300140,
  "dismissNotification::Discovery/Browse (global)": 73300140,
};

const FDC_CASE_BY_LABEL: Record<string, number> = {
  "activatefamily(global)": 73300131,
  "activatefamily(list)": 73300132,
  "activatefamily(favourite)": 73300133,
  "activatefamily(project)": 73300134,
  "activatefamily(project_list)": 73300135,
  "deactivatefamilies(global)": 73300136,
  "activatestyle(global)": 73300137,
  createproject: 73300138,
  "createproject(global)": 73300138,
  addfavoritefamilies: 73300139,
  "addfavoritefamilies(favourite)": 73300139,
  dismissnotification: 73300140,
  "dismissnotification(global)": 73300140,
};

const TESTRAIL_CASE_URL = "https://type.testrail.com/index.php?/cases/view/";

function resolveCaseId(s: UiTriggerSelectionItem): number | undefined {
  let cid =
    (s.id && FDC_CASE_BY_ID[s.id]) ||
    FDC_CASE_BY_LABEL[(s.label || "").toLowerCase()] ||
    (s.operation && FDC_CASE_BY_ID[s.operation]) ||
    undefined;
  if (!cid && s.operation === "activateFamily") {
    const t = (s.touchpoint || "").toLowerCase();
    if (t.includes("project") && t.includes("list")) cid = 73300135;
    else if (t.includes("favourite") || t.includes("favorite")) cid = 73300133;
    else if (t.includes("project")) cid = 73300134;
    else if (t.includes("list")) cid = 73300132;
    else cid = 73300131;
  }
  return cid;
}

function scenarioTitle(s: UiTriggerSelectionItem): string {
  return s.label || (s.touchpoint ? `${s.operation}(${s.touchpoint})` : s.operation);
}

/**
 * One-to-one Generate in UI: each event has its own TestRail id (linked) + details box.
 */
export default function GenerateInUiModal({ selection, onClose, onActive }: Props) {
  const initialRows = useMemo<ScenarioRow[]>(
    () =>
      selection.map((s) => {
        const cid = resolveCaseId(s);
        return {
          ...s,
          test_case_id: cid ? String(cid) : "",
          notes: "",
        };
      }),
    [selection],
  );

  const [rows, setRows] = useState<ScenarioRow[]>(initialRows);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [mcpOk, setMcpOk] = useState<boolean | null>(null);
  const [mcpDetail, setMcpDetail] = useState("");

  useEffect(() => {
    setRows(initialRows);
  }, [initialRows]);

  useEffect(() => {
    let cancelled = false;
    fetchCasepilotStatus()
      .then((s) => {
        if (cancelled) return;
        setMcpOk(Boolean(s.ok && s.configured));
        const online = s.connectors?.online;
        const email = s.connection_info?.email || s.preflight?.email || "";
        setMcpDetail(
          s.ok
            ? `CasePilot connected${email ? ` (${email})` : ""}${online != null ? ` · connector online=${online}` : ""}`
            : s.error || "CasePilot unreachable — check CASEPILOT_API_KEY",
        );
      })
      .catch((err) => {
        if (cancelled) return;
        setMcpOk(false);
        setMcpDetail(err instanceof Error ? err.message : String(err));
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
      onActive?.(res.job);
      if (!["queued", "running", "completed"].includes(String(res.job.status))) {
        const msg =
          (res.job?.agent as { last_error?: string } | undefined)?.last_error ||
          "CasePilot send failed";
        setError(msg);
        onClose();
        return;
      }
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={() => !busy && onClose()} role="presentation">
      <div
        className="modal-card generate-ui-modal generate-ui-modal-wide"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Generate in UI"
      >
        <div className="modal-head">
          <strong>Generate in UI</strong>
          <button type="button" className="link-btn" disabled={busy} onClick={onClose}>
            close ✕
          </button>
        </div>
        <p className="muted small">
          One row per event. Edit TestRail id or extra details per scenario, then Send. When the UI
          browser closes we capture correlation ids and load raw + enrich into Generation Status.
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
                    {r.touchpoint ? (
                      <span className="muted small">{r.touchpoint}</span>
                    ) : null}
                  </div>
                  <label>
                    TestRail case id
                    <div className="generate-ui-case-row">
                      <input
                        value={r.test_case_id}
                        onChange={(e) => updateRow(i, { test_case_id: e.target.value })}
                        placeholder="e.g. 73300131"
                        required
                        autoFocus={i === 0}
                      />
                      {trUrl ? (
                        <a
                          href={trUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="link-btn"
                          title={trUrl}
                        >
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
                      placeholder="Hints for this event only (e.g. use family 910042901, prefer detail Activate)"
                    />
                  </label>
                </div>
              );
            })}
          </div>

          {error && <p className="error small">{error}</p>}
          <div className="modal-actions">
            <button type="button" disabled={busy} onClick={onClose}>
              Cancel
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
