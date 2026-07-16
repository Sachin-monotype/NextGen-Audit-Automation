import { useEffect, useRef, useState } from "react";

type Props = {
  label: string;
  options: string[];
  selected: string[];
  onChange: (values: string[]) => void;
  searchable?: boolean;
};

export default function MultiSelect({
  label,
  options,
  selected,
  onChange,
  searchable = true,
}: Props) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
        setSearch("");
      }
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const filtered = searchable
    ? options.filter((opt) => opt.toLowerCase().includes(search.toLowerCase()))
    : options;

  function toggle(value: string) {
    if (selected.includes(value)) onChange(selected.filter((v) => v !== value));
    else onChange([...selected, value]);
  }

  const summary =
    selected.length === 0 ? "All" : selected.length === 1 ? selected[0] : `${selected.length} selected`;

  return (
    <div className="filter-field multiselect" ref={ref}>
      <span>{label}</span>
      <button type="button" className="multiselect-toggle" onClick={() => setOpen((o) => !o)}>
        <span className={selected.length ? "" : "muted"}>{summary}</span>
        <span className="chevron">{open ? "▴" : "▾"}</span>
      </button>
      {open && (
        <div className="multiselect-menu">
          {searchable && (
            <div className="multiselect-search">
              <input
                autoFocus
                placeholder={`Search ${label}…`}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
          )}
          <div className="multiselect-actions">
            {selected.length > 0 && (
              <button type="button" className="multiselect-clear" onClick={() => onChange([])}>
                Clear
              </button>
            )}
            <span className="muted">{filtered.length} shown</span>
          </div>
          <div className="multiselect-list">
            {filtered.length === 0 && <span className="muted multiselect-empty">No matches</span>}
            {filtered.map((opt) => (
              <label key={opt} className="multiselect-option">
                <input type="checkbox" checked={selected.includes(opt)} onChange={() => toggle(opt)} />
                <span>{opt}</span>
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
