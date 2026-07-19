import { useMemo, useState } from "react";

export type VerifyInUiContext = {
  operation: string;
  touchpoint?: string;
  scenarioId?: string;
  correlationId?: string;
  /** Notification / UI CTA text from the product (e.g. toast message). */
  ctaText?: string;
  /** Extra free-form context for the downstream UI automation agent. */
  notes?: string;
  /** TestRail case id — placeholder until mapping is wired. */
  testCaseId?: string;
};

type Props = {
  context: VerifyInUiContext;
  onClose: () => void;
};

/**
 * Prompt box for "Verify in UI". Captures CTA + context + TestRail id for a
 * future UI-automation handoff. Connection/send is intentionally not wired yet.
 */
export default function VerifyInUiModal({ context, onClose }: Props) {
  const [cta, setCta] = useState(context.ctaText || defaultCta(context));
  const [notes, setNotes] = useState(context.notes || "");
  const [testCaseId, setTestCaseId] = useState(context.testCaseId || "TR-TBD");
  const [copied, setCopied] = useState(false);

  const prompt = useMemo(() => {
    const lines = [
      `# Verify in UI`,
      `operation: ${context.operation}`,
      context.touchpoint ? `touchpoint: ${context.touchpoint}` : "",
      context.scenarioId ? `scenario_id: ${context.scenarioId}` : "",
      context.correlationId ? `xCorrelationId: ${context.correlationId}` : "",
      `testcase_id: ${testCaseId}`,
      ``,
      `## Expected UI / notification CTA`,
      cta.trim() || "(none)",
      ``,
      `## Context`,
      notes.trim() ||
        `Confirm the UI reflects the successful ${context.operation} event` +
          (context.touchpoint ? ` via ${context.touchpoint}` : "") +
          `.`,
      ``,
      `## Instructions for UI automation`,
      `- Locate the notification / toast / banner matching the CTA above`,
      `- Assert visibility and copy`,
      `- Follow any CTA action if present`,
      `- After GraphQL/BFF calls, capture response header correlation-id (NOT x-correlation-id)`,
      `- Record pass/fail against TestRail ${testCaseId}`,
    ];
    return lines.filter((l) => l !== undefined).join("\n");
  }, [context, cta, notes, testCaseId]);

  async function copyPrompt() {
    try {
      await navigator.clipboard.writeText(prompt);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* ignore */
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose} role="presentation">
      <div className="modal-card verify-ui-modal" onClick={(e) => e.stopPropagation()} role="dialog">
        <div className="modal-head">
          <strong>Verify in UI</strong>
          <button type="button" className="link-btn" onClick={onClose}>
            close ✕
          </button>
        </div>
        <p className="muted small">
          Build the handoff prompt for UI automation. Send/connection comes later —
          copy the prompt for now. For UI-triggered events, pair raw/enrich with response
          header <code>correlation-id</code> (Cloudflare rewrites <code>x-correlation-id</code>).
        </p>
        <label className="filter-field">
          <span>TestRail testcase id</span>
          <input value={testCaseId} onChange={(e) => setTestCaseId(e.target.value)} />
        </label>
        <label className="filter-field">
          <span>Notification / CTA text (from UI)</span>
          <textarea rows={3} value={cta} onChange={(e) => setCta(e.target.value)} />
        </label>
        <label className="filter-field">
          <span>Extra context</span>
          <textarea rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} />
        </label>
        <label className="filter-field">
          <span>Prompt preview</span>
          <pre className="verify-ui-prompt">{prompt}</pre>
        </label>
        <div className="modal-actions">
          <button type="button" className="primary" onClick={copyPrompt}>
            {copied ? "Copied" : "Copy prompt"}
          </button>
          <button type="button" disabled title="UI automation send — coming later">
            Send to UI agent (soon)
          </button>
        </div>
      </div>
    </div>
  );
}

function defaultCta(ctx: VerifyInUiContext): string {
  const op = ctx.operation;
  const touch = ctx.touchpoint || "";
  if (op.toLowerCase().includes("activate")) {
    return `Font / family activated successfully${touch ? ` (${touch})` : ""}`;
  }
  if (op.toLowerCase().includes("notif") || op.toLowerCase().includes("invite")) {
    return `Notification shown for ${op}`;
  }
  return `${op} completed — verify toast / banner in NextGen UI`;
}
