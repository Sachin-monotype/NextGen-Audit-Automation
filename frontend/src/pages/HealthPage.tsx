import { useCallback, useEffect, useState } from "react";
import {
  fetchApiHealth,
  runApiProbe,
  runCustomProbe,
  type ApiProbe,
} from "../api";

const CATEGORY_LABEL: Record<string, string> = {
  infra: "Infrastructure",
  api: "Trigger APIs (GraphQL / Ingress)",
  source: "Enrichment source APIs",
};

const CATEGORY_ORDER = ["infra", "api", "source"];

function StateBadge({ state }: { state: ApiProbe["state"] }) {
  const text = state === "ok" ? "REACHABLE" : state === "blocked" ? "UNREACHABLE" : "ERROR";
  return <span className={`probe-badge ${state}`}>{text}</span>;
}

/** Build a runnable curl (or raw SQL) from the probe's editable request. */
function requestToCurlOrQuery(req: NonNullable<ApiProbe["request"]>): string {
  const method = (req.method || "GET").toUpperCase();
  if (method === "TCP" || method === "PING") return "";
  if (method === "SELECT") {
    return typeof req.body === "string" ? req.body : JSON.stringify(req.body ?? "", null, 2);
  }
  let urlStr = req.url || "";
  try {
    const url = new URL(urlStr);
    if (req.params && typeof req.params === "object") {
      for (const [k, v] of Object.entries(req.params)) {
        if (v != null && v !== "") url.searchParams.set(k, String(v));
      }
    }
    urlStr = url.toString();
  } catch {
    /* keep raw url */
  }
  const lines = [`curl -X ${method} '${urlStr}'`];
  const headers = req.headers || {};
  for (const [k, v] of Object.entries(headers)) {
    if (v == null || v === "") continue;
    lines.push(`  -H '${k}: ${String(v).replace(/'/g, "'\\''")}'`);
  }
  if (req.body != null && method !== "GET" && method !== "HEAD") {
    const body =
      typeof req.body === "string" ? req.body : JSON.stringify(req.body);
    lines.push(`  -d '${body.replace(/'/g, "'\\''")}'`);
  }
  return lines.join(" \\\n");
}

function ProbeCard({
  probe,
  onRetest,
  onCustom,
}: {
  probe: ApiProbe;
  onRetest: (id: string) => void;
  onCustom: (id: string, request: NonNullable<ApiProbe["request"]>) => Promise<ApiProbe>;
}) {
  const [busy, setBusy] = useState(false);
  const [showBody, setShowBody] = useState(false);
  const [showEditor, setShowEditor] = useState(false);
  const [reqText, setReqText] = useState("");
  const [jsonError, setJsonError] = useState("");
  const [customResult, setCustomResult] = useState<ApiProbe | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (probe.request) {
      setReqText(JSON.stringify(probe.request, null, 2));
      setJsonError("");
    }
  }, [probe.id, probe.request]);

  async function retest() {
    setBusy(true);
    setCustomResult(null);
    try {
      await onRetest(probe.id);
    } finally {
      setBusy(false);
    }
  }

  async function sendEdited() {
    let parsed: NonNullable<ApiProbe["request"]>;
    try {
      parsed = JSON.parse(reqText);
      setJsonError("");
    } catch (e) {
      setJsonError(String(e));
      return;
    }
    setBusy(true);
    try {
      const result = await onCustom(probe.id, parsed);
      setCustomResult(result);
    } catch (e) {
      setCustomResult({
        ...probe,
        state: "error",
        ok: false,
        detail: String(e),
      });
    } finally {
      setBusy(false);
    }
  }

  function copyCurlOrQuery() {
    let req = probe.request;
    if (showEditor && reqText) {
      try {
        req = JSON.parse(reqText);
      } catch {
        /* use probe.request */
      }
    }
    if (!req) return;
    const text = requestToCurlOrQuery(req);
    if (!text) return;
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  const display = customResult ?? probe;
  const editable = probe.request && probe.method !== "TCP" && probe.method !== "ping";
  const canCopy =
    !!probe.request &&
    probe.method !== "TCP" &&
    probe.method !== "ping";

  return (
    <div className={`probe-card ${display.state}`}>
      <div className="probe-head">
        <StateBadge state={display.state} />
        <strong>{probe.label}</strong>
        <span className="probe-metrics muted">
          {display.status_code != null && `HTTP ${display.status_code} · `}
          {display.latency_ms != null && `${display.latency_ms} ms`}
        </span>
        <button type="button" className="link-btn" disabled={busy} onClick={retest}>
          {busy ? "testing…" : "Test"}
        </button>
        {canCopy && (
          <button type="button" className="link-btn" onClick={copyCurlOrQuery}>
            {copied
              ? "Copied!"
              : probe.method === "SELECT"
                ? "Copy query"
                : "Copy curl"}
          </button>
        )}
      </div>
      {probe.why && <p className="probe-why">{probe.why}</p>}
      <div className="probe-endpoint mono">
        <span className="probe-method">{probe.method}</span> {probe.url}
      </div>
      {probe.sample && <div className="probe-sample mono">sample: {probe.sample}</div>}
      <div className="probe-detail">{display.detail}</div>
      {display.hint && <div className="probe-hint">→ {display.hint}</div>}

      {editable && (
        <div className="probe-editor-toggle">
          <button type="button" className="link-btn" onClick={() => setShowEditor((s) => !s)}>
            {showEditor ? "hide request editor" : "edit & send request"}
          </button>
        </div>
      )}
      {showEditor && editable && (
        <div className="probe-editor">
          <p className="muted small">
            Edit method / url / headers / params / body, then Send — useful to try a different
            customer, profile id, familyId, or GraphQL mutation.
          </p>
          <textarea
            className="probe-editor-text mono"
            value={reqText}
            spellCheck={false}
            onChange={(e) => {
              setReqText(e.target.value);
              try {
                JSON.parse(e.target.value);
                setJsonError("");
              } catch (err) {
                setJsonError(String(err));
              }
            }}
          />
          {jsonError && <p className="error small">Invalid JSON: {jsonError}</p>}
          <button type="button" className="primary" disabled={busy || !!jsonError} onClick={sendEdited}>
            {busy ? "Sending…" : "Send edited request"}
          </button>
        </div>
      )}

      {(display.response_snippet || customResult?.response_snippet) && (
        <div className="probe-body">
          <button type="button" className="link-btn" onClick={() => setShowBody((s) => !s)}>
            {showBody ? "hide response" : "show response"}
          </button>
          {showBody && <pre>{display.response_snippet}</pre>}
        </div>
      )}
    </div>
  );
}

