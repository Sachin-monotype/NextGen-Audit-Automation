import { useEffect, useMemo, useState } from "react";
import { fetchEnrichedFields } from "../api";

type Props = {
  operations: string[];
  /** operation → selected field paths. Empty / missing op = validate all. */
  value: Record<string, string[]>;
  onChange: (next: Record<string, string[]>) => void;
};

/**
 * Compare attribute editor: load enriched paths per selected op, let user
 * remove attributes so only remaining paths are validated.
 */
export default function AttributeEditor({ operations, value, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const [activeOp, setActiveOp] = useState(operations[0] || "");
  const [catalog, setCatalog] = useState<Record<string, string[]>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!operations.length) return;
    if (!operations.includes(activeOp)) setActiveOp(operations[0]);
  }, [operations, activeOp]);

  async function loadOp(op: string) {
    if (!op || catalog[op]) return;
    setBusy(true);
    setError("");
    try {
      const res = await fetchEnrichedFields(op);
      const fields = res.fields || [];
      setCatalog((prev) => ({ ...prev, [op]: fields }));
      // Default: all selected when first loaded
      if (!value[op]) {
        onChange({ ...value, [op]: [...fields] });
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (open && activeOp) loadOp(activeOp);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, activeOp]);

  const fields = catalog[activeOp] || [];
  const selected = useMemo(
    () => new Set(value[activeOp] ?? fields),
    [value, activeOp, fields],
  );
  const removed = fields.length - selected.size;

  function removeOne(path: string) {
    const next = fields.filter((f) => selected.has(f) && f !== path);
    onChange({ ...value, [activeOp]: next });
  }

  function restoreAll() {
    onChange({ ...value, [activeOp]: [...fields] });
  }

  function clearOpFilter() {
    const next = { ...value };
    delete next[activeOp];
    onChange(next);
  }

  if (!operations.length) return null;

  return (
    <div className="attribute-editor">
      <button type="button" className="primary outline" onClick={() => setOpen(true)}>
        Edit attributes
        {Object.keys(value).length > 0 && (
          <span className="muted">
            {" "}
            · {Object.values(value).reduce((n, a) => n + a.length, 0)} selected
          </span>
        )}
      </button>
      {open && (
        <div className="modal-backdrop" onClick={() => setOpen(false)} role="presentation">
          <div
            className="modal-card attribute-editor-modal"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
          >
            <div className="modal-head">
              <strong>Select attributes to validate</strong>
              <button type="button" className="link-btn" onClick={() => setOpen(false)}>
                close ✕
              </button>
            </div>
            <p className="muted small">
              Delete attributes you do not want checked. Only remaining paths are
              validated (plus enrichment-scope checks). Leave untouched to validate all.
            </p>
            <div className="attribute-editor-ops">
              {operations.map((op) => (
                <button
                  key={op}
                  type="button"
                  className={op === activeOp ? "chip selected" : "chip"}
                  onClick={() => setActiveOp(op)}
                >
                  {op}
                  {value[op] ? ` (${value[op].length})` : ""}
                </button>
              ))}
            </div>
            {error && <p className="error">{error}</p>}
            {busy && <p className="muted">Loading fields…</p>}
            {!busy && fields.length === 0 && (
              <p className="warn small">
                No enriched sample for <code>{activeOp}</code> — generate first, or
                validate all fields by leaving this empty.
              </p>
            )}
            {fields.length > 0 && (
              <>
                <div className="attribute-editor-toolbar">
                  <span className="muted">
                    {selected.size} / {fields.length} kept
                    {removed > 0 ? ` · ${removed} removed` : ""}
                  </span>
                  <button type="button" className="link-btn" onClick={restoreAll}>
                    Restore all
                  </button>
                  <button type="button" className="link-btn" onClick={clearOpFilter}>
                    Validate all (clear filter)
                  </button>
                </div>
                <ul className="attribute-list">
                  {[...selected].map((path) => (
                    <li key={path}>
                      <code>{path}</code>
                      <button
                        type="button"
                        className="link-btn"
                        title="Remove from validation"
                        onClick={() => removeOne(path)}
                      >
                        delete
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            )}
            <div className="modal-actions">
              <button type="button" className="primary" onClick={() => setOpen(false)}>
                Done
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
