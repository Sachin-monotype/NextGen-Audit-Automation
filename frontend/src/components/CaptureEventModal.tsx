import { useCallback, useEffect, useRef, useState } from "react";
import {
  clearInterceptorEvents,
  fetchInterceptorEvents,
  fetchInterceptorPaused,
  fetchInterceptorStatus,
  getInterceptorExcelDownloadUrl,
  launchInterceptorApp,
  launchInterceptorChrome,
  startInterceptorCapture,
  stopInterceptorCapture,
  triggerInterceptorBatch,
  updateInterceptorFilters,
  controlInterceptorPaused,
  type InterceptorEvent,
  type InterceptorPausedRequest,
  type InterceptorStatus,
} from "../api";

type Props = {
  onClose: () => void;
  onNavigateToResults?: () => void;
  onNavigateToCompare?: () => void;
};

export default function CaptureEventModal({ onClose, onNavigateToResults, onNavigateToCompare }: Props) {
  const [port, setPort] = useState(9222);
  const [target, setTarget] = useState<"web" | "app">("web");
  const [status, setStatus] = useState<InterceptorStatus | null>(null);
  const [events, setEvents] = useState<InterceptorEvent[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const [launching, setLaunching] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [copiedCid, setCopiedCid] = useState<string | null>(null);

  // Settings
  const [autoCompare, setAutoCompare] = useState(true);
  const [batchSize, setBatchSize] = useState(10);
  const [ignoreGet, setIgnoreGet] = useState(true);
  const [ignoreQuery, setIgnoreQuery] = useState(false);
  const [interceptionMode, setInterceptionMode] = useState<"observe" | "pause">("observe");
  const [pausedRequests, setPausedRequests] = useState<InterceptorPausedRequest[]>([]);
  const [selectedPausedId, setSelectedPausedId] = useState<string | null>(null);
  const [editedPostData, setEditedPostData] = useState("");
  const [mockBody, setMockBody] = useState('{"data":{}}');
  const [mockStatus, setMockStatus] = useState(200);

  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refreshStatus = useCallback(async (checkPort?: number) => {
    try {
      const s = await fetchInterceptorStatus(checkPort ?? port);
      setStatus(s);
      if (s.target === "app" || s.target === "web") {
        setTarget(s.target);
      }
    } catch {
      // Ignore poll error
    }
  }, [port]);

  // One-shot sync of filter checkboxes when a capture session appears/disappears.
  const wasCapturingRef = useRef(false);
  useEffect(() => {
    const active = Boolean(status?.is_active);
    if (active && !wasCapturingRef.current && status) {
      if (typeof status.ignore_get === "boolean") setIgnoreGet(status.ignore_get);
      if (typeof status.ignore_query === "boolean") setIgnoreQuery(status.ignore_query);
      if (typeof status.auto_compare_enabled === "boolean") setAutoCompare(status.auto_compare_enabled);
      if (typeof status.batch_size === "number" && status.batch_size > 0) setBatchSize(status.batch_size);
    }
    wasCapturingRef.current = active;
  }, [status]);

  const refreshEvents = useCallback(async () => {
    try {
      const data = await fetchInterceptorEvents({ limit: 500 });
      setEvents(data.events || []);
    } catch {
      // Ignore error
    }
  }, []);

  const refreshPaused = useCallback(async () => {
    try {
      const data = await fetchInterceptorPaused();
      setPausedRequests(data.requests || []);
      if (selectedPausedId && !data.requests.some((request) => request.fetch_id === selectedPausedId)) {
        setSelectedPausedId(null);
      }
    } catch {
      // Ignore poll error
    }
  }, [selectedPausedId]);

  async function applyCaptureFilters(next: {
    ignore_get?: boolean;
    ignore_query?: boolean;
    auto_compare?: boolean;
    batch_size?: number;
  }) {
    try {
      const res = await updateInterceptorFilters(next);
      setIgnoreGet(res.ignore_get);
      setIgnoreQuery(res.ignore_query);
      setAutoCompare(res.auto_compare);
      setBatchSize(res.batch_size);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }
  // Poll loop while modal is open
  useEffect(() => {
    void refreshStatus();
    void refreshEvents();
    void refreshPaused();

    pollTimerRef.current = setInterval(() => {
      void refreshStatus();
      void refreshEvents();
      void refreshPaused();
    }, 1500);

    return () => {
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, [refreshStatus, refreshEvents, refreshPaused]);

  function selectPaused(request: InterceptorPausedRequest) {
    setSelectedPausedId(request.fetch_id);
    setEditedPostData(request.post_data || "");
  }

  async function controlPaused(action: "continue" | "abort" | "mock") {
    if (!selectedPausedId) return;
    setBusy(true);
    setError("");
    try {
      let responseBody: unknown;
      if (action === "mock") {
        responseBody = JSON.parse(mockBody);
      }
      await controlInterceptorPaused(selectedPausedId, {
        action,
        post_data: action === "continue" ? editedPostData : undefined,
        response_code: mockStatus,
        response_body: responseBody,
      });
      setSelectedPausedId(null);
      await refreshPaused();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleLaunchChrome() {
    setLaunching("chrome");
    setError("");
    try {
      const res = await launchInterceptorChrome(port);
      if (!res.ok) {
        setError(res.error || "Failed to launch Chrome");
      }
      await refreshStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLaunching(null);
    }
  }

  async function handleLaunchApp() {
    setLaunching("app");
    setError("");
    try {
      const res = await launchInterceptorApp(port);
      if (!res.ok) {
        setError(res.error || "Failed to launch NextGen App");
      }
      setTarget("app");
      await refreshStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLaunching(null);
    }
  }

  async function handleToggleCapture() {
    setBusy(true);
    setError("");
    try {
      if (status?.is_active) {
        await stopInterceptorCapture();
      } else {
        await startInterceptorCapture({
          port,
          target,
          auto_compare: autoCompare,
          batch_size: batchSize,
          ignore_get: ignoreGet,
          ignore_query: ignoreQuery,
        });
      }
      await refreshStatus();
      await refreshEvents();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleClear() {
    if (!confirm("Clear captured events and reset comparison history?")) return;
    setBusy(true);
    try {
      await clearInterceptorEvents();
      setSelectedIds(new Set());
      await refreshEvents();
      await refreshStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleTriggerBatch(selectedOnly = false) {
    setBusy(true);
    setError("");
    try {
      const ids = selectedOnly && selectedIds.size > 0 ? Array.from(selectedIds) : undefined;
      await triggerInterceptorBatch(ids);
      setSelectedIds(new Set());
      await refreshEvents();
      await refreshStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function handleCopyCid(cid: string) {
    if (!cid) return;
    void navigator.clipboard.writeText(cid);
    setCopiedCid(cid);
    setTimeout(() => setCopiedCid(null), 1500);
  }

  function toggleSelect(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleSelectAllVisible(visEvents: InterceptorEvent[]) {
    if (selectedIds.size >= visEvents.length && visEvents.length > 0) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(visEvents.map((e) => e.id)));
    }
  }

  const filteredEvents = events.filter((e) => {
    if (!search.trim()) return true;
    const q = search.toLowerCase();
    return (
      (e.operation_name || "").toLowerCase().includes(q) ||
      (e.scenario || "").toLowerCase().includes(q) ||
      (e.header_values?.correlation_id || "").toLowerCase().includes(q)
    );
  });

  const cdpConnected = Boolean(status?.cdp_ready);
  const isCapturing = Boolean(status?.is_active);

  return (
    <div
      className="modal-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="presentation"
    >
      <div
        className="modal-card wide interceptor-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Live Capture and Continuous Comparison"
      >
        <div className="modal-header">
          <div className="title-row">
            <h2 className="title">📡 Live Capture &amp; Continuous Background Comparison</h2>
            <div className="cdp-status-indicator">
              <span className={`pulse-dot ${cdpConnected ? "connected" : "disconnected"}`} />
              <span className="cdp-label">
                Port {port}: {cdpConnected ? "Connected" : "Not Reachable"}
              </span>
            </div>
          </div>
          <button type="button" className="close-btn" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>

        <div className="interceptor-modal-body">
          {error && <div className="error-banner">{error}</div>}

          {/* Section 1: Launch Chrome or Monotype Connect + App */}
          <div className="interceptor-launch-card">
            <div className="launch-header">
              <div className="launch-title">
                <strong>1. Open Browser or Desktop App in Remote Debugging Port</strong>
              </div>
              <div className="port-config-row">
                <label className="port-label" htmlFor="cdp-port-input">
                  Port:
                </label>
                <input
                  id="cdp-port-input"
                  type="number"
                  className="port-input"
                  value={port}
                  onChange={(e) => setPort(Number(e.target.value) || 9222)}
                  disabled={isCapturing}
                />
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => void refreshStatus()}
                  disabled={busy}
                >
                  Check
                </button>
              </div>
            </div>

            <div className="launcher-actions-row">
              <button
                type="button"
                className={`launch-btn ${target === "web" ? "active-target" : ""}`}
                disabled={launching !== null}
                onClick={() => void handleLaunchChrome()}
                title={`Runs: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=${port} --user-data-dir="/tmp/chrome_dev_profile"`}
              >
                {launching === "chrome" ? "Launching…" : `🚀 Open Chrome (Port ${port})`}
              </button>

              <button
                type="button"
                className={`launch-btn ${target === "app" ? "active-target" : ""}`}
                disabled={launching !== null}
                onClick={() => void handleLaunchApp()}
                title={`Runs: open -n -a "/Applications/Monotype Connect +/Monotype Connect +.app" --args --remote-debugging-port=${port}`}
              >
                {launching === "app" ? "Launching…" : `📱 Open Monotype Connect + (Port ${port})`}
              </button>

              <div className="target-toggle">
                <span className="small muted">Target:</span>
                <button
                  type="button"
                  className={`pill-toggle ${target === "web" ? "active" : ""}`}
                  onClick={() => setTarget("web")}
                  disabled={isCapturing}
                >
                  Web
                </button>
                <button
                  type="button"
                  className={`pill-toggle ${target === "app" ? "active" : ""}`}
                  onClick={() => setTarget("app")}
                  disabled={isCapturing}
                >
                  App
                </button>
              </div>
            </div>
          </div>

          {/* Section 2: Capture Controls & Auto-Compare Batching Config */}
          <div className="interceptor-controls-card">
            <div className="capture-main-row">
              <button
                type="button"
                className={`capture-btn ${isCapturing ? "stop-btn" : "start-btn"}`}
                disabled={busy}
                onClick={() => void handleToggleCapture()}
              >
                {isCapturing ? "⏹ Stop Live Capture" : "▶ Start Live Capture & Compare"}
              </button>

              <div className="auto-compare-config">
                <label className="checkbox-label" title="Automatically send batches of unique events to comparison machine in background">
                  <input
                    type="checkbox"
                    checked={autoCompare}
                    onChange={(e) => {
                      const checked = e.target.checked;
                      setAutoCompare(checked);
                      void applyCaptureFilters({ auto_compare: checked });
                    }}
                  />
                  <span>
                    Auto-Compare in Background (batches of{" "}
                    <select
                      value={batchSize}
                      onChange={(e) => {
                        const size = Number(e.target.value);
                        setBatchSize(size);
                        void applyCaptureFilters({ batch_size: size });
                      }}
                      className="batch-size-select"
                    >
                      <option value={5}>5</option>
                      <option value={10}>10</option>
                      <option value={15}>15</option>
                      <option value={20}>20</option>
                    </select>
                    )
                  </span>
                </label>

                <label className="checkbox-label muted small" title="Skip GraphQL read queries starting with Get (and similar reads)">
                  <input
                    type="checkbox"
                    checked={ignoreGet}
                    disabled={ignoreQuery}
                    onChange={(e) => {
                      const checked = e.target.checked;
                      setIgnoreGet(checked);
                      void applyCaptureFilters({ ignore_get: checked });
                    }}
                  />
                  <span>Ignore Get* queries{ignoreQuery ? " (included in Mutations only)" : ""}</span>
                </label>

                <label className="checkbox-label muted small" title="Keep GraphQL mutations only — skip all queries">
                  <input
                    type="checkbox"
                    checked={ignoreQuery}
                    onChange={(e) => {
                      const checked = e.target.checked;
                      setIgnoreQuery(checked);
                      // Mutations-only implies ignoring Get* reads.
                      if (checked) setIgnoreGet(true);
                      void applyCaptureFilters({
                        ignore_query: checked,
                        ignore_get: checked ? true : ignoreGet,
                      });
                    }}
                  />
                  <span>Mutations only</span>
                </label>
              </div>
            </div>

            <div className="intercept-mode-row">
              <span className="small muted">Traffic mode:</span>
              <button type="button" className={`pill-toggle ${interceptionMode === "observe" ? "active" : ""}`} disabled={isCapturing} onClick={() => setInterceptionMode("observe")}>Observe</button>
              <button type="button" className={`pill-toggle ${interceptionMode === "pause" ? "active" : ""}`} disabled={isCapturing} onClick={() => setInterceptionMode("pause")}>Pause matching requests</button>
              {interceptionMode === "pause" && <span className="small muted">Only matching GraphQL traffic is held.</span>}
            </div>

            <div className="dedup-rule-callout">
              <span className="info-icon">ℹ</span>
              <span>
                <strong>Deduplication Rule:</strong> Each unique <code>(event, scenario)</code> pair is compared{" "}
                <strong>only once</strong> automatically. Identical subsequent clicks are deduplicated so you can keep triggering without spamming repeat comparisons.
              </span>
            </div>

            {/* Metrics Ribbon */}
            <div className="metrics-ribbon">
              <div className="metric-box">
                <span className="metric-val">{events.length}</span>
                <span className="metric-lbl">Total Captured</span>
              </div>
              <div className="metric-box">
                <span className="metric-val highlight">{status?.queued_count ?? 0}</span>
                <span className="metric-lbl">Queued for Batch</span>
              </div>
              <div className="metric-box">
                <span className="metric-val">{status?.compared_count ?? 0}</span>
                <span className="metric-lbl">Unique Compared</span>
              </div>
              <div className="metric-box">
                <span className="metric-val pass">{status?.pass_count ?? 0}</span>
                <span className="metric-lbl">PASS</span>
              </div>
              <div className="metric-box">
                <span className="metric-val fail">{status?.fail_count ?? 0}</span>
                <span className="metric-lbl">FAIL</span>
              </div>
              {status?.is_comparing_batch && (
                <div className="comparing-spinner-badge">
                  <span className="spinner-icon">⏳</span>
                  <span>Comparing batch in background…</span>
                </div>
              )}
            </div>
          </div>

          {interceptionMode === "pause" && (
            <div className="interceptor-pause-section">
              <div className="section-heading-row">
                <div><strong>Paused requests</strong><span className="small muted"> {pausedRequests.length} waiting for a QA decision</span></div>
                <span className="small muted">Requests auto-continue after 120 seconds</span>
              </div>
              <div className="paused-request-list">
                {pausedRequests.length === 0 ? <span className="small muted">Trigger a matching operation to pause it here.</span> : pausedRequests.map((request) => (
                  <button type="button" key={request.fetch_id} className={`paused-request-row ${selectedPausedId === request.fetch_id ? "selected-row" : ""}`} onClick={() => selectPaused(request)}>
                    <span className="method-pill">{request.method}</span><strong>{request.operation_name || "unknown"}</strong><span className="scenario-badge">{request.target}</span><span className="small muted">{request.url}</span>
                  </button>
                ))}
              </div>
              {selectedPausedId && (
                <div className="paused-editor">
                  <label>Request body<textarea value={editedPostData} onChange={(event) => setEditedPostData(event.target.value)} rows={8} /></label>
                  <label>Mock JSON response<textarea value={mockBody} onChange={(event) => setMockBody(event.target.value)} rows={5} /></label>
                  <label className="mock-status-label">Mock status<input type="number" min={100} max={599} value={mockStatus} onChange={(event) => setMockStatus(Number(event.target.value) || 200)} /></label>
                  <div className="paused-actions">
                    <button type="button" className="primary small" disabled={busy} onClick={() => void controlPaused("continue")}>Continue edited request</button>
                    <button type="button" className="secondary small" disabled={busy} onClick={() => void controlPaused("mock")}>Return mock response</button>
                    <button type="button" className="outline small" disabled={busy} onClick={() => void controlPaused("abort")}>Abort</button>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Section 3: Captured Events Stream & Table */}
          <div className="interceptor-table-section">
            <div className="table-toolbar">
              <div className="search-wrap">
                <input
                  type="search"
                  placeholder="Filter operations or scenarios…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="search-input"
                />
                <span className="small muted">
                  Showing {filteredEvents.length} of {events.length} event(s)
                </span>
              </div>

              <div className="toolbar-actions">
                <button
                  type="button"
                  className="primary small"
                  disabled={busy || filteredEvents.length === 0}
                  onClick={() => void handleTriggerBatch(selectedIds.size > 0)}
                  title="Force immediate comparison on selected or pending events"
                >
                  ⚡ Compare {selectedIds.size > 0 ? `Selected (${selectedIds.size})` : "Pending Batch Now"}
                </button>

                <a
                  href={getInterceptorExcelDownloadUrl()}
                  className="button secondary small"
                  download="audit_results.xlsx"
                  title="Download captured events as Excel"
                >
                  📥 Export Excel
                </a>

                <button
                  type="button"
                  className="outline small"
                  disabled={busy || events.length === 0}
                  onClick={() => void handleClear()}
                >
                  🗑 Clear
                </button>

                {onNavigateToResults && (
                  <button
                    type="button"
                    className="secondary small"
                    onClick={() => {
                      onClose();
                      onNavigateToResults();
                    }}
                  >
                    View in Results →
                  </button>
                )}

                {onNavigateToCompare && (
                  <button
                    type="button"
                    className="secondary small"
                    onClick={() => {
                      onClose();
                      onNavigateToCompare();
                    }}
                  >
                    View in Compare →
                  </button>
                )}
              </div>
            </div>

            <div className="interceptor-table-scroll">
              <table className="interceptor-table">
                <thead>
                  <tr>
                    <th style={{ width: 36 }}>
                      <input
                        type="checkbox"
                        checked={filteredEvents.length > 0 && selectedIds.size >= filteredEvents.length}
                        onChange={() => toggleSelectAllVisible(filteredEvents)}
                        aria-label="Select all"
                      />
                    </th>
                    <th>Operation</th>
                    <th>Scenario</th>
                    <th>Target</th>
                    <th>Correlation ID</th>
                    <th>Auth Token</th>
                    <th>Count</th>
                    <th>Status</th>
                    <th>Compare State</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredEvents.length === 0 ? (
                    <tr>
                      <td colSpan={10} className="empty-cell">
                        {isCapturing
                          ? "Listening on CDP port 9222… Trigger mutations in Chrome or NextGen App to see them appear and auto-compare here!"
                          : "No events captured yet. Click 'Start Live Capture & Compare' above to begin."}
                      </td>
                    </tr>
                  ) : (
                    filteredEvents.map((evt) => {
                      const cid = evt.header_values?.correlation_id || "";
                      const hasAuth = Boolean(evt.header_values?.auth_token);
                      const isSelected = selectedIds.has(evt.id);

                      return (
                        <tr key={evt.id} className={isSelected ? "selected-row" : ""}>
                          <td>
                            <input
                              type="checkbox"
                              checked={isSelected}
                              onChange={() => toggleSelect(evt.id)}
                            />
                          </td>
                          <td className="op-cell">
                            <span className="method-pill">{evt.method}</span>
                            <strong>{evt.operation_name || "unknown"}</strong>
                          </td>
                          <td>
                            <span className="scenario-badge">{evt.scenario || "global"}</span>
                          </td>
                          <td>
                            <span className={`target-badge ${evt.target}`}>
                              {evt.target?.toUpperCase() || "WEB"}
                            </span>
                          </td>
                          <td className="cid-cell">
                            {cid ? (
                              <button
                                type="button"
                                className="copy-cid-btn"
                                onClick={() => handleCopyCid(cid)}
                                title={`Click to copy: ${cid}`}
                              >
                                <code>{cid.slice(0, 8)}…{cid.slice(-4)}</code>
                                <span className="copy-icon">
                                  {copiedCid === cid ? "✓" : "📋"}
                                </span>
                              </button>
                            ) : (
                              <span className="muted">—</span>
                            )}
                          </td>
                          <td>
                            {hasAuth ? (
                              <span className="token-pill present" title="JWT Token captured from request">
                                JWT ✓
                              </span>
                            ) : (
                              <span className="token-pill missing" title="No Authorization header">
                                None
                              </span>
                            )}
                          </td>
                          <td>
                            <span className="call-count-badge">×{evt.call_count || 1}</span>
                          </td>
                          <td>
                            <span
                              className={`http-status-pill ${
                                evt.status_code >= 200 && evt.status_code < 400 ? "ok" : "fail"
                              }`}
                            >
                              {evt.status_code || "OK"}
                            </span>
                          </td>
                          <td>
                            <span className={`compare-pill ${evt.compare_status}`}>
                              {evt.compare_status === "queued" && "Queued"}
                              {evt.compare_status === "comparing" && "Comparing…"}
                              {evt.compare_status === "compared_pass" && "PASS"}
                              {evt.compare_status === "compared_fail" && "FAIL"}
                              {evt.compare_status === "already_compared" && "Compared"}
                              {evt.compare_status === "skipped_no_cid" && "No CID"}
                              {evt.compare_status === "pending" && "Pending"}
                            </span>
                          </td>
                          <td>
                            <button
                              type="button"
                              className="recompare-btn"
                              onClick={() => void handleTriggerBatch(true)}
                              title="Force re-compare this specific event"
                            >
                              🔄
                            </button>
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <div className="modal-footer">
          <span className="small muted">
            CDP listening on port {port}. Traffic is captured directly from your local browser / app session.
          </span>
          <button type="button" className="secondary" onClick={onClose}>
            Done / Close
          </button>
        </div>
      </div>
    </div>
  );
}
