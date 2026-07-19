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

/** FDC-00001 TestRail map — keep in sync with python/audit_validator/ui_testrail_map.py */
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

function mapCaseIds(selection: UiTriggerSelectionItem[]): number[] {
  const out: number[] = [];
  const seen = new Set<number>();
  for (const s of selection) {
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
    if (cid && !seen.has(cid)) {
      seen.add(cid);
      out.push(cid);
    }
  }
  return out;
}

/**
 * One-step Generate in UI: auto-mapped TestRail ids + optional extra details → Send.
 * Parent log auto-refreshes and auto-verifies raw/enrich into Generation Status.
 */
export default function GenerateInUiModal({ selection, onClose, onActive }: Props) {
  const mapped = useMemo(() => mapCaseIds(selection), [selection]);
  const [testCaseId, setTestCaseId] = useState(() => mapped.join(", "));
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [mcpOk, setMcpOk] = useState<boolean | null>(null);
  const [mcpDetail, setMcpDetail] = useState("");

  useEffect(() => {
    setTestCaseId(mapped.join(", "));
  }, [mapped]);

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

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!testCaseId.trim()) return;
    setBusy(true);
    setError("");
    try {
      const cta =
        selection.length === 1
          ? `Perform ${selection[0].label || selection[0].operation} in NextGen UI`
          : `Perform ${selection.length} selected scenarios in NextGen UI`;
      const res = await startGenerateInUi({
        selection,
        test_case_id: testCaseId.trim(),
        cta_text: cta,
        notes: notes.trim(),
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
        className="modal-card generate-ui-modal"
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
          Send {selection.length} scenario{selection.length === 1 ? "" : "s"} to CasePilot. When the UI
          browser closes we auto-capture correlation ids (including intermediate mutations) and load
          raw + enrich into Generation Status.
        </p>
        <p className={`small ${mcpOk ? "ok" : mcpOk === false ? "error" : "muted"}`}>
          {mcpDetail || "Checking CasePilot…"}
        </p>
        <ul className="muted small" style={{ margin: "8px 0" }}>
          {selection.map((s) => {
            const cid =
              (s.id && FDC_CASE_BY_ID[s.id]) ||
              FDC_CASE_BY_LABEL[(s.label || "").toLowerCase()] ||
              undefined;
            return (
              <li key={s.id || `${s.operation}-${s.touchpoint}`}>
                {s.label || s.operation}
                {s.touchpoint ? ` · ${s.touchpoint}` : ""}
                {cid ? (
                  <>
                    {" "}
                    → <code>C{cid}</code>
                  </>
                ) : (
                  " → (enter id below)"
                )}
              </li>
            );
          })}
        </ul>
        <form onSubmit={onSubmit} className="token-cred-form">
          <label>
            TestRail testcase id{mapped.length > 1 ? "s" : ""}
            <input
              value={testCaseId}
              onChange={(e) => setTestCaseId(e.target.value)}
              placeholder="Auto-mapped from selection — edit if needed"
              required
              autoFocus
            />
          </label>
          <label>
            Extra details (optional)
            <textarea
              rows={3}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Hints for the UI agent (e.g. prefer family detail Activate button)"
            />
          </label>
          {error && <p className="error small">{error}</p>}
          <div className="modal-actions">
            <button type="button" disabled={busy} onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="primary" disabled={busy || !testCaseId.trim() || mcpOk === false}>
              {busy ? "Sending…" : "Send to CasePilot"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
