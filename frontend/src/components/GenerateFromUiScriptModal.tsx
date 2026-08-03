import { useEffect, useMemo, useState } from "react";
import {
  fetchUiScriptCatalog,
  importGenerateFromUiScript,
  type UiScriptCatalog,
  type UiTriggerJob,
} from "../api";
import MultiSelect from "./MultiSelect";

type Props = {
  onClose: () => void;
  onComplete: (job: UiTriggerJob) => void;
};

type Target = "web" | "app";

export default function GenerateFromUiScriptModal({ onClose, onComplete }: Props) {
  const [target, setTarget] = useState<Target>("web");
  const [catalog, setCatalog] = useState<UiScriptCatalog | null>(null);
  const [events, setEvents] = useState<string[]>([]);
  const [scenarios, setScenarios] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    setEvents([]);
    setScenarios([]);
    void fetchUiScriptCatalog(target)
      .then((data) => {
        if (!cancelled) setCatalog(data);
      })
      .catch((e) => {
        if (!cancelled) {
          setCatalog(null);
          setError(e instanceof Error ? e.message : String(e));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [target]);

  const eventOptions = catalog?.events ?? [];
  const scenarioOptions = useMemo(() => {
    const rows = catalog?.rows ?? [];
    if (events.length === 0) {
      return catalog?.scenarios ?? [];
    }
    const set = new Set<string>();
    for (const r of rows) {
      if (events.includes(r.event_name) && r.scenario) set.add(r.scenario);
    }
    return [...set].sort();
  }, [catalog, events]);

  const matchingCount = useMemo(() => {
    const rows = catalog?.rows ?? [];
    return rows.filter((r) => {
      if (events.length && !events.includes(r.event_name)) return false;
      if (scenarios.length && !scenarios.includes(r.scenario)) return false;
      return true;
    }).length;
  }, [catalog, events, scenarios]);

  const authTokenCount = useMemo(() => {
    const rows = catalog?.rows ?? [];
    return rows.filter((r) => {
      if (!r.has_auth_token) return false;
      if (events.length && !events.includes(r.event_name)) return false;
      if (scenarios.length && !scenarios.includes(r.scenario)) return false;
      return true;
    }).length;
  }, [catalog, events, scenarios]);

  async function onSubmit() {
    if (matchingCount === 0) {
      setError("No matching OK rows for this selection.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const res = await importGenerateFromUiScript({
        target,
        events: events.length ? events : undefined,
        scenarios: scenarios.length ? scenarios : undefined,
      });
      if (!res.job) throw new Error(res.error || "Import failed");
      onComplete(res.job);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose} role="presentation">
      <div
        className="modal-card generate-ui-script-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Generate from UI Script"
      >
        <div className="modal-head">
          <strong>Generate from UI Script</strong>
          <button type="button" className="link-btn" onClick={onClose}>
            close ✕
          </button>
        </div>

        <p className="muted small">
          Reads <code>datasource-latest.xlsx</code> directly (Web / App sheets). Pick a target,
          then event and scenario. Actor JWT comes from Excel <code>auth_token</code> when set;
          otherwise the project logged-in user.
        </p>

        <div className="ui-script-target-toggle" role="group" aria-label="Target">
          <button
            type="button"
            className={target === "web" ? "active" : ""}
            disabled={busy || loading}
            onClick={() => setTarget("web")}
          >
            Web
          </button>
          <button
            type="button"
            className={target === "app" ? "active" : ""}
            disabled={busy || loading}
            onClick={() => setTarget("app")}
          >
            App
          </button>
        </div>

        {loading ? (
          <p className="muted small">Loading catalog…</p>
        ) : catalog ? (
          <>
            <p className="muted small ui-script-path" title={catalog.path}>
              {catalog.sheet} · {catalog.count ?? 0} OK rows
            </p>
            <div className="ui-script-filters">
              <MultiSelect
                label="Events"
                options={eventOptions}
                selected={events}
                onChange={(vals) => {
                  setEvents(vals);
                  setScenarios((prev) => prev.filter((s) => {
                    if (!vals.length) return true;
                    const rows = catalog.rows ?? [];
                    return rows.some(
                      (r) => vals.includes(r.event_name) && r.scenario === s,
                    );
                  }));
                }}
              />
              <MultiSelect
                label="Scenarios"
                options={scenarioOptions}
                selected={scenarios}
                onChange={setScenarios}
              />
            </div>
            <p className="muted small">
              Will import <strong>{matchingCount}</strong> row{matchingCount === 1 ? "" : "s"}
              {authTokenCount > 0
                ? ` · ${authTokenCount} with Excel auth_token`
                : " · actor from logged-in user"}
              {events.length === 0 && scenarios.length === 0 ? " (all)" : ""}.
            </p>
          </>
        ) : null}

        {error && <p className="error small">{error}</p>}

        <div className="modal-actions">
          <button type="button" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            className="primary"
            disabled={busy || loading || matchingCount === 0}
            onClick={() => void onSubmit()}
          >
            {busy ? "Importing…" : "Import & verify"}
          </button>
        </div>
      </div>
    </div>
  );
}