export default function HealthPage() {
  const [probes, setProbes] = useState<ApiProbe[]>([]);
  const [loading, setLoading] = useState(false);
  const [checkedAt, setCheckedAt] = useState<string>("");

  const runAll = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetchApiHealth();
      setProbes(res.probes);
      setCheckedAt(res.checked_at);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    runAll();
  }, [runAll]);

  const retestOne = useCallback(async (id: string) => {
    const updated = await runApiProbe(id);
    setProbes((prev) => prev.map((p) => (p.id === id ? updated : p)));
  }, []);

  const sendCustom = useCallback(async (_id: string, request: NonNullable<ApiProbe["request"]>) => {
    return runCustomProbe(request);
  }, []);

  const blockedSources = probes.filter((p) => p.category === "source" && p.state === "blocked");
  const blockedInfra = probes.filter(
    (p) => p.category === "infra" && p.id === "rabbitmq" && p.state === "blocked",
  );
  const vpnNeeded = blockedSources.length > 0 || blockedInfra.length > 0;

  const grouped = CATEGORY_ORDER.map((cat) => ({
    cat,
    items: probes.filter((p) => p.category === cat),
  })).filter((g) => g.items.length > 0);

  return (
    <div className="page health-page">
      <div className="page-head">
        <div>
          <h2>API Health</h2>
          <p className="muted">
            Live reachability of every system this project talks to, with a short note on why
            each call matters. Expand <em>edit &amp; send request</em> to try different
            ids/payloads like Postman.
          </p>
        </div>
        <button type="button" className="primary" disabled={loading} onClick={runAll}>
          {loading ? "Testing…" : "Run all"}
        </button>
      </div>

      {vpnNeeded && (
        <div className="banner warn">
          <strong>Some systems are unreachable from this network.</strong> RabbitMQ and the
          enrichment source APIs sit behind the corporate network. <b>Connect to the corporate
          VPN</b> and press <em>Run all</em>.
        </div>
      )}

      {checkedAt && <p className="muted small">Last checked: {new Date(checkedAt).toLocaleString()}</p>}

      {grouped.map((g) => (
        <section key={g.cat} className="probe-group">
          <h3>{CATEGORY_LABEL[g.cat] ?? g.cat}</h3>
          <div className="probe-grid">
            {g.items.map((p) => (
              <ProbeCard key={p.id} probe={p} onRetest={retestOne} onCustom={sendCustom} />
            ))}
          </div>
        </section>
      ))}

      {!loading && probes.length === 0 && <p className="muted">No probes yet — press Run all.</p>}
    </div>
  );
}
